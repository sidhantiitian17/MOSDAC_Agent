# Installation & Startup Guide (`install.md`)

> A **step-by-step**, beginner-friendly guide to get the MOSDAC GraphRAG Agent running
> on your machine — from a fresh clone to a working chatbot. If you want to understand
> *what* you're installing first, read **[readme_main.md](readme_main.md)**.

There are **two ways** to run this project:

- **Path A — Local (virtualenv).** Best for development and understanding the code.
- **Path B — Docker Compose.** Best for running the whole stack the way it ships.

Both paths need the **same four external services**. Read [§1](#1-what-you-need-the-big-picture)
once, then pick a path.

---

## Table of Contents

1. [What you need (the big picture)](#1-what-you-need-the-big-picture)
2. [Prerequisites — install these first](#2-prerequisites)
3. [Get the code](#3-get-the-code)
4. [Path A — Local install (virtualenv)](#4-path-a--local-install-virtualenv)
5. [Start the external services](#5-start-the-external-services)
6. [Configure `.env`](#6-configure-env)
7. [Verify everything is wired (`python main.py test`)](#7-verify-everything-is-wired)
8. [Ingest your documents](#8-ingest-your-documents)
9. [Run the chatbot](#9-run-the-chatbot)
10. [Path B — Docker Compose (full stack)](#10-path-b--docker-compose-full-stack)
11. [Embed the chat widget in a website](#11-embed-the-chat-widget-in-a-website)
12. [Common problems & fixes](#12-common-problems--fixes)
13. [Quick command cheat-sheet](#13-quick-command-cheat-sheet)

---

## 1. What you need (the big picture)

The Python app does **not** contain the LLM or the embedding model — it talks to them
over HTTP. So before the app can do anything useful, you need these running:

| # | Service | Purpose | Default port | Required? |
|---|---------|---------|--------------|-----------|
| 1 | **Tabby ML** | The chat LLM + KG extraction LLM | `8080` | **Yes** (for chat/ingest with LLM extraction) |
| 2 | **Ollama** (`bge-large`) | Text → embeddings | `11434` | **Yes** |
| 3 | **Neo4j 5.18** | The knowledge graph | `7687` (Bolt), `7474` (Browser) | **Yes** |
| 4 | **Redis** | Persistent sessions (production) | `6379` | Optional (prod / multi-replica) |

Plus the Python app itself (the **FastAPI gateway**) on port `8000`.

> **First-run order matters:** start Tabby + Ollama + Neo4j → `python main.py test`
> → `python main.py ingest` (fills the stores) → then start the API / chat. If you skip
> ingestion, the bot correctly refuses every question with "I don't have that
> information" because the stores are empty.

---

## 2. Prerequisites

Install these on your machine (one-time).

### 2.1 Python 3.11+
Check: `python --version` (must be ≥ 3.11). On WSL/Ubuntu: `sudo apt-get install python3.11 python3.11-venv`.

### 2.2 Docker + Docker Compose
For Neo4j/Redis (and Path B). Check: `docker --version` and `docker compose version`.

### 2.3 OCR binaries — Tesseract + Poppler
Needed to read **scanned / image-only PDFs** (the atlases). Without them, normal text
PDFs still work, but image atlases yield no text.

- **Linux/WSL:** `sudo apt-get install tesseract-ocr tesseract-ocr-eng poppler-utils`
- **macOS:** `brew install tesseract poppler`
- **Windows:** install the binaries, then set `TESSERACT_CMD` and `POPPLER_PATH` in `.env`.

> Path B (Docker) installs Tesseract + Poppler **inside the image** automatically — you
> only need these on the host for Path A.

### 2.4 Tabby ML (the LLM server)
Run Tabby serving an **OpenAI-compatible** endpoint on `:8080`. Default model in config
is `Qwen2-1.5B-Instruct`; the production target is a larger model (e.g.
`Qwen2.5-Coder-32B-Instruct`). Note its API token — you'll put it in `.env`.

### 2.5 Ollama (the embedding server)
```bash
# Install Ollama (https://ollama.com), then:
ollama pull bge-large       # download the embedding model used by this project
ollama serve                # serves on http://localhost:11434
```

> ⚠️ This project uses **`bge-large`**, *not* `nomic-embed-text`. Using a different model
> changes the vector dimensions and breaks an existing ChromaDB index.

---

## 3. Get the code

```bash
git clone <your-repo-url> MOSDAC_Agent
cd MOSDAC_Agent
```

---

## 4. Path A — Local install (virtualenv)

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate

# 2. Install all Python dependencies
pip install -r requirement.txt

# 3. Install the spaCy English model (used by the KG-extraction fallback)
python -m spacy download en_core_web_sm
```

That installs everything in [requirement.txt](requirement.txt): LangChain 0.3, ChromaDB,
the Neo4j driver, Docling, FastAPI, slowapi, RAGAS, etc.

> **Optional extras** (already in `requirement.txt`): `redis` (Redis sessions),
> `psycopg[binary,pool]` (Postgres conversation store). `pyproject.toml` also defines
> installable extras (`redis`, `postgres`, `eval`, `dev`) if you install via `pip install -e .`.

---

## 5. Start the external services

### Neo4j + Redis via Docker (recommended, even for Path A)
```bash
# Starts ONLY Neo4j + Redis (and the API too — stop the api container if you run uvicorn yourself)
docker compose up -d neo4j redis
```
Neo4j Browser: <http://127.0.0.1:7474> (auth is set by `NEO4J_PASSWORD` in `.env`; the
local default container can also run `NEO4J_AUTH=none`).

### Tabby ML and Ollama (on the host)
Start these yourself as in [§2.4](#24-tabby-ml-the-llm-server) / [§2.5](#25-ollama-the-embedding-server).
They run on the **host**, not in compose.

---

## 6. Configure `.env`

```bash
cp .env.example .env
```

Open `.env` and set at least these:

```bash
# ── LLM (Tabby ML) ──────────────────────────────────────────────
TABBY_BASE_URL=http://localhost:8080/v1
TABBY_API_TOKEN=your_tabby_token_here
TABBY_MODEL=Qwen2-1.5B-Instruct          # or your production model

# ── Embeddings (Ollama) ─────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=bge-large

# ── Neo4j ───────────────────────────────────────────────────────
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=please-change-me          # MUST match the compose NEO4J_PASSWORD

# ── ChromaDB (local folder, no server) ──────────────────────────
CHROMA_PERSIST_DIR=./chroma_db

# ── Ingestion sources ───────────────────────────────────────────
DOWNLOADS_DIR=./downloads
ATLASES_DIR=./atlases_pdfs
```

> `.env` is git-ignored — your secrets never reach the repo. **Every** credential and
> behaviour toggle is read from `.env`; nothing is hardcoded. The file
> [.env.example](.env.example) documents every available key with comments.

**Windows OCR** (only if on Windows): also set
```bash
TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
POPPLER_PATH=C:/path/to/poppler/bin
```

---

## 7. Verify everything is wired

Run the built-in health check — it probes config, the document loader, the embedder,
ChromaDB, Neo4j, and the LLM (the **same** probes the live `/ready` endpoint uses):

```bash
python main.py test
```

Expected tail:
```
Checking dependencies (embedder / ChromaDB / Neo4j / LLM)...
  embedder   = ok (...)
  chroma     = ok (...)
  neo4j      = ok (...)
  llm        = ok (...)
...
ALL CHECKS PASSED
```

If any line says `FAILED`, that dependency isn't reachable — start it and re-run. See
[§12](#12-common-problems--fixes).

---

## 8. Ingest your documents

This reads everything in `downloads/` (HTML) and `atlases_pdfs/` (PDFs), and — if
`DRUPAL_JSONAPI_URL` is set — the Drupal CMS, then fills ChromaDB + Neo4j.

```bash
python main.py ingest                 # incremental (skips already-ingested files)
python main.py ingest --force         # re-ingest everything from scratch
python main.py ingest --skip-graph    # vectors only (no Neo4j writes)
python main.py ingest --skip-vector   # knowledge graph only (no Chroma writes)
python main.py ingest --skip-drupal   # files only, even if a Drupal URL is set
```

The run prints a summary (files scanned/new/updated/skipped/errors). It is **safe to
re-run** — incremental and crash-safe. Build the optional GraphRAG community summaries
afterward if you enabled them:

```bash
python main.py build-communities
```

> Tip: the repo already ships sample PDFs in `atlases_pdfs/` and saved HTML in
> `downloads/`, so you can ingest immediately to try the system.

---

## 9. Run the chatbot

### Option 1 — CLI REPL (simplest)
```bash
python main.py chat
```
```
You: What is the spatial resolution of INSAT-3D Imager TIR-1?
Assistant: ...
```
Type `reset` to clear history, `exit`/`quit` to leave.

### Option 2 — FastAPI server (the real API)
```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```
Then:
```bash
curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"11111111-1111-1111-1111-111111111111","message":"What is the spatial resolution of INSAT-3D Imager TIR-1?"}'
```
- Interactive API docs (self-hosted, offline-safe): <http://localhost:8000/docs>
- Liveness: `GET /health` · Readiness: `GET /ready`

---

## 10. Path B — Docker Compose (full stack)

This builds the API image (with Tesseract, Poppler, spaCy, and **baked-in Docling
models** for air-gapped parsing) and starts **Neo4j + Redis + the API** together.

> Tabby ML and Ollama still run on the **host**. The compose file automatically rewrites
> `TABBY_BASE_URL`/`OLLAMA_BASE_URL` to `host.docker.internal` so the container can reach
> them. (Inside a container, `localhost` means the container itself — not your host.)

```bash
# 1. Make sure Tabby (:8080) and Ollama (:11434) are running on the host.
# 2. Create and fill .env (see §6). Set a strong NEO4J_PASSWORD and REDIS_PASSWORD.
# 3. Build + start the stack:
docker compose up -d --build

# 4. Watch the API come up (it waits for Neo4j + Redis to be healthy):
docker compose logs -f chat_api
```

The API container has a **readiness-aware healthcheck** — the orchestrator only routes
traffic once the embedder + Chroma + Neo4j actually answer (not merely when the port
opens). Once healthy, the API is on <http://localhost:8000>.

**Ingest from inside the container** (so it writes to the mounted Chroma/Neo4j and can
reach the host's Ollama/Tabby):
```bash
docker compose exec chat_api python main.py ingest
```

Useful compose facts (see [docker-compose.yml](docker-compose.yml)):
- `./chroma_db`, `./neo4j_data`, `./prompts`, `./static`, `./downloads`, `./atlases_pdfs`
  are **bind-mounts** — edit them on the host, see changes in the container.
- The container starts as root only to `chown` the writable mounts, then drops to the
  non-root `appuser` via `gosu` ([docker-entrypoint.sh](docker-entrypoint.sh)).
- Per-user SQLite history lives on a durable named volume (`conv_data`).

For a fully **air-gapped / ISRO on-prem** install (loading images from tarballs,
pre-caching models, no internet), follow [docs/start_offline.md](docs/start_offline.md).

---

## 11. Embed the chat widget in a website

The browser widget is served by the API at `/static/`. Add one script tag to any page:

```html
<script>
  window.GRAPH_RAG_CHAT_CONFIG = {
    apiBaseUrl: "https://your-host/chatapi",   // where the API is reachable
    title: "MOSDAC BOT",
    botName: "MOSDAC Assistant"
  };
</script>
<script src="https://your-host/static/graph-rag-chat-widget.js" defer></script>
```

- The MOSDAC-branded variant is `/static/mosdac-chat-widget.js` (sets ISRO defaults then
  loads the generic widget).
- Ready-made snippets and the nginx reverse-proxy config are in
  [deployments/](deployments/) (see [deployments/README.md](deployments/README.md) and
  [deployments/nginx/mosdac.conf](deployments/nginx/mosdac.conf)).
- To enable **per-user login (SSO)**, set the `CHAT_API_AUTH_ENABLED` / `KEYCLOAK_*`
  keys in `.env`; test the flow with [static/sso-demo.html](static/sso-demo.html).

---

## 12. Common problems & fixes

| Symptom | Cause / Fix |
|---------|-------------|
| `python main.py test` fails on embedder/Chroma/Neo4j/LLM | That service is down. Start Tabby (`:8080`), Ollama (`:11434`), Neo4j (`:7687`) and re-run. |
| `/ready` returns 503 | One of embedder/Chroma/Neo4j is unreachable. Check `docker compose logs`. |
| API container can't reach Tabby/Ollama | Inside compose, `localhost` ≠ host. Use `host.docker.internal` (already wired in compose). |
| Bot always answers "I don't have that information" | Stores are empty or the question is off-topic. Run `python main.py ingest`; check `GUARD_SCOPE_MIN_SIM` / `GUARD_GROUNDING_ACTION`. |
| Ingestion re-processes everything every time | Expected with `--force`. Otherwise ensure `ingest_manifest.json` persists between runs (don't delete it). |
| Scanned/image PDF yields no text | Install Tesseract + Poppler; for raster atlases set `DOCLING_FORCE_FULL_PAGE_OCR_DIRS`. |
| `bge-large` / embedding dimension errors | You changed the embedding model. Keep `OLLAMA_EMBEDDING_MODEL=bge-large`, or re-ingest with `--force` after a model change. |
| Startup error about rate limiting / slowapi | `slowapi` missing. `pip install slowapi`, or for local dev only set `CHAT_API_REQUIRE_RATE_LIMIT=false`. |
| Sessions lost across workers/replicas | Use `CHAT_API_SESSION_BACKEND=redis` + `CHAT_API_REDIS_URL`, and for multi-replica history `CHAT_API_CONV_STORE=postgres`. |
| Neo4j auth fails after changing the password | `NEO4J_AUTH` only initialises a **fresh** `./neo4j_data` volume. Reset the password or recreate `./neo4j_data`. |
| `RAGAS eval refuses to score` | Set `RAGAS_JUDGE_MODEL` to a model **stronger** than the generator. See [evaluation_plan.md](evaluation_plan.md). |
| Widget shows "something went wrong" on long answers | Use `/chat/stream` (the widget does) and ensure nginx isn't buffering SSE (`X-Accel-Buffering: no` is set by the API). |

---

## 13. Quick command cheat-sheet

```bash
# ── setup (Path A) ──────────────────────────────────────────────
python -m venv venv && source venv/bin/activate
pip install -r requirement.txt
python -m spacy download en_core_web_sm
cp .env.example .env            # then edit it

# ── external services ───────────────────────────────────────────
ollama pull bge-large && ollama serve          # embeddings  (:11434)
# (start Tabby ML on :8080 yourself)
docker compose up -d neo4j redis               # graph + sessions

# ── verify → ingest → run ───────────────────────────────────────
python main.py test                            # health-check every component
python main.py ingest                          # populate ChromaDB + Neo4j
python main.py build-communities               # (optional) GraphRAG summaries
python main.py chat                            # CLI REPL
uvicorn chat_api.main:app --port 8000 --reload # HTTP API + /docs

# ── full stack (Path B) ─────────────────────────────────────────
docker compose up -d --build
docker compose exec chat_api python main.py ingest
docker compose logs -f chat_api

# ── tests & evaluation ──────────────────────────────────────────
pytest -q
python main.py ragas-eval --smoke
```

---

**Next:** read [readme_main.md](readme_main.md) for the end-to-end architecture, or open
the `README.md` inside any folder for a deep dive into that part of the code.
