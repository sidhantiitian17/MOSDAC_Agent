# `chat_api/` — The FastAPI Gateway

This package is the **HTTP front door** of the whole system. It exposes the chatbot as a
web API, enforces all the edge security (CORS, rate limiting, auth, headers), manages
sessions and per-user conversation history, and orchestrates a single chat turn through
the RAG core and the guardrails.

It is deliberately **decoupled from the RAG internals**: it talks to
[graph_rag/](../graph_rag/) and [guardrails/](../guardrails/) through small interfaces,
so the same gateway could serve any RAG backend.

> Parent context: [readme_main.md §7 (query pipeline)](../readme_main.md) and
> [§10 (API surface)](../readme_main.md). Config: [config.py](config.py) (`CHAT_API_*`).

---

## Architecture: the app-factory pattern

[main.py](main.py) `create_app()` **composes** the application from independently-
swappable parts, so tests can inject fakes and production can swap backends:

```
create_app()
  ├─ retriever  = HybridRetriever()              (graph_rag/retrieval)
  ├─ chain      = build_graph_rag_chain(...)     (graph_rag/chain)
  ├─ llm        = get_llm()                       (graph_rag/llm)
  ├─ sessions   = build_session_store()          (session.py)
  ├─ repo       = build_conversation_repository() (db/)
  ├─ service    = ChatService(...)               (service.py)   ← the brain
  └─ router     = build_router(service, limiter) (routes.py)    ← the endpoints
```

The HTTP request lifecycle (middleware order, outermost first):
`RequestIDMiddleware` → `SecurityHeadersMiddleware` → `BodySizeLimitMiddleware` →
`CORSMiddleware` → `SlowAPIMiddleware` (rate limit) → router.

---

## File-by-file

### [main.py](main.py) — the app factory & middleware
The composition root. Builds the FastAPI app, wires all middleware and security, mounts
the static widget assets, and self-hosts Swagger UI (offline-safe).
- **Key pieces:** `create_app()` (factory), `SecurityHeadersMiddleware` (OWASP headers +
  strict CSP, with a relaxed CSP only for `/docs`), `RequestIDMiddleware` (stamps an
  `X-Request-ID` into every log line for tracing), `BodySizeLimitMiddleware` (pure-ASGI
  413 cap, defeats chunked-body bypass), `_client_ip_key` (real-client-IP for rate
  limiting behind a trusted proxy), `_setup_rate_limiter` (slowapi, **fails closed** if
  missing unless `CHAT_API_REQUIRE_RATE_LIMIT=false`), `_make_lifespan` (warms the BM25
  index on boot; closes the Neo4j driver + conversation store on shutdown).
- **Depends on:** `chat_api.config`, `chat_api.db`, `chat_api.routes`, `chat_api.service`,
  `chat_api.session`; lazily `graph_rag.chain/llm/retrieval`, `guardrails.config`; the
  `static/` folder; `slowapi`.
- **Exposes:** `app` (the ASGI app run by `uvicorn chat_api.main:app`).

### [routes.py](routes.py) — the HTTP endpoints
Declares every route; depends **only** on the `ChatService` abstraction.
- **Endpoints:** `/health`, `/ready`, `/config`, `/me`, `/metrics`, `POST /chat`,
  `POST /chat/stream` (SSE), `/conversations*` (list/get/delete), `DELETE /chat/{session_id}`,
  `POST /reload`.
- **Auth helpers:** `_require_api_key` (constant-time compare of `X-API-Key`/Bearer vs
  `CHAT_API_API_KEY`), `_require_admin` (guards `/reload` + `/metrics` with
  `CHAT_API_ADMIN_TOKEN`). `build_router(service, limiter)` exempts health/readiness/
  metrics from the per-IP rate budget.
- **Depends on:** `chat_api.auth`, `chat_api.config`, `chat_api.db.repository`,
  `chat_api.models`, `chat_api.service`, `graph_rag.health`, `observability`.

### [service.py](service.py) — `ChatService` (the orchestrator / brain)
**The most important file in this package.** Pure business logic, no FastAPI imports, so
it is unit-testable and reusable by any transport. It runs one chat turn end-to-end and
is where the guardrail layers are invoked:
`L1 check_input` → contextualize → retrieve → `L2 check_retrieval_groundable` →
LLM (`chain.invoke`/`stream`) → `L4 check_output` → `L5 log_request`.
- **Entry points:** `chat()` (anonymous/ephemeral), `chat_authenticated()` (per-user,
  persisted, ownership-enforced), `chat_stream()` (SSE generator: `token` events then one
  authoritative `final` event post-L4). Plus `_answer_text_only`, `_answer_with_image`
  (multimodal, same L2 gate), `list/get/delete_conversation`, `reload()` (hot-reload BM25
  + caches after a re-ingest).
- **Helpers:** history-prefix builders, overflow→summary handling
  (`_remember_overflow`), lazy `_contextualizer`/`_summarizer`/`_titler`/`_answer_cache`.
- **Depends on:** `chat_api.config/session/answer_cache/titler/db`, `graph_rag.config`,
  `graph_rag.retrieval.query_contextualizer`, `graph_rag.chat.summarizer`,
  `graph_rag.llm.tabby_client` (`llm_slot` concurrency throttle), `guardrails` (pipeline,
  templates, audit), `observability`.
- **Used by:** `routes.py` (every chat endpoint) and `main.py` (composition).

### [models.py](models.py) — request/response schemas
Pydantic models that define and validate the API contract: `ChatRequest`,
`ChatResponse`, `CitationItem`, `ConversationOut`, `ConversationDetail`, `MessageOut`.
Enforces message length (`CHAT_API_MAX_MESSAGE_CHARS`) and screenshot fields.
> Note: this file starts with a UTF-8 BOM — keep the encoding if you edit it.

### [session.py](session.py) — pluggable session store
Short-term chat history for **anonymous** sessions. `SessionStore` interface with two
backends: `InMemorySessionStore` (TTL + LRU cap, dev/single-node) and
`RedisSessionStore` (persistent, multi-replica). `build_session_store()` picks one from
`CHAT_API_SESSION_BACKEND` and refuses memory when `REQUIRE_PERSISTENT_SESSIONS=true`.
- **Depends on:** `chat_api.config`, optional `redis`.

### [auth.py](auth.py) — Keycloak / OIDC authentication
Verifies Keycloak JWTs against the realm's **JWKS** public keys (RS256), with a
**config-driven claim adapter** so claim names (`sub`/`preferred_username`/`email`) come
from `.env` (`JWT_FIELD_*`) — never hardcoded.
- **Key pieces:** `NormalizedUser`, `decode_token`, `normalize_user_data`,
  `get_current_user` (required → 401), `get_optional_user` (anonymous-friendly),
  `_jwk_client`/`reset_jwk_cache` (JWKS caching).
- **Depends on:** `chat_api.config`, `PyJWT[crypto]` (lazy — only when auth enabled).
- **Used by:** `routes.py` (`Depends(...)` on protected endpoints).

### [titler.py](titler.py) — conversation titles
`ConversationTitler.make_title(question, answer)` asks the LLM for a short title for a
brand-new conversation, generated **off the request path** as a background task.
- **Depends on:** `graph_rag.llm.tabby_client` (`get_llm`, `llm_slot`).

### [answer_cache.py](answer_cache.py) — optional FAQ cache
`AnswerCache` (LRU + TTL) short-circuits repeated, already-**grounded** questions keyed on
`(query, history, corpus_version)`. `bump_corpus_version()` invalidates it on `/reload`.
Enabled by `CHAT_API_ENABLE_ANSWER_CACHE`.

### [config.py](config.py) — `ChatAPISettings`
All gateway settings (env prefix `CHAT_API_`): branding, CORS lists, sessions/TTL, auth &
JWKS, rate-limit trust, request limits, screenshots/VLM, metrics/admin token, conversation
store selection. Helper methods derive the JWKS URL and split issuer→(url, realm).

### [__init__.py](__init__.py)
Package marker; re-exports `create_app`/`app` and the core models for convenience.

### [db/](db/) — per-user conversation persistence
See **[db/README.md](db/README.md)**. Backend-agnostic conversation store (SQLite default,
PostgreSQL for multi-replica), selected by `CHAT_API_CONV_STORE`.

---

## How a request flows through this package

```
HTTP POST /chat ─► routes.chat()
                     │  (auth via auth.get_optional_user, API key via _require_api_key)
                     ▼
                  service.chat()  /  chat_authenticated()  /  chat_stream()
                     │  L1 guardrails.check_input
                     │  retrieve (graph_rag.retrieval)
                     │  L2 guardrails.check_retrieval_groundable
                     │  LLM (graph_rag.chain + llm, throttled by llm_slot)
                     │  L4 guardrails.check_output
                     │  L5 guardrails.audit.log_request + observability
                     ▼
                  session.py / db/  (store the PII-redacted turn)
                     ▼
                  ChatResponse  ─► JSON to the client
```

---

## Run it

```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```
Docs: `/docs` · Liveness: `/health` · Readiness: `/ready`. Full setup:
[install.md](../install.md).
