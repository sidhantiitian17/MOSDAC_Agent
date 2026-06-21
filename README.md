# MOSDAC GraphRAG Chatbot — Start Here

The single source of truth for what this project is, how it works end-to-end, and
how to configure, run, and operate it. Read this first.

> **What it is:** a document-grounded, offline-capable Graph-RAG assistant for the
> MOSDAC / ISRO domain. It ingests scientific PDFs, web HTML, Office files, images,
> and Drupal content; stores them in a **vector store (ChromaDB)** and a
> **knowledge graph (Neo4j)**; answers questions with **hybrid retrieval + a local
> LLM (Tabby ML)**; and wraps every request in a deterministic **guardrail
> pipeline** so it is safe to deploy on a Government of India portal.
>
> Everything can run **100% offline / air-gapped** — no internet at runtime.

---

## Table of Contents

1. [Architecture & Components](#1-architecture--components)
2. [End-to-End Process Flow](#2-end-to-end-process-flow)
3. [Prerequisites](#3-prerequisites)
4. [Installation](#4-installation)
5. [External Services (Tabby, Ollama, Neo4j, Redis)](#5-external-services)
6. [Configuration (`.env`)](#6-configuration-env)
7. [How to Start the Application](#7-how-to-start-the-application)
8. [HTTP API Reference](#8-http-api-reference)
9. [Testing](#9-testing)
10. [Evaluation (RAGAS production gate)](#10-evaluation-ragas-production-gate)
11. [Observability & Production Hardening](#11-observability--production-hardening)
12. [Project Layout](#12-project-layout)
13. [Operational Runbooks & Further Docs](#13-operational-runbooks--further-docs)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Architecture & Components

The system is composed of independently-swappable parts wired together by a
factory ([chat_api/main.py](chat_api/main.py) `create_app()`). Each external
dependency is reached over HTTP/Bolt and configured **only** through `.env`.

| Component | Role | Where | Default endpoint |
|-----------|------|-------|------------------|
| **Tabby ML** | Chat LLM + KG extraction LLM (OpenAI-compatible) | Separate container / host process | `http://localhost:8080/v1` |
| **Ollama** | Embeddings — `bge-large` via `/api/embeddings` | Host process | `http://localhost:11434` |
| **Neo4j 5.18** | Knowledge graph (entities, relations, measurements, chunks) | Container | `bolt://localhost:7687` |
| **ChromaDB** | Vector store (in-process, persisted to `./chroma_db`) | In-process | `./chroma_db` |
| **Redis** | Persistent multi-replica session store (prod) | Container | `redis://redis:6379/0` |
| **FastAPI gateway** | HTTP `/chat` API, sessions, CORS, auth, rate-limit | Container / host | `http://localhost:8000` |

Internal Python packages:

- [graph_rag/](graph_rag/) — the RAG core: `ingestion/`, `preprocessing/`,
  `embeddings/`, `vector_store/`, `knowledge_graph/`, `retrieval/`, `chain/`,
  `chat/`, `eval/`, plus [graph_rag/config.py](graph_rag/config.py) and
  [graph_rag/health.py](graph_rag/health.py).
- [guardrails/](guardrails/) — the security pipeline: `input/`, `retrieval/`,
  `output/`, `audit/`, orchestrated by [guardrails/pipeline.py](guardrails/pipeline.py).
- [chat_api/](chat_api/) — FastAPI app factory, routes, models, session backends,
  chat service, answer cache.
- [observability/](observability/) — Prometheus metrics (`/metrics`).
- [main.py](main.py) — CLI entry point. [drupal_ingest.py](drupal_ingest.py) — Drupal source.

> **No local model weights are loaded in-process.** Both the LLM and the embedder
> are HTTP services, so the Python process stays lightweight (no torch /
> sentence-transformers). See [requirement.txt](requirement.txt).

---

## 2. End-to-End Process Flow

There are two flows: **ingestion** (offline, populates the stores) and
**query/answer** (online, serves each user request).

### 2A. Ingestion flow — `python main.py ingest`

Orchestrated by [graph_rag/ingestion/pipeline.py](graph_rag/ingestion/pipeline.py).

```
files (downloads/, atlases_pdfs/) ─┐
                                   ├─► 1. DISCOVER + LOAD
Drupal JSON:API (optional) ────────┘      • content-hash manifest skips already-ingested files
                                          • Docling parses PDFs → Markdown + LaTeX math + OCR
                                          • Office/HTML/images via the format registry
                                          • quality gate drops blank scans / OCR gibberish
                                              │
                                              ▼
                                       2. SPLIT into chunks
                                          • header-aware, math/table-safe, overlapped
                                              │
                          ┌───────────────────┴───────────────────┐
                          ▼                                        ▼
              3. EMBED → ChromaDB                      4. EXTRACT → Neo4j
                 • bge-large via Ollama                   • LLM/spaCy triples (subject→rel→object)
                 • dedup by chunk_id                      • regex quantity parsing → Measurement nodes
                 • text features tagged                   • Chunk nodes for provenance/citations
                   (has_formula, numeric_density)         • entities linked to source chunks
                          └───────────────────┬───────────────────┘
                                              ▼
                                  5. RECORD manifest (only on a clean run)
```

Key behaviours:

- **Incremental** — a SHA-256 manifest (`ingest_manifest.json`) skips files already
  ingested. `--force` re-ingests everything. The two stores are also data-idempotent
  (Chroma dedups by `chunk_id`; Neo4j `MERGE`s on canonical keys).
- **Crash-safe** — the manifest is updated only after a complete, error-free run, so
  a partial run is safely retried.
- **Drupal** runs automatically when `DRUPAL_JSONAPI_URL` is set (delta-sync by
  content hash), sharing the same vector/KG code path.
- Flags: `--force`, `--skip-files`, `--skip-drupal`, `--skip-vector`, `--skip-graph`.

### 2B. Query / answer flow — `POST /chat` or `python main.py chat`

Coordinated by [chat_api/service.py](chat_api/service.py) `ChatService.chat()`,
with retrieval in [graph_rag/retrieval/hybrid_retriever.py](graph_rag/retrieval/hybrid_retriever.py)
and guardrails in [guardrails/pipeline.py](guardrails/pipeline.py).

```
user message
   │
   ▼
L1  INPUT GUARD (before any spend)                     guardrails/input/
   • normalize + charset check
   • prompt-injection detection (regex + embedding similarity)
   • PII redaction
   • domain scope gate (off-topic → refuse)            → refuse early if blocked
   │
   ▼
   QUERY CONTEXTUALIZATION (history-aware)             rewrites follow-ups into a standalone query
   │
   ▼
   HYBRID RETRIEVAL                                    graph_rag/retrieval/
   • vector (semantic, bge-large) + BM25 (keyword)
   • Reciprocal Rank Fusion (RRF)
   • feature boost for numeric/formula queries
   • passage rerank (cross-encoder or bi-encoder)
   • exact-formula verbatim injection
   • graph context (Neo4j multi-hop) assembled separately
   • retrieved context sanitized (indirect-injection defence)
   │
   ▼
L2  GROUNDING GATE                                     guardrails/retrieval/grounding_gate.py
   • relevance floor + min supporting passages
   • builds a citation registry from the hits          → refuse "no info" if not groundable
   │
   ▼
   LLM GENERATION (Tabby ML)                           graph_rag/chain/ + graph_rag/llm/tabby_client.py
   • concurrency-throttled; optional SSE streaming
   • optional multimodal path when a screenshot is attached (same L2 gate)
   │
   ▼
L4  OUTPUT GUARD                                       guardrails/output/
   • secret/leakage scrub
   • citation verification
   • grounding enforcement (flag | strip | refuse ungrounded numbers/sentences)
   • PII redaction + toxicity check
   │
   ▼
L5  AUDIT LOG + metrics                                guardrails/audit/ + observability/
   │
   ▼
answer + citations + {grounded, refused}  (only the PII-redacted turn is stored in the session)
```

The guardrail layers fail **observably**: with `GUARD_EMBEDDER_REQUIRED=true` the
system refuses (fail-closed) when the embedder is down; otherwise it degrades but
always emits the `guardrail_degraded_total` metric.

---

## 3. Prerequisites

- **Python 3.11+** (declared in [pyproject.toml](pyproject.toml)).
- **Docker + Docker Compose** (for the Neo4j / Redis / API stack).
- **OCR binaries** — Tesseract + Poppler (for image-only/scanned PDFs):
  - Linux: `sudo apt-get install tesseract-ocr tesseract-ocr-eng poppler-utils`
  - macOS: `brew install tesseract poppler`
  - Windows: install binaries and set `TESSERACT_CMD` / `POPPLER_PATH` in `.env`.
- **Two external model services** (see [§5](#5-external-services)):
  - **Tabby ML** serving a chat model (default config: `Qwen2-1.5B-Instruct`;
    production target: `Qwen2.5-Coder-32B-Instruct`).
  - **Ollama** serving the `bge-large` embedding model.

---

## 4. Installation

```bash
# 1. Clone, then create a virtualenv
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirement.txt

# 3. Install the spaCy English model (used by KG extraction fallback)
python -m spacy download en_core_web_sm

# 4. Create your .env from the template and fill it in
cp .env.example .env
```

> `.env` is git-ignored — secrets never reach the repo. Every credential is loaded
> from `.env` only; nothing is hardcoded in source.

---

## 5. External Services

You need Tabby (LLM), Ollama (embeddings), Neo4j (graph), and — for production —
Redis (sessions). The compose file starts Neo4j + Redis + the API; **Tabby and
Ollama run on the host** (or their own containers).

### Tabby ML (LLM)
Run Tabby serving an OpenAI-compatible endpoint on `:8080`. Point `.env` at it:
```bash
TABBY_BASE_URL=http://localhost:8080/v1
TABBY_API_TOKEN=your_tabby_token_here
TABBY_MODEL=Qwen2-1.5B-Instruct        # prod: Qwen2.5-Coder-32B-Instruct
```

### Ollama (embeddings)
```bash
ollama pull bge-large
ollama serve                            # serves on :11434
```
```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=bge-large
```

### Neo4j + Redis
Started by [docker-compose.yml](docker-compose.yml) (Neo4j runs `NEO4J_AUTH=none`;
the graph persists to the `./neo4j_data` bind-mount). To run Neo4j standalone
instead, see the header of the compose file.

> **Docker networking:** when the API runs inside compose, `localhost` means the
> container itself. The compose file overrides `TABBY_BASE_URL` and
> `OLLAMA_BASE_URL` to `host.docker.internal` so the container can reach the
> host's Tabby/Ollama, and points Neo4j/Redis at their service names.

For a full air-gapped (ISRO on-prem) walkthrough — loading images from tarballs,
pre-caching models — see [docs/start_offline.md](docs/start_offline.md).

---

## 6. Configuration (`.env`)

All behaviour is config-driven; flip a flag, no code change. The complete,
commented template is [.env.example](.env.example). The most important groups:

| Group | Keys | Notes |
|-------|------|-------|
| **LLM** | `TABBY_BASE_URL`, `TABBY_API_TOKEN`, `TABBY_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` | OpenAI-compatible. `LLM_REQUEST_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_MAX_CONCURRENCY` for resilience. |
| **KG extraction** | `EXTRACTION_BACKEND` (`llm`/`spacy`/`auto`), `TABBY_EXTRACTION_MODEL` | Swap the extraction model in one line; blank reuses `TABBY_MODEL`. |
| **Embeddings** | `OLLAMA_BASE_URL`, `OLLAMA_EMBEDDING_MODEL`, `EMBED_QUERY_INSTRUCTION`, `OLLAMA_EMBED_BATCH_SIZE` | bge-style asymmetric query prefix; native batch embedding for throughput. |
| **Neo4j** | `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, pool/timeout settings | Container ignores creds (`NEO4J_AUTH=none`). |
| **ChromaDB** | `CHROMA_PERSIST_DIR`, `CHROMA_COLLECTION` | Local persisted vector store. |
| **Ingestion / parsing** | `USE_DOCLING`, `DOCLING_*`, `INGEST_ENABLE_OFFICE/IMAGES`, `INGEST_MIN_*` (quality gate), `DOWNLOADS_DIR`, `ATLASES_DIR`, `INGEST_MANIFEST_PATH` | Docling is the primary PDF parser; quality gate rejects low-signal extractions. |
| **Chunking** | `CHUNK_SIZE`, `CHUNK_OVERLAP`, `CHUNK_MAX_SECTION_CHARS`, `ENABLE_SECTION_SUBSPLIT` | |
| **Retrieval** | `TOP_K_VECTOR`, `TOP_K_BM25`, `TOP_K_GRAPH`, `GRAPH_DEPTH`, `HYBRID_RRF_K`, `ENABLE_PASSAGE_RERANK`, `TOP_K_PASSAGES`, `ENABLE_FEATURE_BOOST`, `ENABLE_EXACT_FORMULA_MATCH`, `ENABLE_CROSS_ENCODER_RERANK` | |
| **History-aware** | `ENABLE_QUERY_CONTEXTUALIZATION`, `ENABLE_CONVERSATION_SUMMARY` | Rewrite follow-ups; summarize overflow turns. |
| **Guardrails** | `GUARD_GROUNDING_ACTION` (`flag`/`strip`/`refuse`), `GUARD_EMBEDDER_REQUIRED`, `GUARD_CONTEXT_INJECTION_SCAN`, `GUARD_AUDIT_LOG_PATH` | Anti-hallucination + injection defence + audit. |
| **Drupal** | `DRUPAL_JSONAPI_URL`, `DRUPAL_USERNAME`, `DRUPAL_PASSWORD` | Enables Drupal ingestion when set. |
| **Chat API** | `CHAT_API_*` — title/bot name, `ALLOWED_ORIGINS` (CORS), `SESSION_BACKEND` (`memory`/`redis`), `API_KEY`, `ADMIN_TOKEN`, `ENABLE_METRICS`, `MAX_REQUEST_BYTES`, `ENABLE_SCREENSHOT` | Production: `redis` backend + `REQUIRE_PERSISTENT_SESSIONS=true`. |
| **Eval (RAGAS)** | `RAGAS_JUDGE_MODEL`, `RAGAS_JUDGE_BASE_URL`, `RAGAS_JUDGE_*` | Judge must be a **stronger** model than the generator under test. |
| **System prompt** | `SYSTEM_PROMPT_PATH` → [prompts/system_prompt.txt](prompts/system_prompt.txt) | Edit the file to change LLM behaviour, no code change. |

---

## 7. How to Start the Application

### Option A — CLI (local dev)

```bash
python main.py test               # health-check every component (config, loader, deps, LLM)
python main.py ingest             # incremental ingestion (files + Drupal if configured)
python main.py ingest --force     # re-ingest everything
python main.py chat               # interactive REPL
python main.py build-communities  # build GraphRAG community summaries
```

### Option B — FastAPI server (local)

```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Option C — Docker Compose (full stack)

Starts Neo4j + Redis + the API (Tabby and Ollama must already be running on the host):

```bash
docker compose up -d --build
docker compose logs -f chat_api
```

The API container has a **readiness-aware healthcheck** — the orchestrator only
routes traffic once the embedder + Chroma + Neo4j actually answer (not merely when
the port opens).

> **First-run order:** start Tabby + Ollama + Neo4j → run `python main.py test` →
> `python main.py ingest` to populate the stores → then start the API and chat.

---

## 8. HTTP API Reference

Defined in [chat_api/routes.py](chat_api/routes.py).

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Liveness — cheap, never touches downstream deps. |
| `GET` | `/ready` | Readiness — probes embedder / Chroma / Neo4j; `503` when not ready. |
| `GET` | `/config` | Widget config (title, bot name, screenshot toggle). |
| `GET` | `/metrics` | Prometheus metrics (when `CHAT_API_ENABLE_METRICS=true`). |
| `POST` | `/chat` | Main Q&A. Returns `answer`, `citations`, `grounded`, `refused`. Honours `CHAT_API_API_KEY`. |
| `POST` | `/chat/stream` | Server-Sent Events — `token` events then one authoritative `final` event (post-L4). |
| `DELETE` | `/chat/{session_id}` | Clear a session's history. |
| `POST` | `/reload` | Hot-reload BM25 index / caches after a re-ingest (requires `CHAT_API_ADMIN_TOKEN`). |

Example:

```bash
curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"11111111-1111-1111-1111-111111111111","message":"What is the spatial resolution of INSAT-3D Imager TIR-1?"}'
```

The browser widget snippet lives in
[deployments/widget-snippets/generic.html](deployments/widget-snippets/generic.html);
per-domain deployment config is in [deployments/README.md](deployments/README.md).

---

## 9. Testing

```bash
pytest -q                                  # full suite
pytest tests/test_chat_api.py -v           # API layer
pytest tests/test_pipeline_security.py -v  # guardrails
```

Tests that need live Neo4j / Ollama / Tabby are auto-skipped when those services
are absent, so the suite runs green in CI without them. CI
([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs the tests plus a
dependency vulnerability scan (pip-audit) and a Trivy image scan on every push/PR.

---

## 10. Evaluation (RAGAS production gate)

A "no-mercy" production gate that tries to *fail* the system before users do.

```bash
python main.py ragas-eval                  # PROD config against tests/eval/golden/v1
python main.py ragas-eval --smoke          # cheaper subset for fast iteration / CI tripwire
python main.py ragas-eval --config BOTH    # PROD + RAW (guards flag-only)
python main.py eval                         # legacy cheap deterministic Phase-0 harness
```

Requires a configured judge (`RAGAS_JUDGE_*`) — **a stronger model than the local
generator**, never the same one. Full methodology and the GO/NO-GO scorecard are in
[evaluation_plan.md](evaluation_plan.md). Golden dataset:
[tests/eval/golden/v1/](tests/eval/golden/v1/).

---

## 11. Observability & Production Hardening

- **Metrics:** Prometheus at `GET /metrics` (request counts/latency, guardrail
  refusals, degradation, answer-cache hits). See [observability/](observability/).
- **Security:** OWASP headers, CORS allowlist (no wildcards), per-IP rate limiting
  (slowapi), body-size cap, optional API-key/admin-token auth, UUID session-id
  validation — all in [chat_api/main.py](chat_api/main.py).
- **Resilience:** LLM retries + concurrency cap, Neo4j connection pool, BM25
  warm-up on boot + hot-reload, Redis-backed persistent sessions for multi-replica.
- **Load test:** [scripts/loadtest.py](scripts/loadtest.py).

The full production-readiness review (blockers, P0–P2 items, what's done) is in
[production.md](production.md). Backup & disaster recovery:
[docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).

---

## 12. Project Layout

```text
.
├── main.py                  # CLI: ingest / chat / test / eval / ragas-eval / build-communities
├── drupal_ingest.py         # Drupal JSON:API → Graph RAG ingestion
├── chat_api/                # FastAPI gateway (app factory, routes, service, sessions, cache)
├── graph_rag/               # RAG core
│   ├── ingestion/           #   load → split (+ format registry, quality gate, manifest)
│   ├── preprocessing/       #   Docling-based cleaning, header chunking, math safety
│   ├── embeddings/          #   Ollama bge-large HTTP embedder
│   ├── vector_store/        #   ChromaDB
│   ├── knowledge_graph/     #   extraction, quantity parsing, Neo4j store, communities
│   ├── retrieval/           #   vector + BM25 + graph + RRF fusion + rerank
│   ├── chain/               #   RAG chain (prompt assembly + LLM)
│   ├── chat/                #   chatbot REPL + conversation summarizer
│   ├── eval/                #   RAGAS gate, custom metrics, scorecard
│   ├── config.py            #   pydantic settings (reads .env)
│   └── health.py            #   shared readiness probes (CLI test + /ready)
├── guardrails/              # L1 input · L2 retrieval · L4 output · L5 audit
├── observability/           # Prometheus metrics
├── prompts/                 # system_prompt.txt
├── deployments/             # per-domain env + embeddable widget snippet
├── scripts/                 # loadtest.py
├── tests/                   # pytest suite incl. tests/eval/golden/
├── docker-compose.yml       # Neo4j + Redis + chat_api
├── Dockerfile.api           # API image (Tesseract, Poppler, spaCy, Docling models)
├── requirement.txt
└── .env.example             # configuration template
```

---

## 13. Operational Runbooks & Further Docs

These are the **current, maintained** docs (everything else was historical
planning/scratch and has been removed):

- [start.md](start.md) — **this file** (entry point).
- [docs/start_offline.md](docs/start_offline.md) — air-gapped / ISRO on-prem setup.
- [production.md](production.md) — production-readiness review & hardening checklist.
- [evaluation_plan.md](evaluation_plan.md) — RAGAS evaluation methodology & gate.
- [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) — backup & disaster recovery runbook.
- [deployments/README.md](deployments/README.md) — per-domain deployment customization.
- [.env.example](.env.example) — the authoritative, fully-commented config reference.

---

## 14. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `python main.py test` fails on embedder/Chroma/Neo4j/LLM | A dependency is down — start Tabby (`:8080`), Ollama (`:11434`), Neo4j (`:7687`). |
| `/ready` returns 503 | One of embedder / Chroma / Neo4j is unreachable; check `docker compose logs`. |
| API container can't reach Tabby/Ollama | Inside compose, `localhost` ≠ host. Use `host.docker.internal` (already wired in [docker-compose.yml](docker-compose.yml)). |
| Bot always refuses ("no info") | Stores are empty or off-topic — run `python main.py ingest`; check the scope gate / `GUARD_GROUNDING_ACTION`. |
| Ingestion re-processes everything | Expected with `--force`; otherwise ensure `ingest_manifest.json` persists between runs. |
| Scanned/image PDF yields no text | Install Tesseract + Poppler; set `DOCLING_FORCE_FULL_PAGE_OCR_DIRS` for raster atlases. |
| Sessions lost across workers | Use `CHAT_API_SESSION_BACKEND=redis` and set `CHAT_API_REDIS_URL`. |
| RAGAS eval refuses to score | Set `RAGAS_JUDGE_MODEL` (a stronger model than the generator) — see [evaluation_plan.md](evaluation_plan.md). |
