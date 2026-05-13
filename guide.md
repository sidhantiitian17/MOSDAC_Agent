# MOSDAC Agent — Implementation & Testing Guide

This guide walks through the `mosdac_agent/` package that implements
`enhanceToolCall.md`, how it integrates with the existing Graph-RAG chatbot
under `chat_api/`, and **exactly how to verify each piece end-to-end** on
your own machine.

## TL;DR

```bash
# 1. Install
pip install -r requirement.txt

# 2. Verify the pure-Python tool layer (no Ollama, no MCP, no network)
pytest tests/test_mosdac_tools.py -v
pytest tests/test_mosdac_mock_server.py -v
pytest tests/test_mosdac_integration.py -v

# 3. (Optional) Verify the agent layer with a fake LLM (no Ollama needed)
pytest tests/test_mosdac_agent.py -v

# 4. Run the full app
#    Terminal A — start the fake MOSDAC backend (only if you want HTTP)
python -m mosdac_agent.mock_mosdac

#    Terminal B — start the chat API (mounts /mosdac when env enabled)
$env:MOSDAC_ENABLE_MOSDAC_ENDPOINT="true"
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload

#    Terminal C — run the Streamlit UI
$env:CHAT_API="http://localhost:8000/mosdac/chat"
streamlit run mosdac_agent/streamlit_app.py
```

---

## 1. Architecture, in one picture

```
                   +--------------------------------------------+
HTML widget /      |              chat_api FastAPI               |
Streamlit UI ----->|  +- /chat ----- ChatService (existing RAG)  |
                   |  +- /mosdac/*  -MosdacAgentService (NEW)    |
                   +----------------+---------------------------+
                                    |
                                    v
                          AgentRunner -- LangGraph ReAct loop
                                    |
                                    v
                          4 LangChain tools (in-process)
                                    |
                                    v
                       MosdacClient -- MockMosdacClient (offline)
                                   \-- HttpMosdacClient  (real MOSDAC)

   Same package can ALSO publish the tools through MCP:
       python -m mosdac_agent.mcp_server   ->   MCP / streamable-http
```

Key invariants:

* The graph-RAG chatbot keeps its `/chat` endpoint unchanged.
* The MOSDAC agent lives at `/mosdac/*`, mounted only when
  `MOSDAC_ENABLE_MOSDAC_ENDPOINT=true`.
* Both endpoints share the **same** session store, so widgets that switch
  between them don't lose conversation context.

## 2. Package layout

```
mosdac_agent/
|-- __init__.py          # lazy public facade (build_agent, build_mcp_server, ...)
|-- config.py            # MosdacSettings - every value env-overridable
|-- exceptions.py        # MosdacError, ValidationError, RateLimitError, ...
|-- catalog.py           # Built-in INSAT catalogue + Indian-state bbox lookup
|-- store.py             # Store protocol + SqliteStore + InMemoryStore
|-- client.py            # MosdacClient protocol + Http + Mock implementations
|-- tools.py             # ToolContext + 4 *_impl() funcs + build_local_tools()
|-- mcp_server.py        # FastMCP server (run via `python -m mosdac_agent.mcp_server`)
|-- agent.py             # build_agent + AgentRunner + MosdacAgentService
|-- mock_mosdac.py       # Fake MOSDAC FastAPI app (port 9000)
|-- routes.py            # build_mosdac_router(service) -> /mosdac/health, /chat, ...
|-- streamlit_app.py     # Quick chat UI prototype
+-- widget/
    |-- widget.html      # Embeddable iframe target
    |-- widget.css
    +-- widget.js
```

### How modularity holds up on a different domain

Everything is env-driven via `MosdacSettings`. Re-deploying to a sandbox or
sibling portal needs only `.env` changes — no code edits:

| Env var                              | Effect                                       |
| ------------------------------------ | -------------------------------------------- |
| `MOSDAC_BASE_URL`                    | Point at staging / mirror                    |
| `MOSDAC_USERNAME` / `..._PASSWORD`   | Different SSO account                        |
| `MOSDAC_USE_MOCK=true`               | Talk to `mock_mosdac.py` (no live calls)     |
| `MOSDAC_CATALOG_JSON_PATH`           | Override the built-in product catalogue      |
| `MOSDAC_REGIONS_JSON_PATH`           | Override the state-bbox lookup               |
| `AGENT_LLM_MODEL`                    | Swap `qwen2.5:32b` ↔ `qwen2.5:14b` ↔ OpenAI  |
| `AGENT_LLM_BASE_URL`                 | Point at a different Ollama / vLLM / OpenAI  |
| `AGENT_USE_LOCAL_TOOLS=false`        | Switch from in-process tools to MCP transport|
| `MOSDAC_ENABLE_MOSDAC_ENDPOINT=true` | Mount the agent under the existing FastAPI   |
| `MOSDAC_BOT_NAME`                    | Re-brand the assistant per deployment        |
| `MOSDAC_FINAL_SUCCESS_SENTENCE`      | Override the success line shown to users     |

## 3. Step-by-step verification

### 3.1 Unit tests — the pure tool layer

```bash
pytest tests/test_mosdac_tools.py -v
```

What this verifies (entirely in-process, no network):

* Catalogue resolution works for INSAT-3D variants.
* `place_order` rejects bad dates, oversized windows, missing AOI, unknown
  states, and unknown dataset IDs.
* Happy path returns an `order_id`, a resolved bounding box, an SFTP path,
  and records an audit row.
* Idempotency: same key → same `order_id`, second call returns
  `status: "duplicate"`.
* Per-hour rate limit fires after `MAX_ORDERS_PER_USER_PER_HOUR`.
* `list_my_orders` isolates per-user history.
* `SqliteStore` round-trips idempotency + audit rows.

### 3.2 Mock MOSDAC backend tests

```bash
pytest tests/test_mosdac_mock_server.py -v
```

This exercises `mosdac_agent.mock_mosdac:app` directly through FastAPI's
`TestClient`. Confirms the token endpoint, the order POST/GET, 404 for
unknown IDs, and idempotency-header replay.

### 3.3 Integration — both endpoints on the same FastAPI app

```bash
pytest tests/test_mosdac_integration.py -v
```

This is the "in sync with the chatbot pipeline" check. It:

1. Boots the existing `chat_api` FastAPI app with **mocked** RAG deps.
2. Mounts the MOSDAC router on the same app, using a deterministic
   `FakeRunner` so no Ollama / LangGraph reasoning is required.
3. Hits `/chat` and `/mosdac/chat` and verifies they answer
   independently while sharing the session store.
4. Confirms `DELETE /mosdac/chat/{session}` clears history.
5. Confirms input validation (empty session_id → 422).

### 3.4 Agent test — full LangGraph wiring, fake LLM

```bash
pytest tests/test_mosdac_agent.py -v
```

Builds a real LangGraph ReAct agent but plugs in
`FakeMessagesListChatModel` so the test runs without Ollama. Verifies:

* `build_agent(...)` returns a runnable.
* `AgentRunner.chat()` returns the final assistant string.
* `MosdacAgentService.chat()` writes user + assistant turns into the
  shared session store.

The test auto-skips if `langgraph` isn't installed.

### 3.5 Run-the-real-stack smoke test (manual)

#### 3.5.1 Pull Qwen via Ollama

```bash
ollama serve &
ollama pull qwen2.5:32b      # 24 GB GPU recommended
# or, on a 16 GB GPU:
ollama pull qwen2.5:14b
```

Set the model in your `.env`:

```
AGENT_LLM_BASE_URL=http://localhost:11434/v1
AGENT_LLM_MODEL=qwen2.5:32b
AGENT_LLM_API_KEY=ollama
```

#### 3.5.2 Start the fake MOSDAC backend (optional)

```bash
python -m mosdac_agent.mock_mosdac
# listens on http://localhost:9000
```

To make the agent talk to it (instead of the in-process MockMosdacClient),
set:

```
MOSDAC_USE_MOCK=false
MOSDAC_BASE_URL=http://localhost:9000
MOSDAC_USERNAME=dev
MOSDAC_PASSWORD=dev
```

#### 3.5.3 Start the chat API with the MOSDAC endpoint mounted

```bash
$env:MOSDAC_ENABLE_MOSDAC_ENDPOINT="true"
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

Probe it:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/mosdac/health
curl -X POST http://localhost:8000/mosdac/chat `
     -H "Content-Type: application/json" `
     -H "X-MOSDAC-User: dev" `
     -d '{"session_id":"demo","message":"Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP"}'
```

#### 3.5.4 Launch the Streamlit UI

```bash
$env:CHAT_API="http://localhost:8000/mosdac/chat"
$env:MOSDAC_USER="dev"
streamlit run mosdac_agent/streamlit_app.py
```

Type the canonical order in the chat input; the bot should reply with
"Order has been placed. Check your SFTP account." followed by the order ID.

#### 3.5.5 Run the MCP server alone (for MCP Inspector)

```bash
$env:MCP_TRANSPORT="streamable-http"
python -m mosdac_agent.mcp_server
```

Then in another terminal:

```bash
npx @modelcontextprotocol/inspector
```

Connect to `http://127.0.0.1:8765/mcp` and exercise the four tools
interactively.

### 3.6 Run the canonical scenario end-to-end through the agent

```bash
python - <<'PY'
from mosdac_agent.agent import AgentRunner, build_agent
from mosdac_agent.client import MockMosdacClient
from mosdac_agent.config import MosdacSettings
from mosdac_agent.store import InMemoryStore

agent = build_agent(
    settings=MosdacSettings(_env_file=None, mosdac_use_mock=True),
    store=InMemoryStore(),
    client=MockMosdacClient(),
)
runner = AgentRunner(agent=agent)
print(runner.chat(
    thread_id="canonical",
    message="Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP",
))
PY
```

This assumes a running Ollama with the configured model. Expect a single
final reply beginning with `Order has been placed. Check your SFTP account.`

## 4. Embedding the widget into another portal

Serve `mosdac_agent/widget/*` from any static host (the same FastAPI app
can serve them via `app.mount("/widget", StaticFiles(directory=...))`).
Then ask the portal webmaster to inject:

```html
<!-- Simple iframe (recommended, isolated) -->
<iframe src="https://your-host.example/widget/widget.html"
        style="position:fixed;bottom:20px;right:20px;width:360px;
               height:520px;border:1px solid #888;border-radius:10px;
               background:#fff;z-index:9999"
        title="MOSDAC Assistant"></iframe>
```

The widget calls `<API>/chat` and `<API>/config` — both honour
`window.MOSDAC_API`, so the same bundle can serve multiple deployments.

## 5. Deploying on alternate domains

The package was designed so a single Docker image can be re-targeted:

1. **Branding** — set `MOSDAC_BOT_NAME`, `MOSDAC_FINAL_SUCCESS_SENTENCE`,
   `MOSDAC_SFTP_BASE_URL` per domain.
2. **Backend** — keep `MOSDAC_USE_MOCK=true` for sandboxes, flip to
   `false` in prod with credentials provided through your secret manager.
3. **Catalogue / regions** — drop a JSON file beside the container and
   point `MOSDAC_CATALOG_JSON_PATH` / `MOSDAC_REGIONS_JSON_PATH` at it.
4. **LLM** — `AGENT_LLM_*` env vars cover Ollama, vLLM, OpenAI, Together,
   Fireworks, DashScope — any OpenAI-compatible endpoint works.
5. **Transport** — `AGENT_USE_LOCAL_TOOLS=true` for single-process,
   `false` if you'd rather run the MCP server on a separate node.
6. **Persistence** — replace `SqliteStore` with a Redis/Postgres
   implementation of the same `Store` Protocol and inject it via
   `build_agent(store=…)`.

## 6. Troubleshooting

| Symptom                                              | Likely cause / fix                                                                                                            |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `/mosdac/health` returns 404                         | `MOSDAC_ENABLE_MOSDAC_ENDPOINT` is not `true`, or the import failed — check log for "MOSDAC endpoint not mounted".            |
| `RuntimeError: langgraph is required …`              | `pip install langgraph` (already in `requirement.txt`).                                                                       |
| `RuntimeError: fastmcp is required …`                | `pip install fastmcp` — only needed if you run `mcp_server.py`.                                                               |
| Agent reply is empty / hangs                          | Ollama is not running, or the configured model isn't pulled. Verify with `ollama list`.                                       |
| Rate-limit hit during testing                          | Lower `MAX_ORDERS_PER_USER_PER_HOUR`, wait 1 h, or delete `data/idempotency.sqlite`.                                          |
| `MOSDAC login failed (4xx)`                          | DO NOT auto-retry — three failures lock the account for one hour per the manual. Re-check creds, retry manually.              |
| Existing `tests/test_chat_api.py` failures            | `MOSDAC_ENABLE_MOSDAC_ENDPOINT` defaults to `false` so this should not happen; if it does, set it explicitly to `false`.      |

## 7. What's already wired vs. what's left to operationalise

Implemented & tested in this PR:

* All four MCP tools (search, place_order, check_status, list_my_orders).
* SQLite idempotency + per-user-per-hour rate limit.
* In-process mock client AND standalone fake MOSDAC FastAPI app.
* LangGraph ReAct agent + thread-safe runner.
* FastAPI router mounted on the existing `chat_api` app.
* Streamlit UI + embeddable HTML/CSS/JS widget.
* MCP server entrypoint (`python -m mosdac_agent.mcp_server`).
* 35+ tests covering tools, store, mock server, integration, and agent.

Left for the deployment team:

* Replace the placeholder endpoint paths in `client.py::HttpMosdacClient`
  with the exact ones from your Order API PDF (search + order POST + GET).
* Confirm the Keycloak `client_id` and grant type with MOSDAC admins.
* Replace the dev `X-MOSDAC-User` header with real Keycloak userinfo
  validation in `routes.py::_resolve_user`.
* Ship the widget on the production HTTPS host and hand the iframe
  snippet to the portal webmaster.
