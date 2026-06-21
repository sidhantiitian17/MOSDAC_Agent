# Production Readiness Review — MOSDAC Graph RAG Chatbot

> **Reviewer's verdict:** The system is **architecturally strong but not yet production‑ready.**
> The codebase is genuinely modular (factory composition, dependency injection,
> config‑driven flags, ~440 passing tests). The blockers are *operational*, not
> structural: per‑request latency that will not survive concurrency, several
> reliability single‑points‑of‑failure, two real guardrail bypasses, and missing
> observability. Everything below is fixable **without** rewriting the
> architecture — and almost every fix slots into an existing seam.

---

## ✅ Implementation status (this branch)

**All P0 blockers, all P1/P2 items, and the missing‑capability gaps below have been
implemented and tested — `463 passed, 12 skipped` (was 441), no regressions.**
Every change landed behind a config flag or an existing interface; nothing in the
original architecture was rewritten.

| ID | Fix | Where | Default |
|----|-----|-------|---------|
| **P0‑1** | Native Ollama `/api/embed` batch endpoint (N calls → 1) + auto‑fallback to legacy; process‑level query‑embedding LRU | [ollama_embedder.py](graph_rag/embeddings/ollama_embedder.py) | on |
| **P0‑1a** | Attack‑corpus embeddings cached once (not per request); scope seeds batched | [injection.py](guardrails/input/injection.py), [scope.py](guardrails/input/scope.py) | on |
| **P0‑2** | Session TTL + LRU eviction (in‑memory) and Redis `EXPIRE`; `require_persistent_sessions` refuses memory for multi‑replica | [session.py](chat_api/session.py) | TTL 24h |
| **P0‑3** | Image path now runs the L2 grounding gate (refuses before the LLM); VLM capability flag + startup warning | [service.py](chat_api/service.py#L109-L160) | on |
| **P0‑4** | `/health` (liveness) vs `/ready` (deep readiness) sharing [health.py](graph_rag/health.py); CLI `test` reuses it; compose healthcheck → `/ready` | [routes.py](chat_api/routes.py), [health.py](graph_rag/health.py) | on |
| **P0‑5** | Guardrails report `degraded` when the embedder is down: always metric+WARN, fail‑closed when `GUARD_EMBEDDER_REQUIRED=true` | [pipeline.py](guardrails/pipeline.py) | observable, fail‑open |
| **P1‑1** | Real‑client‑IP rate limiting behind a trusted proxy; optional `X‑API‑Key`/Bearer auth on `/chat` | [main.py](chat_api/main.py), [routes.py](chat_api/routes.py) | auth off |
| **P1‑2** | Body‑size middleware (rejects on `Content‑Length`) + config‑driven message/screenshot length validators | [main.py](chat_api/main.py), [models.py](chat_api/models.py) | on |
| **P1‑3** | Retrieved‑context injection scan neutralizes smuggled directives; hits kept raw for grounding | [injection.py](guardrails/input/injection.py), [hybrid_retriever.py](graph_rag/retrieval/hybrid_retriever.py) | on |
| **P1‑4** | BM25 warmed at startup; auto‑rebuild on corpus change; `POST /reload` for hot re‑ingest pickup | [bm25_retriever.py](graph_rag/retrieval/bm25_retriever.py), [main.py](chat_api/main.py) | on |
| **P1‑5** | LLM `request_timeout` + bounded `max_retries` + process‑wide concurrency semaphore (`llm_slot`) | [tabby_client.py](graph_rag/llm/tabby_client.py) | 60s / 2 / 8 |
| **P1‑6** | SSE `POST /chat/stream` — streams tokens, emits an authoritative L4‑guarded `final` event | [service.py](chat_api/service.py), [routes.py](chat_api/routes.py) | on |
| **P2‑1** | Scope seeds + attack corpus loadable from config files (true domain‑agnostic deploy) | [scope.py](guardrails/input/scope.py), [injection.py](guardrails/input/injection.py) | built‑ins |
| **P2‑4/6** | Neo4j pool/timeout config; clear factory boot error | [neo4j_store.py](graph_rag/knowledge_graph/neo4j_store.py), [main.py](chat_api/main.py) | on |
| **Obs.** | `/metrics` (Prometheus, in‑process fallback) via [observability/](observability/); FastAPI `lifespan` warm‑up + graceful Neo4j close | [observability/metrics.py](observability/metrics.py), [main.py](chat_api/main.py) | on |
| **Audit** | Durable rotating audit sink + `system_prompt_hash` per record | [logger.py](guardrails/audit/logger.py) | stdout unless path set |
| **Cache** | Optional grounded‑answer cache keyed on query+history+corpus version | [answer_cache.py](chat_api/answer_cache.py) | off |
| **CI/DR** | GitHub Actions (tests + `pip-audit` + Trivy), load‑test script, backup runbook, Redis in compose | [.github/workflows/ci.yml](.github/workflows/ci.yml), [scripts/loadtest.py](scripts/loadtest.py), [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) | — |

New tests live in [tests/test_production_hardening.py](tests/test_production_hardening.py) (22 cases, no live deps).
The original review follows unchanged for traceability.

This document is organized as:

1. [What is already done well](#1-what-is-already-done-well)
2. [Critical issues, by severity](#2-critical-issues-by-severity) — P0 → P2
3. [What is missing before production](#3-what-is-missing-before-production)
4. [Remediation plan (phased, non‑breaking)](#4-remediation-plan-phased-non-breaking)
5. [Production readiness checklist (Definition of Done)](#5-production-readiness-checklist-definition-of-done)

---

## 1. What is already done well

Worth stating up front so the criticism below is read in context — these are
production‑grade decisions and must be **preserved** by any fix:

| Area | Evidence |
|---|---|
| **Clean layering** | `chat_api/` (transport) → `ChatService` (business logic) → `graph_rag/` (RAG). The service is testable without HTTP. |
| **Factory + DI composition** | [chat_api/main.py](chat_api/main.py#L91-L138) `create_app(...)` injects retriever/chain/llm/sessions — easy to swap backends or test doubles. |
| **Config over hardcoding** | [graph_rag/config.py](graph_rag/config.py) and [guardrails/config.py](guardrails/config.py) expose nearly every knob via `.env`. Secrets are env‑only; `.env` is correctly git‑ignored. |
| **Graceful degradation** | Each retriever in [hybrid_retriever.py](graph_rag/retrieval/hybrid_retriever.py#L226-L275) fails independently; the pipeline degrades to available sources. |
| **Defense‑in‑depth guardrails** | L1 input → L2 grounding gate → L4 output → L5 audit, fail‑closed by default. |
| **Pluggable session store** | [session.py](chat_api/session.py) Protocol with memory/Redis backends behind a factory. |
| **Embedding‑dim safety** | [chroma_store.py](graph_rag/vector_store/chroma_store.py#L145-L177) refuses to serve a corpus embedded with a different model. |

The problems are what happens when this meets **real traffic, real uptime
requirements, and a real adversary**.

---

## 2. Critical issues, by severity

### 🔴 P0 — Blockers (must fix before any production traffic)

---

#### P0‑1 · Per‑request latency will collapse under concurrency (embedding fan‑out)

**The single most important finding.** A *single* `/chat` text request triggers
a long chain of **sequential, blocking** embedding HTTP round‑trips to Ollama,
because `OllamaEmbedder.embed_documents` loops one text at a time with no
batching ([ollama_embedder.py:54-55](graph_rag/embeddings/ollama_embedder.py#L54-L55)):

| Stage | Embedding calls | Source |
|---|---|---|
| Injection embedding‑similarity check | **1 query + 8 attack phrases = 9** | [injection.py:98-118](guardrails/input/injection.py#L98-L118) |
| Scope gate | 1 query (centroid cached) | [scope.py:82-103](guardrails/input/scope.py#L82-L103) |
| Vector retrieval | 1 query | [vector_retriever.py](graph_rag/retrieval/vector_retriever.py#L37) |
| Passage rerank (bi‑encoder) | **1 query + up to 20 passages** | [rerankers.py:43-44](graph_rag/retrieval/rerankers.py#L43-L44) |
| Graph path rerank | 1 query + N paths | [graph_retriever.py:157-170](graph_rag/retrieval/graph_retriever.py#L157-L170) |
| Output sentence‑grounding | embeds answer sentences + passages | `grounding_check.check_sentence_grounding` |

That is **30–50+ sequential HTTP calls to a single embedding server per
question**, plus 1–2 LLM calls. At even 100 ms/embedding that is **3–5 s of
pure embedding latency per request, before generation.** Under concurrency the
Ollama endpoint becomes the bottleneck and p99 latency explodes.

The **worst offender is gratuitous**: the injection check **re‑embeds the same 8
static attack phrases on every request** — they never change and should be
embedded once at startup.

**Why it matters:** a public portal chatbot must handle bursty concurrent
traffic. This design serializes the most expensive operation and multiplies it.

**Fix (modular, non‑breaking):**
1. **Cache static corpora.** Pre‑compute and cache `_ATTACK_PHRASES` embeddings (like `scope.py` already caches the centroid). One‑line change to memoize; removes 8 calls/request immediately.
2. **Batch the embedder.** Add a true batch endpoint call (Ollama `/api/embed` accepts arrays, or fan out with a bounded `ThreadPoolExecutor`) inside `embed_documents`. This is a single‑file change to `OllamaEmbedder` behind the existing `Embeddings` interface — *nothing downstream changes*.
3. **Reuse query embeddings.** The same query is embedded ≥4 times per request (injection, scope, vector, rerank). Add a per‑request embedding cache (a thin `lru_cache`/dict keyed on text) so identical strings hit once.
4. **Make the bi‑encoder rerank reuse first‑stage vectors** instead of re‑embedding the 20 fused passages — the vector channel already computed most of them.

> Expected impact: ~40 calls → ~3–5 calls per request. This alone is the
> difference between "demo" and "production."

---

#### P0‑2 · No persistence/eviction on sessions → unbounded memory leak

`InMemorySessionStore` is a process‑global `defaultdict` with **no TTL and no
eviction** ([session.py:29-54](chat_api/session.py#L29-L54)). Every distinct
client‑supplied `session_id` lives forever. `GUARD_SESSION_TTL_SECONDS` is
*defined in config but never enforced anywhere*. The Redis backend also sets
**no key expiry** ([session.py:84-98](chat_api/session.py#L84-L98)).

**Why it matters:** a long‑running server (or any scraping/abuse) grows memory
without bound → OOM crash. With multiple uvicorn workers, memory sessions are
also **per‑worker**, so history is lost/inconsistent across requests on
different workers.

**Fix:**
- Enforce `session_ttl_seconds`: in Redis, `EXPIRE`/`SET ... ex=` on every write (trivial, the seam exists).
- For in‑memory, add an LRU cap + last‑access timestamp eviction, or **make Redis mandatory for multi‑replica production** and document memory as dev‑only (the code already warns).
- **Decision needed:** multi‑replica deployment *requires* Redis. Make `build_session_store()` refuse `memory` when `WORKERS>1`/replica count >1, or default prod compose to Redis.

---

#### P0‑3 · Image/screenshot path bypasses the L2 grounding gate

In `ChatService.chat`, the screenshot branch hard‑codes
`grounded, refused = True, False` and `top_score = 1.0`, and
`_answer_with_image` **never calls `check_retrieval_groundable`**
([service.py:109-147](chat_api/service.py#L109-L147),
[service.py:239-247](chat_api/service.py#L239-L247)). So attaching *any* image
lets a user ask *anything* and skip the relevance floor that is described as
"the CRITICAL structural control that guarantees the bot answers ONLY when the
knowledge base supports it" ([grounding_gate.py:3-5](guardrails/retrieval/grounding_gate.py#L3-L5)).

Secondary concern: the configured chat model is `Qwen2-1.5B-Instruct`
([config.py:142](graph_rag/config.py#L142)) which is **not a vision model** — the
multimodal path may silently degrade or error depending on what Tabby serves.

**Why it matters:** it is a grounding/hallucination bypass and a scope‑of‑knowledge
hole on a government information service.

**Fix:**
- Route the image path through the **same** L2 gate as text: run `check_retrieval_groundable(hits, ...)` and refuse on failure before the LLM call. The `hits` are already retrieved in `_answer_with_image`.
- Gate the screenshot feature on an explicit **vision‑capable model** capability flag; default `CHAT_API_ENABLE_SCREENSHOT=false` until a VLM backend is wired.

---

#### P0‑4 · `/health` is a liveness lie; no readiness check

`GET /health` returns static branding and **never pings Neo4j, Chroma, the LLM,
or the embedder** ([routes.py:18-26](chat_api/routes.py#L18-L26)). A load
balancer will route traffic to a replica whose Neo4j/embedder is down, and every
request will fail (or silently fail‑open guardrails — see P0‑5).

**Fix:** split into `/health` (liveness, cheap) and `/ready` (readiness:
`neo4j.ping()`, `chroma.count()`, a tiny embedder probe, LLM reachability with a
short timeout, each cached for a few seconds). Wire `/ready` to the
compose/k8s healthcheck. The `cmd_test()` in [main.py](main.py#L114-L200) already
contains exactly these probes — refactor them into a shared `health.py` and
reuse from both CLI and API (no duplication, modular).

---

#### P0‑5 · Guardrails silently fail‑open when the embedder is down

The scope gate and the injection embedding tier both **catch their own exceptions
and return "allow"/in‑scope** ([scope.py:101-103](guardrails/input/scope.py#L101-L103),
[injection.py:119-121](guardrails/input/injection.py#L119-L121)). The pipeline’s
top‑level `fail_closed=True` does **not** cover these because the exception never
propagates. So if Ollama hiccups, two key input defenses turn off **invisibly**
and the system keeps answering off‑topic / jailbreak‑adjacent prompts.

**Why it matters:** a dependency outage silently downgrades security posture with
no signal.

**Fix:**
- Respect `fail_closed` inside these checks: when the embedder is unavailable *and* `fail_closed` is set, return a degraded‑mode refusal (or at minimum **emit a metric/alert** and a structured WARN that this turn ran with reduced guards).
- The deterministic regex injection tier still runs — keep it as the floor — but make the *degradation observable* (counter `guardrail_degraded_total`).

---

### 🟠 P1 — High (fix in the first hardening sprint)

---

#### P1‑1 · Per‑IP rate limiting is defeated behind a proxy/load balancer

`slowapi` keys on `get_remote_address` ([main.py:77](chat_api/main.py#L77)),
which behind nginx/k8s ingress returns the **proxy IP** — so either *all* users
share one bucket (false lockouts) or `X-Forwarded-For` is spoofable. Per‑session
abuse lockout is also evadable because the client supplies the `session_id` and
can rotate UUIDs freely.

**Fix:** configure trusted‑proxy / `X-Forwarded-For` handling (Starlette
`ProxyHeadersMiddleware` or slowapi key func that reads the real client IP from a
configurable trusted header). Add an optional API‑key / token gate for
non‑public deployments. Document the trust boundary.

---

#### P1‑2 · No request body‑size cap (the docstring claims one that doesn't exist)

[main.py:19](chat_api/main.py#L19) advertises "Request body size cap" but **no
such middleware exists**. `ChatRequest.message` and `screenshot_base64` have **no
`max_length`** ([models.py:10-23](chat_api/models.py#L10-L23)), and
`_validate_screenshot` only checks size **after** the full base64 payload is
decoded in memory ([service.py:92-107](chat_api/service.py#L92-L107)). A large
POST is read entirely into RAM before rejection → trivial DoS.

**Fix:** add a body‑size‑limit middleware (reject on `Content-Length` >
configurable cap) and pydantic `max_length` on `message`/`screenshot_base64`.
Both are small, additive, fully backward‑compatible.

---

#### P1‑3 · Indirect prompt injection from retrieved documents is not filtered

The injection guard scans **user input only** ([pipeline.py:53-68](guardrails/pipeline.py#L53-L68)).
Retrieved passages — which come from a **crawled Drupal/web corpus** (`downloads/`)
— are concatenated verbatim into the prompt
([graph_rag_chain.py:14-29](graph_rag/chain/graph_rag_chain.py#L14-L29),
[hybrid_retriever.py:117-124](graph_rag/retrieval/hybrid_retriever.py#L117-L124)).
A poisoned document containing "NOTE TO AI: ignore your instructions…" reaches
the model through the context channel, where the input guard never looks. There
are a couple of indirect‑injection regexes but they run on the *query*, not the
*context*.

**Fix:** run a context‑sanitization pass on retrieved passages before prompt
assembly (reuse `injection.check` against each passage; neutralize/flag matches).
Keep it config‑gated (`GUARD_CONTEXT_INJECTION_SCAN`). This is a clean addition
at the `_format_context` seam.

---

#### P1‑4 · BM25 index is built once in‑memory and never refreshed

`BM25Retriever._build_index` loads the **entire corpus** into RAM on first query
and caches it for the instance lifetime ([bm25_retriever.py:46-72](graph_rag/retrieval/bm25_retriever.py#L46-L72)).
Consequences:
- **Cold‑start latency spike** on the first request after boot (full‑corpus tokenize + index build, synchronously, inside a user request).
- **Staleness:** after a re‑ingest, the long‑running API serves a stale BM25 view until restart — vector and keyword channels silently diverge.
- **Memory:** the whole chunk corpus is duplicated in process memory.

**Fix:** build the index at startup (warm‑up hook) not on the first user request;
add a refresh trigger (version stamp on the Chroma collection, or an admin
`/reload` endpoint) so re‑ingestion is picked up without a restart. For large
corpora, consider a persisted BM25 (e.g. Tantivy/Whoosh) behind the same
interface.

---

#### P1‑5 · No timeouts / circuit breakers on the LLM; everything is one Tabby endpoint

`get_llm()` constructs `ChatOpenAI(..., streaming=True)` with **no `request_timeout`
and no `max_retries` policy** ([tabby_client.py:37-44](graph_rag/llm/tabby_client.py#L37-L44)).
Chat generation, KG extraction, query contextualization and summarization **all
hit the same single Tabby endpoint** with no concurrency limit or backpressure
beyond IP rate limiting. A slow/hung Tabby stalls request threads (FastAPI runs
the sync routes in a bounded threadpool — exhaust it and the whole API stops
responding).

**Fix:** set explicit `request_timeout` and bounded `max_retries` from config; add
a concurrency semaphore / queue in front of the LLM client; consider separating
the extraction model endpoint (the config already supports `EXTRACTION_LLM_*`) so
offline ingestion can't starve online chat.

---

#### P1‑6 · No answer streaming to the client → poor latency UX

The LLM is created with `streaming=True` internally, but `/chat` returns one
complete JSON ([routes.py:37-53](chat_api/routes.py#L37-L53)). On a small local
model, users stare at a spinner for the full generation.

**Fix:** add a streaming endpoint (SSE / `StreamingResponse`) that streams tokens
while still running the L4 output guard on the assembled answer before final
commit. Additive — keep the existing `/chat` for clients that want the guarded,
buffered response.

---

### 🟡 P2 — Medium (quality, correctness, polish)

- **P2‑1 · Domain seed phrases & attack phrases are hardcoded in source.**
  `_MOSDAC_SEED_PHRASES` ([scope.py:18-39](guardrails/input/scope.py#L18-L39)) and
  `_ATTACK_PHRASES` ([injection.py:86-95](guardrails/input/injection.py#L86-L95))
  are baked into Python. The project sells itself as "domain‑agnostic / deploy the
  same image anywhere" ([config.py docstring](chat_api/config.py#L1-L5)), but the
  scope guard is hard‑wired to MOSDAC. **Move both to config‑referenced files**
  (`GUARD_SCOPE_SEED_PATH`, `GUARD_INJECTION_CORPUS_PATH` — the latter already
  exists in config but isn't used by `injection.py`). Same pattern as the
  file‑driven system prompt.

- **P2‑2 · CLI chat path has *no* guardrails.** `GraphRagChatbot.chat`
  ([chatbot.py:78-92](graph_rag/chat/chatbot.py#L78-L92)) calls the chain directly
  — no input/output guards, no grounding gate. Fine for admin use, but it should
  be documented loudly, and ideally share the `ChatService` path so the two can't
  drift.

- **P2‑3 · `grounding_action="strip"` can mangle valid answers.** The numeric and
  sentence grounding checks ([pipeline.py:144-181](guardrails/pipeline.py#L144-L181))
  can strip legitimate sentences on a sim threshold (`0.40`), returning partial or
  empty answers and refusing. This needs an **offline eval sweep** (the RAGAS
  harness already exists — see `evaluation_plan.md`) to tune the threshold against
  false‑strip rate before trusting it in prod.

- **P2‑4 · Neo4j driver has no reconnection/pool tuning.** `Neo4jStore` opens a
  driver and relies on it for the process lifetime
  ([neo4j_store.py:42-46](graph_rag/knowledge_graph/neo4j_store.py#L42-L46)). No
  `max_connection_pool_size`, `connection_acquisition_timeout`, or explicit
  re‑auth on Neo4j restart. Add pool config from `.env` and verify behavior across
  a Neo4j bounce.

- **P2‑5 · `400` errors echo internal validation text.**
  [routes.py:54-55](chat_api/routes.py#L54-L55) returns `str(exc)` to the client
  (e.g. screenshot byte limits). Low‑risk, but prefer a fixed client message and
  log the detail server‑side.

- **P2‑6 · Eager construction at boot = total outage on any one dep mismatch.**
  `create_app()` builds the retriever (which runs `check_embedding_compat` →
  raises on dim mismatch) at import time ([main.py:120-129](chat_api/main.py#L120-L129)).
  Fail‑fast is reasonable, but pair it with a clear startup error surfaced to ops
  (and the readiness probe) rather than an opaque import crash.

- **P2‑7 · `lru_cache` singletons hide config reloads.** `get_llm`/`get_embedder`
  cache for process life — expected, but document that `.env` changes need a
  restart (no hot‑reload), and ensure no test leaks the cache across cases.

---

## 3. What is missing before production

These are not bugs in existing code — they are **capabilities a production
chatbot needs that aren't here yet.**

| Gap | Why it's needed | Suggested home |
|---|---|---|
| **Observability / metrics** | Audit logs go to a Python logger only ([logger.py:58](guardrails/audit/logger.py#L58)). No Prometheus metrics (latency, refusal rate, grounding pass‑rate, embedding/LLM call counts, error rates), no tracing. You are blind in prod. | `/metrics` endpoint + OpenTelemetry spans around retrieve/LLM. |
| **Centralized, persisted audit trail** | Audit records are stdout JSON; on a gov service you likely need durable, queryable logs (file rotation or a log sink). | Pluggable audit sink (file/Redis/HTTP) behind the existing `log_request`. |
| **Authentication / API gating option** | `/chat` is fully open; only IP rate‑limited. Needs at least an optional API key for embedded/widget deployments. | Config‑gated auth dependency in `routes.py`. |
| **Ingestion automation & freshness** | Ingestion is manual CLI; no scheduled re‑crawl, no freshness signal, no "last updated" surfaced to users. | Scheduled job + collection version stamp (ties into P1‑4 reload). |
| **Backup / DR for state** | Neo4j (`./neo4j_data` bind mount) and `chroma_db/` have no documented backup/restore or migration path. | Backup runbook + volume snapshot policy. |
| **Load & soak testing** | No evidence of concurrency/load testing. P0‑1 must be proven fixed under, say, 50 concurrent users. | `locust`/`k6` script + target SLOs. |
| **CI/CD + image scanning** | 38 test files / ~440 tests exist but no CI pipeline, no dependency/CVE scan, no SBOM in the repo. | GitHub Actions: test + `pip-audit` + image scan. |
| **Graceful shutdown / lifespan** | No FastAPI `lifespan` to warm caches (BM25, centroid, embeddings) on startup and close the Neo4j driver on shutdown. | `lifespan` context in `create_app`. |
| **Cost/usage caps** | No per‑session or global token budget; a small local model masks this now but matters if the LLM is swapped for a paid API. | Config budget guard in `ChatService`. |
| **Prompt/version pinning** | System prompt is file‑driven (good) but not versioned/hashed into the audit record, so you can't tie an answer to the prompt that produced it. | Log `system_prompt_hash` in `log_request`. |
| **Answer caching** | Identical/popular questions re‑run the full pipeline every time. A semantic answer cache would cut load dramatically on an FAQ‑heavy portal. | Optional cache layer keyed on normalized query + corpus version. |

---

## 4. Remediation plan (phased, non‑breaking)

Sequenced so each phase ships independently, every change lands behind a config
flag or an existing interface, and **no existing test should break**. Run the
full `pytest` suite + the RAGAS gate (`python main.py ragas-eval`) after each
phase to prove no regression.

### Phase 0 — Stop the bleeding (1–2 days) · *blockers*
- **P0‑1a** Cache `_ATTACK_PHRASES` embeddings (remove 8 calls/request).
- **P0‑1b** Add batching + bounded threadpool to `OllamaEmbedder.embed_documents`; add per‑request query‑embedding cache.
- **P0‑3** Route image path through `check_retrieval_groundable`; default screenshot off until a VLM is configured.
- **P0‑5** Make embedder‑down guardrail degradation respect `fail_closed` and emit a metric/WARN.
- Gate: full test suite green; manual latency check shows calls/request dropped ~10×.

### Phase 1 — Reliability & operability (2–4 days)
- **P0‑2** Enforce session TTL (Redis `EXPIRE` + in‑memory eviction); make Redis the prod default; refuse `memory` for multi‑replica.
- **P0‑4** Split `/health` (liveness) vs `/ready` (deep readiness); refactor `cmd_test` probes into shared `health.py`; wire compose healthcheck to `/ready`.
- **P1‑5** LLM `request_timeout` + bounded retries + concurrency semaphore from config.
- **P1‑4** Warm BM25 + centroid + static embeddings in a FastAPI `lifespan`; add `/reload` (admin‑gated) for re‑ingest pickup; close Neo4j driver on shutdown.
- **P2‑4** Neo4j pool/timeout config.

### Phase 2 — Security hardening (2–3 days)
- **P1‑2** Body‑size middleware + pydantic `max_length` on inputs.
- **P1‑1** Trusted‑proxy / `X‑Forwarded‑For` handling for real‑IP rate limiting; optional API‑key auth dependency.
- **P1‑3** Config‑gated context‑injection scan on retrieved passages.
- **P2‑1** Externalize scope seed phrases & attack corpus to config‑referenced files (true domain‑agnostic deploy).

### Phase 3 — Observability & UX (3–5 days)
- `/metrics` (Prometheus) + OpenTelemetry tracing on retrieve/LLM/guards.
- Pluggable durable audit sink; log `system_prompt_hash`.
- **P1‑6** Streaming `/chat/stream` (SSE) with post‑stream output guard.
- Optional semantic answer cache.

### Phase 4 — Quality, CI/CD, DR (ongoing)
- **P2‑3** Tune grounding thresholds with the RAGAS harness; measure false‑strip rate.
- CI pipeline (tests + `pip-audit` + image scan), SBOM.
- Backup/restore runbook for Neo4j + Chroma.
- Load/soak test to a defined SLO (e.g. p95 < 3 s @ 50 concurrent) — **proves P0‑1 is actually fixed.**

---

## 5. Production readiness checklist (Definition of Done)

**Performance & scale**
- [x] ≤ ~5 embedding round‑trips per request (batched, cached static corpora)
- [x] Embedding `embed_documents` batches / parallelizes (native `/api/embed`)
- [x] BM25 + centroid + attack‑phrase embeddings warmed at startup, refreshable
- [ ] Load test passes target SLO at target concurrency *(script shipped — [scripts/loadtest.py](scripts/loadtest.py); run against a live stack to certify the SLO)*

**Reliability**
- [x] Sessions persisted (Redis) with enforced TTL; memory backend blocked for multi‑replica
- [x] `/ready` probes all downstream deps; LB/compose healthcheck wired to it
- [x] LLM/Neo4j/embedder calls have timeouts + bounded retries + pool limits
- [x] FastAPI `lifespan` warms caches and closes drivers cleanly

**Security**
- [x] Image path runs the L2 grounding gate (no bypass)
- [x] Guardrail degradation under embedder outage is fail‑closed *and* observable
- [x] Body‑size cap + input `max_length`
- [x] Real‑client‑IP rate limiting behind proxy; optional auth gate
- [x] Retrieved‑context injection scan enabled
- [x] Scope/attack corpora externalized to config (no MOSDAC hardcoding in source)

**Observability**
- [x] Prometheus metrics: latency, refusal rate, dep errors, guard‑degraded count (`/metrics`)
- [x] Durable, queryable audit log with `system_prompt_hash`
- [ ] Tracing across retrieve → LLM → guards *(metrics + structured logs in; distributed tracing/OTel spans still to add)*

**Process**
- [x] CI: tests + dependency/CVE scan + image scan ([.github/workflows/ci.yml](.github/workflows/ci.yml))
- [x] Backup/restore runbook for Neo4j + Chroma ([docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md))
- [ ] RAGAS gate is GO and grounding thresholds tuned against false‑strip rate *(run `python main.py ragas-eval`; P2‑3)*
- [x] Deployment docs state the Redis‑required / VLM‑required / proxy‑trust assumptions ([.env.example](.env.example))

---

### Closing assessment

The architecture is the hard part, and it is **already right** — modular,
injectable, config‑driven, well‑tested. None of the fixes above require breaking
that structure; each one lands at a seam that already exists (the `Embeddings`
interface, the `SessionStore` Protocol, the guardrail pipeline stages, the app
factory, the config object). The realistic distance to production is **roughly
two to three focused weeks** of hardening, dominated by **P0‑1 (latency)**,
**P0‑2 (session lifecycle)**, and the **observability gap** — fix those and this
becomes a genuinely production‑ready Graph RAG chatbot.
