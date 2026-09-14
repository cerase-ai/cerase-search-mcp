#!/usr/bin/env python3
"""Cerase Search MCP — first-party web search.

TOOLS-2: a Cerase-owned rebuild of the former external kiso-search,
integrated natively so it (a) routes through cerase-litellm with the
right tier-independent tool-model alias, and (b) propagates the calling
Agent's id into LiteLLM request metadata so RecalcTenantUsage can
attribute the spend per-agent (the contract the recalc job reads from
`metadata.cerase_agent_id`).

Two tools, mapping 1:1 to the two billable search tool-models:

  - search(agent_id, query)      → LiteLLM alias `search`     (standard)
  - deepsearch(agent_id, query)  → LiteLLM alias `deepsearch` (advanced)

Billing (control-plane config/cerase.php):
  - token charge: ×3 (search) / ×15 (deepsearch) via the SpendLogs
    stream, keyed by the injected agent_id.
  - flat fee: web_search_cr (20 cr) per call via the gateway's
    event_log mcp_call row (tool_name = cerase-search.search /
    cerase-search.deepsearch ∈ billable_search_tools). Both apply.

The `agent_id` parameter is bound by the gateway from the calling
Agent's identity (same pattern as cerase-memory's user_id) — the LLM
never supplies it.

Env vars (set by docker-compose / mcp-runner):
  - LITELLM_BASE_URL   (e.g. http://cerase-litellm:4000)
  - LITELLM_MASTER_KEY (or virtual key)
  - CERASE_SEARCH_ALIAS / CERASE_DEEPSEARCH_ALIAS (override the alias)
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

# MCP stdio transport uses stdout as the JSON-RPC channel — any log on stdout
# corrupts the protocol. Own the stderr invariant explicitly (don't depend on
# the mcp SDK's default handler, which could change across versions) and quiet
# httpx so backend request URLs don't leak into the logs.
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = FastMCP("cerase-search")

_SEARCH_ALIAS = os.environ.get("CERASE_SEARCH_ALIAS", "search")
_DEEPSEARCH_ALIAS = os.environ.get("CERASE_DEEPSEARCH_ALIAS", "deepsearch")

_SYSTEM_PROMPT = (
    "You are a precise web-search assistant. Answer the user's query "
    "using current web sources. Be concise and factual; cite the "
    "sources you used inline."
)


def _client():
    """Lazily build an OpenAI-compatible client pointed at cerase-litellm.

    Lazy so `tools/list` discovery doesn't pay the import / connection
    cost. Mirrors cerase-memory's openai-provider usage.
    """
    from openai import OpenAI

    base = os.environ.get("LITELLM_BASE_URL", "http://cerase-litellm:4000").rstrip("/")
    return OpenAI(
        api_key=os.environ.get("LITELLM_MASTER_KEY", ""),
        base_url=base + "/v1",
    )


def _to_dict(obj: Any) -> Any:
    """Best-effort convert a pydantic response part (openai/litellm) to a plain
    dict; pass dicts through untouched."""
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _extract_sources(resp: Any) -> list[dict[str, Any]]:
    """Followable web sources behind the answer's inline [n] markers, index-aligned
    so sources[n-1]['index'] == n. Backend-shape-defensive: some search backends put
    them on choices[0].message.annotations[].url_citation; others expose a top-level
    `citations` (URL strings) or `search_results` (dicts). Returns [] when the backend
    gave none — never fabricated.
    """
    # 1) Annotations form: choices[0].message.annotations[].url_citation.
    try:
        msg = resp.choices[0].message if resp.choices else None
    except Exception:
        msg = None
    annotations = list(getattr(msg, "annotations", None) or []) if msg is not None else []
    if annotations:
        out: list[dict[str, Any]] = []
        for i, entry in enumerate(annotations):
            entry = _to_dict(entry)
            uc = (entry.get("url_citation") or {}) if isinstance(entry, dict) else {}
            if uc.get("url"):
                out.append({"index": i + 1, "url": uc["url"], "title": uc.get("title") or None})
        if out:
            return out

    extra = getattr(resp, "model_extra", None) or {}

    # 2) Top-level `citations` (URL strings or dicts).
    citations = getattr(resp, "citations", None) or extra.get("citations")
    if citations:
        out = []
        for i, c in enumerate(citations):
            if isinstance(c, str) and c:
                out.append({"index": i + 1, "url": c, "title": None})
            elif isinstance(c, dict) and c.get("url"):
                out.append({"index": i + 1, "url": c["url"], "title": c.get("title") or None})
        if out:
            return out

    # 3) Top-level `search_results` (list of dicts).
    results = getattr(resp, "search_results", None) or extra.get("search_results")
    if results:
        out = []
        for i, s in enumerate(results):
            s = _to_dict(s)
            if isinstance(s, dict) and s.get("url"):
                out.append({"index": i + 1, "url": s["url"], "title": s.get("title") or None})
        if out:
            return out

    return []


def _run_search(alias: str, agent_id: str, query: str) -> dict[str, Any]:
    if not agent_id:
        raise ValueError("agent_id is required (cannot be empty)")
    if not query or not query.strip():
        raise ValueError("query is required (cannot be empty)")

    resp = _client().chat.completions.create(
        model=alias,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        # Inject the calling Agent id into LiteLLM request metadata so
        # the SpendLogs row carries metadata.cerase_agent_id —
        # RecalcTenantUsage attributes the tool-model spend to this
        # agent. Same contract SlotWriter bakes for the main agent.
        extra_body={"metadata": {"cerase_agent_id": agent_id}},
    )
    answer = resp.choices[0].message.content if resp.choices else ""
    return {"answer": answer or "", "model": alias, "sources": _extract_sources(resp)}


@mcp.tool()
def search(agent_id: str, query: str) -> dict[str, Any]:
    """Search the live web and return a concise, sourced answer.

    Use for everyday "what's the latest on…", "find…", "look up…"
    requests where current information beyond the model's training is
    needed.

    Args:
        agent_id: Cerase Agent PK — bound by the gateway. Must NOT be
            empty.
        query: natural-language search query.

    Returns:
        dict with `answer` (sourced text), `model`, and `sources` — a list of
        {index, url, title} for the URLs behind the answer's inline [n] markers
        (empty if the backend returned none).
    """
    return _run_search(_SEARCH_ALIAS, agent_id, query)


@mcp.tool()
def deepsearch(agent_id: str, query: str) -> dict[str, Any]:
    """Run a deeper, multi-step web search (advanced search) for harder
    research questions that need broader source coverage.

    Costs more credits than `search` — prefer `search` unless the
    question genuinely needs deep research.

    Args:
        agent_id: Cerase Agent PK — bound by the gateway.
        query: natural-language research query.

    Returns:
        dict with `answer` (sourced text), `model`, and `sources` — a list of
        {index, url, title} for the URLs behind the answer's inline [n] markers
        (empty if the backend returned none).
    """
    return _run_search(_DEEPSEARCH_ALIAS, agent_id, query)


if __name__ == "__main__":
    mcp.run()
