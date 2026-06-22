# cerase-search MCP

First-party web search for Cerase agents (TOOLS-2). Replaces the
external `kiso-search`. Routed via `cerase-litellm`.

## Tools

| Tool | LiteLLM alias | Use |
|---|---|---|
| `search(agent_id, query)` | `search` (standard) | everyday "find / look up / latest on" |
| `deepsearch(agent_id, query)` | `deepsearch` (advanced) | deeper multi-source research |

`agent_id` is bound by the gateway from the calling Agent's identity
(same pattern as `cerase-memory`'s `user_id`) and is injected into the
LiteLLM request as `metadata.cerase_agent_id` so `RecalcTenantUsage`
attributes the spend per-agent.

## Billing

A search costs **both**:
- the **token** charge at the tool-model multiplier (×3 search / ×15
  deepsearch) via the SpendLogs stream, and
- a **flat fee** `web_search_cr` (20 cr) via the gateway's `event_log`
  `mcp_call` row (`tool_name` ∈ `billable_search_tools`).

Source of truth: `control-plane/config/cerase.php → credit_pricing`.

## Env

| Var | Default | Meaning |
|---|---|---|
| `LITELLM_BASE_URL` | `http://cerase-litellm:4000` | LiteLLM proxy base |
| `LITELLM_MASTER_KEY` | — | provider key |
| `CERASE_SEARCH_ALIAS` | `search` | override search alias |
| `CERASE_DEEPSEARCH_ALIAS` | `deepsearch` | override deepsearch alias |

## Run shape

`mcp-proxy --port 3000 -- python /app/server.py` (FastMCP stdio →
HTTP/SSE), identical to the other `cerase-*` MCP images. GHCR image
`ghcr.io/cerase-ai/cerase-search-mcp:<tag>` built by
`.github/workflows/docker-publish.yml`.
