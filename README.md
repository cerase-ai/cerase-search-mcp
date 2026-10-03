# cerase-search-mcp

An MCP server that answers a web-search query with a short, sourced answer. It
does not crawl anything itself: it sends the query to a search-capable model
through an OpenAI-compatible LiteLLM proxy and returns the model's answer with
the URLs behind its inline `[n]` citations.

## Tools

| Tool | What it does | Arguments | Returns |
|---|---|---|---|
| `search` | Everyday lookup of current information, through the model alias `search`. | `agent_id`, `query` | `{answer, model, sources}` |
| `deepsearch` | Multi-step research with broader source coverage, through the model alias `deepsearch`. Costs more than `search`. | `agent_id`, `query` | `{answer, model, sources}` |

`sources` is a list of `{index, url, title}`, where `index` matches the `[n]`
marker in `answer`. It is read from the model response in one of three shapes
(`message.annotations[].url_citation`, a top-level `citations` list, or a
top-level `search_results` list) and is empty when the model returned none;
the server never invents a source.

`agent_id` must not be empty. Inside Cerase the gateway fills it with the
calling assistant's id, and the server forwards it to LiteLLM as request
metadata (`metadata.cerase_agent_id`) so the spend is attributed to that
assistant.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `LITELLM_BASE_URL` | `http://cerase-litellm:4000` | Base URL of the OpenAI-compatible proxy; requests go to `<base>/v1/chat/completions`. The default is the LiteLLM service name inside a Cerase appliance. |
| `LITELLM_MASTER_KEY` | empty | API key sent to the proxy. |
| `CERASE_SEARCH_ALIAS` | `search` | Model name `search` calls. |
| `CERASE_DEEPSEARCH_ALIAS` | `deepsearch` | Model name `deepsearch` calls. |

## Installation

The connector is published in the Cerase Marketplace as
`studio.guidance/cerase-search`
([marketplace page](https://marketplace.cerase.ai/en/p/studio.guidance/cerase-search)).
Every Cerase appliance installs it at boot, so its assistants have it without
an install step.

The image `ghcr.io/cerase-ai/cerase-search-mcp` is built and published from
the copy of these files kept in the Cerase appliance repository, which is
private. This repository carries the same files byte for byte, so a change
made only here does not reach the image.

## Build and run locally

```sh
docker build -t cerase-search-mcp .
docker run --rm -p 3000:3000 \
  -e LITELLM_BASE_URL=https://<your-openai-compatible-proxy> \
  -e LITELLM_MASTER_KEY=<your-key> \
  cerase-search-mcp
```

`server.py` speaks MCP over stdio; the image runs it behind `mcp-proxy`, which
serves Streamable HTTP at `http://localhost:3000/mcp` and SSE at
`http://localhost:3000/sse`. The proxy must serve models named `search` and
`deepsearch` (or the names set in the two alias variables), and they must be
models that search the web, since the server sends a plain chat completion.

The image's `HEALTHCHECK` runs `scripts/healthcheck.py`, an MCP client that
completes the handshake and lists the tools over `/mcp`. Its
`CERASE_HEALTHCHECK_*` variables exist to point it at a stub in tests.

## License

MIT. See [LICENSE](LICENSE).
