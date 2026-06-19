# Backend Verification & Deployment-Readiness Plan

**Scope:** Prove the FastAPI gateway in [chat_api/](chat_api/) works end-to-end with the [graph_rag/](graph_rag/) pipeline, surface every bug that could break a deployment, and confirm the stack runs cleanly on localhost.

**Status legend:** ☐ not started · ◐ in progress · ☑ verified

---

## 1. System Under Test

```
HTTP client
   │  POST /chat  {session_id, message, screenshot_base64?}
   ▼
chat_api.main:app                    ← app factory + middleware (CORS, security headers, rate limit)
   │   build_router(service)
   ▼
chat_api.routes                      ← request validation → ChatResponse
   ▼
chat_api.service.ChatService         ← orchestration + guardrails L1/L2/L4/L5
   ├─ guardrails.get_pipeline()      ← input/retrieval/output guards
   ├─ chat_api.session.SessionStore  ← memory | redis history
   └─ graph_rag pipeline
        ├─ HybridRetriever.retrieve()      vector(Chroma) + BM25 + graph(Neo4j) → RRF → rerank
        │    └─ embeddings.get_embedder()  → Ollama /api/embeddings (bge-large)
        ├─ build_graph_rag_chain()         LCEL: retrieve → prompt → LLM → str
        │    └─ QueryContextualizer        follow-up rewrite (LLM)
        └─ llm.get_llm()                   → Tabby ML /v1 (OpenAI-compatible, streaming)
```

**External dependencies that must be live for a real (non-mocked) run:**

| Dependency | Endpoint (default) | Used by | Failure mode if down |
|---|---|---|---|
| Tabby ML (LLM) | `http://localhost:8080/v1` | chain, contextualizer, image path | answers fail / 500 |
| Ollama (embeddings) | `http://localhost:11434/api/embeddings` | vector retrieval, passage rerank | vector context empty → over-refusal |
| Neo4j (graph) | `bolt://localhost:7687` | graph retrieval | graph context degrades gracefully |
| ChromaDB (vectors) | `./chroma_db` (embedded) | vector + BM25 | empty store → over-refusal |

---

## 2. Pre-Flight Configuration Checklist

Run **before** any live test. Most "the bot refuses everything" incidents trace back here.

- ☐ `.env` exists (copy from [.env.example](.env.example)) and `TABBY_API_TOKEN` is set (non-empty — [get_llm](graph_rag/llm/tabby_client.py#L30) raises `ValueError` if blank).
- ☐ `chroma_db/` is populated (ingestion was run). Empty store ⇒ grounding gate refuses every question.
- ☐ Neo4j has nodes (`MATCH (n) RETURN count(n)` > 0) — optional but expected.
- ☐ **Embedding model parity (CRITICAL):** the embedder used now must match the one that built `chroma_db`. See Bug #3. Confirm `OLLAMA_EMBEDDING_MODEL` matches the ingest-time model and Ollama has it pulled (`ollama list`).
- ☐ `prompts/system_prompt.txt` present (chain falls back to a built-in default if missing).
- ☐ Tabby ML reachable: `curl http://localhost:8080/v1/models` returns 200.
- ☐ Ollama reachable: `curl http://localhost:11434/api/tags` lists the embedding model.

---

## 3. Test Strategy (layered, fast → slow)

### Layer A — Unit / contract tests (no external services, mocked)
Already present in [tests/test_chat_api.py](tests/test_chat_api.py). These validate config parsing, session store, service orchestration, and route wiring with mocked retriever/chain/LLM.

```powershell
# From repo root, venv active
python -m pytest tests/test_chat_api.py -v
```
**Pass criteria:** all green. This proves the HTTP contract and business logic independent of infra.

Extend with these **missing** cases (see Gaps in §6):
- ☐ `/chat` returns `refused=true` + `REFUSAL_NO_CONTEXT` when retriever yields no groundable `_hits`.
- ☐ Injection input (`"ignore previous instructions ..."`) ⇒ refused at L1, chain never invoked.
- ☐ Oversized request body rejected (body-size cap — see Bug #?).
- ☐ Malformed `session_id` handling (UUID validation is documented in [main.py](chat_api/main.py#L19) but **not implemented** — see Bug #8).

### Layer B — Pipeline integration (real graph_rag, mocked or live LLM)
```powershell
python -m pytest tests/test_retrieval.py tests/test_chain.py tests/test_chatbot.py -v
```
- ☐ `HybridRetriever.retrieve("INSAT-3D")` returns non-empty `vector_context` and a `_hits` list with scores.
- ☐ Each retriever degrades independently (kill Neo4j → graph_context = "(knowledge graph unavailable)", vector still works). This is the [graceful-degradation contract](graph_rag/retrieval/hybrid_retriever.py#L142).

### Layer C — End-to-end on localhost (everything live)
Boot the API and exercise it over HTTP (§4). This is the real deployment-readiness gate.

### Layer D — Full suite regression
```powershell
python -m pytest -q
```
Record failures; triage infra-dependent tests (Neo4j/Chroma/Tabby) separately from logic failures.

---

## 4. Localhost Bring-Up & Smoke Test

### 4.1 Start dependencies
```powershell
# Neo4j (or use docker-compose just for neo4j)
docker run -d --name mosdac_graph -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=none neo4j:5.18.0
# Tabby ML — started separately per its own runbook (port 8080)
# Ollama — ensure the embedding model is pulled:
ollama pull bge-large
```

### 4.2 Run the API (host, with reload)
```powershell
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```
Watch startup logs for:
- `ChatAPI booted: ...`
- `Rate limiter enabled: N per IP` **or** `slowapi not installed` (note: see Bug #1 — this log is misleading).

### 4.3 HTTP smoke checks
```powershell
# Health
curl http://localhost:8000/health
# Expect: {"status":"ok", ...}

# Widget config
curl http://localhost:8000/config

# Text chat (grounded question that exists in the corpus)
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" `
  -d '{"session_id":"11111111-1111-1111-1111-111111111111","message":"What is INSAT-3D?"}'
# Expect: answer non-empty, grounded=true, refused=false, citations[] populated

# Off-topic / no-context question
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" `
  -d '{"session_id":"11111111-1111-1111-1111-111111111111","message":"Who won the 2010 World Cup?"}'
# Expect: refused=true (grounding gate or scope gate)

# Multi-turn (follow-up contextualization)
#   Turn 1: "Tell me about Oceansat-2"   Turn 2: "What is its resolution?"
# Expect: turn 2 resolves "its" → Oceansat-2 via QueryContextualizer

# Clear session
curl -X DELETE http://localhost:8000/chat/11111111-1111-1111-1111-111111111111

# Interactive docs
start http://localhost:8000/docs
```

### 4.4 E2E acceptance matrix

| # | Scenario | Endpoint | Expected |
|---|---|---|---|
| 1 | Health | `GET /health` | 200, `status=ok` |
| 2 | Config | `GET /config` | 200, branding fields |
| 3 | Grounded Q | `POST /chat` | 200, `grounded=true`, citations present |
| 4 | No-context Q | `POST /chat` | 200, `refused=true` |
| 5 | Injection input | `POST /chat` | 200, `refused=true`, chain skipped |
| 6 | Follow-up turn | `POST /chat` ×2 | turn 2 answer references entity from turn 1 |
| 7 | Screenshot (if enabled) | `POST /chat` + base64 | 200, image-grounded answer |
| 8 | Invalid base64 screenshot | `POST /chat` | **400** |
| 9 | Oversized screenshot | `POST /chat` | **400** (`too large`) |
| 10 | Clear session | `DELETE /chat/{id}` | 200, history gone |
| 11 | CORS preflight | `OPTIONS /chat` from allowed origin | allow headers present |
| 12 | Internal error | force chain exception | **500**, generic message (no stack leak) |

---

## 5. Bugs & Issues Found (from code review)

Severity: **CRITICAL** = blocks deployment · **HIGH** = breaks in prod config · **MEDIUM** = correctness/security gap · **LOW** = polish.

### #1 — Rate limiting is a no-op (HIGH, security)
[`_setup_rate_limiter`](chat_api/main.py#L66) sets `app.state.limiter` and registers the `RateLimitExceeded` handler, but **never adds `SlowAPIMiddleware` and no route uses `@limiter.limit(...)`**. slowapi enforces `default_limits` only via the middleware or a per-route decorator. As written, **no request is ever rate-limited**, yet the log says `Rate limiter enabled`.
**Fix:** `from slowapi.middleware import SlowAPIMiddleware; app.add_middleware(SlowAPIMiddleware)` (and/or decorate `/chat`). Re-test by exceeding `rate_limit_per_min` and expecting HTTP 429.

### #2 — No Ollama embedding service in docker-compose (CRITICAL, Docker only)
[docker-compose.yml](docker-compose.yml) defines only `neo4j` + `chat_api`. The embedder calls `OLLAMA_BASE_URL` which defaults to `http://localhost:11434`. Inside the `chat_api` container, `localhost` is the container itself → **connection refused on every embed** → empty vector context → over-refusal. `TABBY_BASE_URL` gets a `host.docker.internal` override in compose, but **`OLLAMA_BASE_URL` does not**.
**Fix:** add an Ollama service to compose, **or** override `OLLAMA_BASE_URL: http://host.docker.internal:11434` in the `chat_api` environment block. Verify inside the container: `curl http://host.docker.internal:11434/api/tags`.

### #3 — Embedding backend drift: query-time vs ingest-time mismatch risk (CRITICAL, correctness)
Live code [embeddings/__init__.py](graph_rag/embeddings/__init__.py) imports the **Ollama bge-large** embedder (1024-dim, `/api/embeddings`). But recent history ("Switch embeddings to Nomic Embed Text on Tabby ML") and [requirement.txt](requirement.txt#L13) comments reference **Nomic via Tabby `/v1/embeddings`**, and `nomic_embedder.py` was deleted (git status). If `chroma_db` was built with one embedder and queries use another, **cosine similarity is meaningless → silently wrong/empty retrieval** (no error thrown).
**Action:** confirm which embedder populated `chroma_db`; ensure config, code, and the running Ollama/Tabby model all agree. Add a startup assertion that embedding dimensionality matches the Chroma collection.

### #4 — Image path bypasses output guardrails (MEDIUM, security)
[`_answer_with_image`](chat_api/service.py#L109) returns the raw LLM output and hardcodes `grounded=True, top_score=1.0`, skipping `pipeline.check_output()` (no leakage scrub, no PII-out, no citation verify, no toxicity gate) that the text path runs. A vision answer can emit ungrounded or unsafe content unchecked.
**Fix:** route image answers through `check_output()` too.

### #5 — Double retrieval per text turn (MEDIUM, performance/latency)
[`_answer_text_only`](chat_api/service.py#L137) retrieves once for the grounding gate, then `chain.invoke` **retrieves again** internally — and re-runs the query contextualizer both times. Each text turn pays ~2× embedding + Neo4j + (possibly) an LLM contextualization call. Noted as "acceptable overhead" in-code, but it doubles tail latency and external load.
**Fix:** pass the already-retrieved context into the chain, or have the chain accept precomputed context.

### #6 — `models_cache` mount + compose header are stale (LOW)
[docker-compose.yml](docker-compose.yml#L68) mounts `./models_cache:ro` and the header claims "BAAI/bge-large from ./models_cache", but the embedder is **HTTP-based** (no in-process weights). The mount is unused and the comment misleads operators about where embeddings come from.

### #7 — CORS default origins omit ports (LOW, dev gotcha)
Default `CHAT_API_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1` won't match a browser app served from `http://localhost:3000` (the browser sends the port in `Origin`). Fine when prod sets the real origin; document it so localhost widget testing isn't blocked by silent CORS failures.

### #8 — UUID session-id validation documented but not implemented (LOW)
[main.py](chat_api/main.py#L19) docstring claims "UUID session-id validation," but `session_id` is a plain `str` in [models.py](chat_api/models.py#L10) with no validator, and routes accept any string. Either implement the validator or correct the docstring. (Low risk: keys are namespaced, but unbounded distinct IDs can grow the in-memory store.)

### #9 — `get_llm` is `lru_cache`d; per-call temperature/max_tokens ignored (LOW)
[get_llm](graph_rag/llm/tabby_client.py#L24) caches a single instance, so later `temperature`/`max_tokens` args are silently ignored. Image path and chain share one LLM config. Harmless today; surprising later.

### #10 — Screenshot base64 validated on first 256 chars only (LOW)
[`_validate_screenshot`](chat_api/service.py#L104) decodes `screenshot_b64[:256]`. A payload with a valid head but corrupt tail passes validation and fails later inside the LLM data URL. Validate the full string (or rely on size cap + full decode).

---

## 6. Test Coverage Gaps to Close

- ☐ No test asserts the **no-context refusal** path (`REFUSAL_NO_CONTEXT`, `refused=true`).
- ☐ No test for **injection/off-topic** refusal through the real guardrail pipeline (only mocked happy paths).
- ☐ No **rate-limit** test (and currently nothing to test — Bug #1).
- ☐ No test for the **image/screenshot** answer path beyond size/validation.
- ☐ No **request body-size cap** test (cap is claimed in [main.py](chat_api/main.py#L19) docstring — confirm it exists).
- ☐ No **Docker integration** smoke test (compose up → curl /health) — would have caught Bug #2.

---

## 7. Deployment-Readiness Checklist

**Functional**
- ☐ Layer A unit tests green.
- ☐ §4 E2E matrix (rows 1–12) pass on localhost.
- ☐ Grounded question returns citations; off-topic refuses.

**Configuration / secrets**
- ☐ No hardcoded tokens; `TABBY_API_TOKEN` from `.env` only (verified — code raises if missing).
- ☐ `.env` not committed; `.env.example` current.
- ☐ Embedding model parity confirmed (Bug #3).

**Security**
- ☐ CORS origin allowlist set to real frontend origin(s), never `*`.
- ☐ Security headers present on every response (curl `-I`).
- ☐ Rate limiting actually enforced (Bug #1 fixed; 429 observed).
- ☐ 500 responses return generic message, no stack trace (verified in [routes.py](chat_api/routes.py#L56)).
- ☐ Image path runs output guardrails (Bug #4 fixed).

**Infra / Docker**
- ☐ `docker compose up --build` boots neo4j + chat_api; `/health` returns 200.
- ☐ Embeddings reachable from inside the container (Bug #2 fixed).
- ☐ `chroma_db`, `neo4j_data`, `prompts` volumes populated/mounted.
- ☐ Neo4j healthcheck passes before chat_api starts (already wired via `depends_on`).

**Operability**
- ☐ Logs show booted line + dependency status.
- ☐ Session backend chosen deliberately (memory = single replica; redis = multi-replica/persistent).
- ☐ Graceful degradation verified (kill Neo4j → still answers from vectors).

---

## 8. Acceptance Criteria (definition of "deployment-ready on localhost")

1. `python -m pytest tests/test_chat_api.py` → all pass.
2. `uvicorn chat_api.main:app` boots with no traceback and logs the booted line.
3. E2E matrix rows 1–4, 8–10, 12 pass against the live stack.
4. A known-corpus question returns a grounded, cited answer; an out-of-corpus question is refused.
5. CRITICAL bugs (#2, #3) resolved or explicitly N/A for the chosen run mode (host vs Docker).
6. HIGH bug (#1) resolved before any internet-facing deployment.

---

## 9. Execution Order (recommended)

1. §2 pre-flight config + §3 Layer A unit tests (fast feedback).
2. §5 Bug #3 (embedding parity) — gates whether retrieval is even meaningful.
3. §4 localhost bring-up + smoke (rows 1–4).
4. §5 Bug #1, #4 fixes + retest.
5. Docker path: §5 Bug #2 fix → `docker compose up` → §4 matrix.
6. §6 add missing tests → §7 checklist → §8 sign-off.
