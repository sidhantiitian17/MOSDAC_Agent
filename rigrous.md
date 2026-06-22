# RIGOROUS PRODUCTION-READINESS & SECURITY AUDIT — MOSDAC GraphRAG Agent

**Date:** 2026-06-22
**Scope:** Whole codebase, end-to-end — FastAPI gateway (`chat_api/`), guardrails, RAG core (`graph_rag/`), persistence, deployment (`Dockerfile.api`, `docker-compose.yml`, nginx), client widgets, dependency/packaging, config.
**Method:** Manual read of every load-bearing module + infra file, dependency-version reconciliation, git-tracking audit, targeted test run (`tests/test_auth.py`, `tests/test_chat_api.py` → 59 passed).

---

## VERDICT (after remediation): ✅ GO — all B/H/M/L items below are implemented and the suite is green (515 passed, 12 skipped — live-dependency tests only).

> Original verdict (kept for the record): ❌ NO-GO — the gap was narrow and almost entirely in the packaging/deployment layer, not the application code.

The **application logic was already production-grade.** The blockers were in how it was **packaged and deployed**, and they have now been fixed (config/infra, not redesign). See the **RESOLUTION STATUS** below for exactly what changed, per finding.

---

## ✅ RESOLUTION STATUS (2026-06-22)

Every B1–B3, H1–H4, M1–M7 and L1–L6 item is implemented. Verified with `pytest` (515 passed / 12 skipped / 0 failed), `uv lock --check` (consistent), and `docker compose config` (valid). No functionality was removed; tests that asserted the old insecure behavior were updated to the new contracts and new regression tests were added.

| ID | Status | What changed (files) |
|----|--------|----------------------|
| B1 | ✅ Fixed | New `.dockerignore` excludes `.env`, `conversations.db*`, `.git`, data dirs, venv, tests. |
| B2 | ✅ Fixed | `docker-compose.yml`: Neo4j ports bound to `127.0.0.1` only; `NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}`. |
| B3 | ✅ Fixed | `pyproject.toml` rewritten to the real 0.3 stack with upper bounds + extras; `requirement.txt` upper-bounded; `uv.lock` regenerated (langchain 0.3.30) and `uv lock --check` passes. |
| H1 | ✅ Fixed | `main.py` `_client_ip_key` prefers `X-Real-IP` then **right-most** XFF hop (spoof-resistant); nginx note + `.env.example` guidance to set `CHAT_API_TRUST_FORWARDED_FOR=true`. |
| H2 | ✅ Fixed | `_setup_rate_limiter` RAISES at boot when the limiter can't attach and `CHAT_API_REQUIRE_RATE_LIMIT=true` (new setting, default true). |
| H3 | ✅ Fixed | `Dockerfile.api` adds non-root `appuser` (UID 10001) + `USER`; durable `/app/data` for SQLite (named volume in compose). |
| H4 | ✅ Fixed | New `chat_api/db/postgres_repo.py` (`PostgresConversationRepository`); `CHAT_API_CONV_STORE=postgres` + `CHAT_API_POSTGRES_DSN`; factory refuses sqlite when `require_persistent_sessions=true`. |
| M1 | ✅ Fixed | `DELETE` added to `allowed_methods` default + `.env.example` + `generic.env`. |
| M2 | ✅ Fixed | `routes.py` uses `hmac.compare_digest` for the api-key and admin-token checks. |
| M3 | ✅ Fixed | `GET /metrics` now behind `_require_admin` (404 when no token, 401 wrong, 200 with `X-Admin-Token`). |
| M4 | ✅ Fixed | `DELETE /chat/{session_id}` now behind `_require_api_key` + UUID-validated (400 on non-UUID). |
| M5 | ✅ Fixed | `auth.py` logs the JWT cause server-side, returns generic `"Invalid token."`. |
| M6 | ✅ Fixed | `enable_screenshot` default → false; image path hard-gated on `vision_model` in `_validate_screenshot`. |
| M7 | ✅ Fixed | `/health`, `/ready`, `/metrics` exempted from the limiter via the limiter passed into `build_router`. |
| L1 | ✅ Fixed | `.env.example` documents dropping localhost origins in production. |
| L2 | ✅ Fixed | `BodySizeLimitMiddleware` rewritten as pure-ASGI: caps streamed bytes (chunked/no-CL) plus the Content-Length fast path. |
| L3 | ✅ Fixed | `RequestIDMiddleware` stamps `X-Request-ID`; log records carry `[request_id]`. |
| L4 | ✅ Fixed | Dockerfile CMD adds `--timeout-keep-alive 65`; documents single-worker + scale-out model. |
| L5 | ✅ Fixed | `graph_rag/config.py` `neo4j_password` default removed (empty; forced from env). |
| L6 | ✅ Fixed | Redis `--requirepass ${REDIS_PASSWORD}`; session URL carries the password. |

**New/updated tests:** image-path vision gate (×3), `/metrics` admin gate (×2), `clear_session` UUID, conv-store multi-replica guard (×2), rate-limiter fail-closed (×2).

---

### Original detailed findings (retained below for traceability)

---

## SEVERITY SUMMARY

| ID | Severity | Title | Type |
|----|----------|-------|------|
| B1 | 🔴 BLOCKER | No `.dockerignore` + `COPY . .` bakes `.env` secrets & `conversations.db` (user PII) into image layers | Security / Packaging |
| B2 | 🔴 BLOCKER | Neo4j published to host on `0.0.0.0:7474/7687` with `NEO4J_AUTH=none` | Security / Infra |
| B3 | 🔴 BLOCKER | `pyproject.toml` and `requirement.txt` declare contradictory dependency versions (langchain 1.x vs 0.3) | Reproducibility |
| H1 | 🟠 HIGH | Per-IP rate limiting is broken/ spoofable behind the documented nginx proxy | Security / Availability |
| H2 | 🟠 HIGH | Rate limiter silently disables itself if `slowapi` import fails — no startup failure | Security |
| H3 | 🟠 HIGH | API container runs as **root** | Security hardening |
| H4 | 🟠 HIGH | Per-user history is local SQLite → multi-replica deployment splits/loses user data (breaks "SCALABLE" goal) | Scalability / Correctness |
| M1 | 🟡 MEDIUM | CORS `allowed_methods` default omits `DELETE`, but the widget issues `DELETE` | Correctness |
| M2 | 🟡 MEDIUM | API-key / admin-token compared with `!=` (non-constant-time) — timing side-channel | Security |
| M3 | 🟡 MEDIUM | `/metrics` exposed with no auth | Security / Info disclosure |
| M4 | 🟡 MEDIUM | `DELETE /chat/{session_id}` is unauthenticated and unvalidated | Security |
| M5 | 🟡 MEDIUM | JWT decode errors leak library internals to the client | Info disclosure |
| M6 | 🟡 MEDIUM | Screenshots enabled by default while `vision_model` is empty → 8 MB uploads sent to a text-only model | UX / Resource |
| M7 | 🟡 MEDIUM | Health/readiness/metrics probes share the global per-IP rate budget | Availability |
| L1 | 🟢 LOW | Prod CORS allow-list still contains `localhost` origins | Hygiene |
| L2 | 🟢 LOW | Body-size middleware only checks `Content-Length` (chunked bypass) | Robustness |
| L3 | 🟢 LOW | No request/correlation ID in logs | Observability |
| L4 | 🟢 LOW | Single uvicorn process, no worker/keep-alive tuning | Scalability |
| L5 | 🟢 LOW | `graph_rag/config.py` ships a weak default `neo4j_password` | Hygiene (ties to B2) |
| L6 | 🟢 LOW | Redis has no `requirepass` (internal-only today) | Defense-in-depth |

---

## 🔴 BLOCKERS — must fix before any deployment

### B1 — No `.dockerignore`; `Dockerfile.api` does `COPY . .`
**Location:** `Dockerfile.api:33` (`COPY . .`), repo root (no `.dockerignore`).

**What's wrong:** Docker `COPY . .` ignores `.gitignore` and only respects `.dockerignore`, which does not exist. The entire build context is copied into the image, including:
- `.env` — **live secrets** (`TABBY_API_TOKEN`, `CHAT_API_ADMIN_TOKEN`, `CHAT_API_API_KEY`, `DRUPAL_PASSWORD`, future Keycloak/Neo4j creds).
- `conversations.db` / `-wal` / `-shm` — **persisted user chat history (PII)**.
- `.git/` (full history), `chroma_db/`, `downloads/`, `atlases_pdfs/` (hundreds of MB), `venv/` if present.

**Impact:** Any image pushed to a registry (or shared, or pulled by an operator) ships your secrets and users' chat history baked into a layer — recoverable with `docker history` / layer extraction even after a later `rm`. Also massively bloats the image and slows builds.

**Fix:** Add a `.dockerignore` at repo root:
```
.git
.gitignore
.env
.env.*
!.env.example
venv/
.venv/
__pycache__/
*.pyc
conversations.db*
chroma_db/
neo4j_data/
downloads/
atlases_pdfs/
.pytest_cache/
tests/
eval_runs/
*.md
ingest_manifest.json
drupal_ingestion_state.json
```
(The runtime data dirs are mounted as volumes in compose, so excluding them from the image is correct.) Rebuild and confirm with `docker history` that no `.env` layer exists.

---

### B2 — Neo4j published on host with authentication disabled
**Location:** `docker-compose.yml:25-31` (`ports: 7474/7687`, `NEO4J_AUTH: none`).

**What's wrong:** The graph DB is bound to `0.0.0.0:7474` (HTTP/Browser) and `0.0.0.0:7687` (Bolt) on the host **with no authentication**. Anyone who can reach the host on those ports has full unauthenticated read/write/delete on the entire knowledge graph (and can run arbitrary Cypher).

**Impact:** On any server with a non-loopback interface (i.e. a real deployment), the KG is wide open. Data tampering, exfiltration, or wipe.

**Fix (pick one, do both ideally):**
1. **Remove the host port mapping** — `chat_api` reaches Neo4j over the compose network via `bolt://neo4j:7687`; it does **not** need host-published ports. Delete the `ports:` block (or bind to loopback: `"127.0.0.1:7687:7687"`).
2. **Enable auth:** set `NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"`, supply `NEO4J_PASSWORD` from `.env`, and make sure `graph_rag/config.py` / the Neo4j client use it (see L5). Remove the "creds are ignored" comments.

---

### B3 — Contradictory dependency manifests; non-reproducible build
**Location:** `pyproject.toml:6-11` vs `requirement.txt` vs the live venv.

**What's wrong:** Three sources of truth disagree:
- `pyproject.toml` → `langchain>=1.2.18`, `langchain-community>=0.4.1`, `langchain-openai>=1.2.1`
- `requirement.txt` → `langchain>=0.3`, `langchain-community>=0.3`, `langchain-openai>=0.2` (the file the Dockerfile actually installs)
- Live venv → `langchain==0.3.30`

`requirement.txt` even documents: *"Do NOT unpin to >=0.4 — that drags langchain up to 1.x and breaks langchain-chroma / langchain-neo4j."* Yet `pyproject.toml` does exactly that. Anyone running `uv sync` / `pip install .` (the standard path for a `pyproject`-based project) installs langchain 1.x and gets a **broken stack**. `uv.lock` (180 pkgs) was generated against the wrong manifest.

**Impact:** Builds are not reproducible and the "obvious" install command produces a non-working environment. This is a latent outage waiting for the next clean build / new machine / CI runner.

**Fix:** Make **one** source of truth.
- Put the real, working pinned set (the 0.3 line, ragas `>=0.2,<0.3`, etc., as in `requirement.txt`) into `pyproject.toml [project].dependencies`, with upper bounds.
- Regenerate `uv.lock` from it (`uv lock`).
- Have `Dockerfile.api` install from the lock (`uv sync --frozen` or `pip install -r requirement.txt` with hashes) — pick **one** mechanism and delete the other so they can't drift again.
- Add upper bounds to every `>=` in `requirement.txt` (e.g. `fastapi>=0.100,<0.116`) so a transitive major bump can't silently break prod.

---

## 🟠 HIGH — fix before exposing to real traffic

### H1 — Rate limiting is broken & spoofable behind the documented nginx proxy
**Location:** `chat_api/main.py:92-99` (`_client_ip_key`), `chat_api/config.py:87` (`trust_forwarded_for=False` default), `deployments/nginx/mosdac.conf:43`.

**What's wrong (two compounding bugs):**
1. The shipped deployment is "nginx → FastAPI". With `trust_forwarded_for=False` (the default), `_client_ip_key` returns `request.client.host` = the **nginx IP** for *every* request. All users collapse into **one rate-limit bucket** → either the whole site shares a single 20/min budget (trivially self-DoS'd) or per-client abuse limiting effectively doesn't exist.
2. If you flip `trust_forwarded_for=True`, the app trusts `X-Forwarded-For.split(",")[0]` (left-most). nginx uses `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`, which **appends to a client-supplied header**. A client sending `X-Forwarded-For: 1.2.3.4` controls the left-most value → **rate-limit key is attacker-spoofable**, defeating per-IP limits and abuse lockout.

**Impact:** The primary abuse / DoS control on a public endpoint is either inert or bypassable.

**Fix:**
- Set `CHAT_API_TRUST_FORWARDED_FOR=true` **only** behind the trusted proxy, AND
- In nginx, overwrite rather than append: `proxy_set_header X-Forwarded-For $remote_addr;` (or set `X-Real-IP` and have the app key on a single trusted hop), so the client cannot inject the value.
- Belt-and-braces in `_client_ip_key`: when trusting XFF, take the right-most untrusted hop or strip to the proxy-appended entry, not blindly `[0]`.

### H2 — Rate limiter silently disables itself
**Location:** `chat_api/main.py:102-124` (`_setup_rate_limiter` swallows `ImportError` with a WARN).

**What's wrong:** If `slowapi` isn't importable (partial install, env drift, trimmed image), rate limiting is silently skipped and the app still boots and serves the public `/chat`. There is no startup failure and the only signal is a log line nobody reads.

**Impact:** A public LLM endpoint with zero rate limiting and no alarm.

**Fix:** Add `CHAT_API_REQUIRE_RATE_LIMIT=true` (default true in prod) and **raise** at startup if the limiter could not be attached. Keep the soft-fallback only for dev.

### H3 — Container runs as root
**Location:** `Dockerfile.api` (no `USER` directive; `CMD uvicorn …` runs as UID 0).

**Impact:** Any RCE in a dependency (LLM/parsing/OCR stack is large) runs as root inside the container — worse blast radius, easier breakout.

**Fix:**
```dockerfile
RUN useradd -m -u 10001 appuser && chown -R appuser /app
USER appuser
```
(Place after `COPY . .`; ensure mounted volume dirs are writable by UID 10001.)

### H4 — Per-user history is local SQLite → multi-replica splits/loses data
**Location:** `chat_api/db/sqlite_repo.py` (single shared `sqlite3` connection + one global `RLock`); `chat_api/config.py:151` (`conv_store="sqlite"`, `sqlite_path="./conversations.db"`).

**What's wrong:** `require_persistent_sessions` forces Redis for *session* state, but the conversation store is a **local SQLite file**. The moment you run more than one API replica (the stated "SCALABLE" goal), each replica has its own `conversations.db` → a user's history is split across replicas / appears/disappears depending on which one the LB routes to. Separately, all writes serialize through one process-wide `RLock`, capping write throughput.

**Impact:** Correctness failure at horizontal scale; single-writer throughput ceiling.

**Fix:** The repository abstraction already exists (`ConversationRepository` Protocol). Add a Postgres-backed implementation and select it via `CHAT_API_CONV_STORE=postgres` for multi-replica deployments. Single-replica today is fine — but document that **SQLite ⇒ single replica only**, mirroring the existing `require_persistent_sessions` guard (consider refusing `sqlite` + multi-worker at startup).

---

## 🟡 MEDIUM

### M1 — CORS `allowed_methods` default omits DELETE, but the widget deletes
**Location:** `chat_api/config.py:45` and `.env.example:212` → `GET,POST,OPTIONS`; widget issues `DELETE /conversations/{id}` (`static/graph-rag-chat-widget.js:625-626`) and route `DELETE /chat/{session_id}`. (`deployments/generic.env` *does* list DELETE — inconsistent.)

**Impact:** Cross-origin "delete conversation" fails the CORS preflight. Masked only when the widget is same-origin via the nginx `/chatapi/` proxy; breaks for any direct cross-origin embedding.

**Fix:** Add `DELETE` to the default in `config.py` and to `.env.example` (`GET,POST,DELETE,OPTIONS`). Reconcile all three env templates.

### M2 — Non-constant-time secret comparison
**Location:** `chat_api/routes.py:45` (`provided != expected`) and `:54` (`x_admin_token != expected`).

**Impact:** String `!=` short-circuits on first differing byte → timing side-channel for brute-forcing the API key / admin token.

**Fix:** `import hmac; if not hmac.compare_digest(provided or "", expected): raise …` for both checks.

### M3 — `/metrics` is unauthenticated
**Location:** `chat_api/routes.py:107-112`, `enable_metrics=True` default.

**Impact:** Anyone can read request volumes, refusal/abuse rates, latencies, cache hit ratios, internal metric names — reconnaissance + load-pattern leakage.

**Fix:** Guard `/metrics` with the admin token (reuse `_require_admin`), or expose it only on an internal port / bind, and have Prometheus scrape that.

### M4 — `DELETE /chat/{session_id}` is unauthenticated and unvalidated
**Location:** `chat_api/routes.py:226-229` (`clear_session`).

**What's wrong:** Unlike `/chat`, this mutation has **no** `_require_api_key` dependency and the path `session_id` is not UUID-validated. Even when `CHAT_API_API_KEY` is set to lock down the endpoint, anyone can clear arbitrary sessions.

**Impact:** Cross-session griefing (wipe another user's in-flight ephemeral history) without credentials.

**Fix:** Add `dependencies=[Depends(_require_api_key)]` and validate the `session_id` is a UUID (reject otherwise), consistent with the request model.

### M5 — JWT decode error detail leaked to client
**Location:** `chat_api/auth.py:93-95` → `detail=f"Invalid token: {exc}"`.

**Impact:** Returns PyJWT internals (claim names, alg, validation specifics) to the caller — info disclosure that aids token forgery attempts.

**Fix:** Log `exc` server-side; return a generic `detail="Invalid token."` (the `verify failed` branch at :96-101 already does this — make both consistent).

### M6 — Screenshots on by default with no vision model
**Location:** `chat_api/config.py:65` (`enable_screenshot=True`), `:71` (`vision_model=""`); image path `chat_api/service.py:161-217`.

**What's wrong:** The startup warning fires, but the image path still accepts up to 8 MB base64 uploads and feeds them to the **text-only** default chat model, which cannot see them. The `.env.example` comment itself says to keep this OFF until a VLM is wired — but the default is `True`.

**Impact:** Misleading UX (users think the bot "saw" their screenshot), wasted bandwidth and LLM tokens, larger attack surface for no functional gain.

**Fix:** Default `enable_screenshot=False`, OR gate the image branch on `vision_model` being non-empty (fall back to text-only with a clear message otherwise).

### M7 — Health/readiness/metrics share the global rate budget
**Location:** `chat_api/main.py:111-115` (`default_limits` applied app-wide via `SlowAPIMiddleware`).

**Impact:** Frequent LB/orchestrator probes from one source IP can exhaust the per-IP budget and cause `/ready` to 429 → the LB marks the replica unhealthy → flapping.

**Fix:** Exempt `/health`, `/ready`, `/metrics` from the limiter (slowapi `@limiter.exempt` or a path allow-list).

---

## 🟢 LOW (hygiene / hardening / ops)

- **L1 —** Prod CORS allow-list (`chat_api/config.py:33-44`, `.env.example:211`) still includes `http://localhost*` / `127.0.0.1` origins alongside `mosdac.gov.in`. With `allow_credentials=True` this should be trimmed to real origins in the production `.env`.
- **L2 —** `BodySizeLimitMiddleware` (`chat_api/main.py:78-87`) only inspects `Content-Length`; a chunked request without it bypasses the early reject. Downstream Pydantic length caps still apply, so impact is limited — consider a streaming read cap for completeness.
- **L3 —** No request/correlation ID in logs; cross-layer tracing in prod is painful. Add a middleware that stamps an `X-Request-ID` and includes it in log records.
- **L4 —** `Dockerfile.api` CMD runs a single uvicorn process (no `--workers`, no `--timeout-keep-alive`). Scaling is replica-only. Document the intended worker/replica model (and remember multi-worker needs Redis sessions + a shared conv store — see H4).
- **L5 —** `graph_rag/config.py:21` ships `neo4j_password="neo4j_password"` as a source default. Harmless while `NEO4J_AUTH=none`, but it becomes a weak baked-in default the instant B2's auth is enabled without a `.env` override. Remove the default (force it from env).
- **L6 —** Redis runs with no `requirepass` (`docker-compose.yml:48`). It is **not** host-published (good), so internal-only today; add a password for defense-in-depth before ever exposing it.

---

## ✅ WHAT IS ALREADY DONE RIGHT (verified, not assumed)

These were checked and are genuinely solid — do not "fix" them:

- **Anti-IDOR data model:** every conversation/message query filters on `user_id` **and** `conversation_id`; there is deliberately no "fetch by id alone" method; not-found and not-owned are indistinguishable (404). All SQL is parameterized (`chat_api/db/sqlite_repo.py`, `chat_api/db/repository.py`).
- **No client-side XSS:** the widget renders LLM answers and titles via `textContent`, never `innerHTML` (`static/graph-rag-chat-widget.js:690,740,592`). The only `innerHTML` uses are static templates/icons.
- **JWT verification is correct:** signature + `exp` enforced, explicit RS256 allow-list blocks `alg:none` / HS-RS confusion, JWKS cached with rotation, audience/issuer enforced when configured, PyJWT imported lazily (`chat_api/auth.py`).
- **Guardrails fail closed by default** with observable degradation metrics (`guardrails/pipeline.py`, `guardrails/config.py`); L1 input / L2 grounding / L4 output run on both text and image paths and before LLM spend.
- **Resource discipline:** session TTL + LRU cap, `require_persistent_sessions` guard, LLM concurrency semaphore + hard timeout + bounded retries, body/message size caps, Neo4j pool tuning, readiness probes wired to the compose healthcheck.
- **Secrets hygiene in git:** `.env`, `conversations.db`, `chroma_db/`, etc. are git-ignored and **not** tracked; no `.env` in git history. (The image-layer leak in B1 is the gap, not git.)
- **Security headers** (CSP, X-Frame-Options DENY, nosniff, HSTS-on-HTTPS) on every response; generic 500s (no stack traces to clients) in the `/chat` handler.
- **Clean code:** no `TODO/FIXME/HACK` debt, no stray `print()` in hot paths; 440+ tests, and `test_auth.py`+`test_chat_api.py` pass (59) at audit time.

---

## RECOMMENDED FIX ORDER (fastest path to GO)

1. **B1** `.dockerignore` (5 min, stops secret/PII leak) — do first.
2. **B2** Neo4j ports + auth (config only).
3. **B3** Reconcile dependency manifests + regenerate lock (prevents the next broken build).
4. **H3** non-root user · **H1/H2** rate-limit trust + fail-closed · **M2** constant-time compare · **M4** auth on `clear_session` · **M5** generic JWT error — all small, all security.
5. **M1** CORS DELETE · **M3** protect `/metrics` · **M6** screenshot default · **M7** exempt probes.
6. **H4** Postgres conv-store **only when** you actually move to >1 replica (single replica is fine today; just document the constraint).
7. Sweep the LOW list as hygiene.

**When B1–B3 + H1–H4 + M1–M7 are closed, this application is ready for production deployment.** The LOW items are recommended hardening, not gates.

---
*This audit reflects the codebase at commit `723c793` plus the uncommitted SSO/auth changes in the working tree. No code was modified to produce this report.*
