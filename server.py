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

import os
from typing import Any

from mcp.server.fastmcp import FastMCP

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
    return {"answer": answer or "", "model": alias}


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
        dict with `answer` (sourced text) and `model`.
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
        dict with `answer` (sourced text) and `model`.
    """
    return _run_search(_DEEPSEARCH_ALIAS, agent_id, query)


if __name__ == "__main__":
    mcp.run()
