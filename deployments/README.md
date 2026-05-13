# Deployments — Per-Domain Configuration

The chatbot package is **domain-agnostic**. Every visual or behavioural
difference between portals is expressed in environment variables and a tiny
widget config block — no Python source changes are required to ship to a
new domain.

## Quick start for a new domain

1. **Copy** one of the templates in this folder:

   ```bash
   cp deployments/mosdac.env       .env
   # — or —
   cp deployments/generic.env      .env
   ```

2. **Fill in** the secrets at the top of `.env`
   (`NEO4J_PASSWORD`, optional `LONGCAT_API_KEY` / `NVIDIA_API_KEY`).

3. **Pick a widget integration** from `deployments/widget-snippets/`:
   - `mosdac.html`  — drop-in for MOSDAC portal
   - `generic.html` — minimal example with customisable branding

4. **Run**:

   ```bash
   docker compose up -d
   curl http://localhost:8000/health
   ```

## What you can change from `.env` alone

| Variable | Purpose |
|---|---|
| `CHAT_API_TITLE` | FastAPI app title (visible at `/docs`) and default widget title |
| `CHAT_API_BOT_NAME` | Display name used in greetings |
| `CHAT_API_ALLOWED_ORIGINS` | Comma-separated CORS allow-list |
| `CHAT_API_SESSION_BACKEND` | `memory` (default) or `redis` |
| `CHAT_API_REDIS_URL` | e.g. `redis://redis:6379/0` — only needed when backend=redis |
| `CHAT_API_MAX_HISTORY_TURNS` | Conversation turns retained per session (default 10) |
| `CHAT_API_ENABLE_SCREENSHOT` | `true`/`false` — hides camera button when false |
| `CHAT_API_MAX_SCREENSHOT_BYTES` | Reject screenshots larger than this many decoded bytes |
| `SYSTEM_PROMPT_PATH` | Point at any text file to swap LLM persona |
| `QWEN_API_BASE`, `QWEN_MODEL` | Swap local model without rebuilding the image |

## What you can change at runtime in the browser

Set `window.GRAPH_RAG_CHAT_CONFIG = { ... }` before loading the widget:

```html
<script>
  window.GRAPH_RAG_CHAT_CONFIG = {
    apiBase:       '/chatapi',
    title:         'Sandbox Assistant',
    greeting:      'Hi! Ask me anything about the sandbox.',
    accent:        '#28a745',
    elementPrefix: 'sandbox',
  };
</script>
<script src="/static/graph-rag-chat-widget.js"></script>
```

The widget also fetches `GET /chatapi/config` at boot so the page title and
screenshot toggle stay in sync with the backend even if the page omits them.

## Scaling beyond a single replica

- Set `CHAT_API_SESSION_BACKEND=redis` and `CHAT_API_REDIS_URL=redis://...`.
- Put NGINX (or any L7 LB) in front of N `chat_api` containers.
- Mount the shared `chroma_db/` volume read-only or replace it with a remote
  vector store (e.g. Qdrant) — only `graph_rag/vector_store/chroma_store.py`
  changes.
- Neo4j and Ollama can each be moved to a dedicated host by editing the
  `NEO4J_URI` and `QWEN_API_BASE` values — no code changes.
