# Offline Conversion Plan — MOSDAC GraphRAG Chatbot

**Goal:** Convert the entire pipeline (ingestion → knowledge graph → retrieval → chat) to run completely offline using Tabby ML as the LLM, BAAI/bge-small-en-v1.5 as the local embedding model, and Neo4j 5.18.0 from a pre-loaded Docker image.

---

## Current vs Target Architecture

| Component | Current (Online/Mixed) | Target (Fully Offline) |
|---|---|---|
| LLM (graph_rag chain) | LongCat cloud API | Tabby ML (OpenAI-compatible) |
| LLM (chat_api) | Qwen via Ollama | Tabby ML (OpenAI-compatible) |
| LLM (mosdac_agent) | Qwen via Ollama | Tabby ML (OpenAI-compatible) |
| Embeddings (ingestion) | NVIDIA NIM cloud API | BAAI/bge-small-en-v1.5 (local) |
| Embeddings (retrieval) | NVIDIA NIM cloud API | BAAI/bge-small-en-v1.5 (local) |
| Vector Store | ChromaDB (local) | ChromaDB (unchanged, already local) |
| Knowledge Graph | Neo4j any version | Neo4j 5.18.0 (from neo4j_5.tar) |
| Neo4j Auth | `neo4j/mosdac4j` | `NEO4J_AUTH=none` (no auth needed) |

---

## Two Target Environments

### Home Development Setup
```
Tabby ML: http://localhost:8080/v1
Token:    your_tabby_token_here
Model:    Qwen2-1.5B-Instruct
Neo4j:    bolt://localhost:7687  (docker on same machine, NEO4J_AUTH=none)
BGE:      BAAI/bge-small-en-v1.5 (loaded from ./models_cache)
Internet: Available for first-time model download only
```

### ISRO Production Setup
```
Tabby ML: http://192.168.100.101:8080/v1
Token:    isro_wala_token
Model:    Qwen2-1.5B-Instruct
Neo4j:    bolt://localhost:7687  (docker loaded from neo4j_5.tar)
BGE:      BAAI/bge-small-en-v1.5 (must be pre-downloaded, no internet)
Internet: ZERO — completely air-gapped after cloning repo
```

---

## Files Overview

### New Files to Create

| File | Purpose |
|------|---------|
| `graph_rag/embeddings/bge_embedder.py` | Local HuggingFace BGE embedding model |
| `graph_rag/llm/tabby_client.py` | Tabby ML LLM client (OpenAI-compatible) |

### Files to Modify

| File | What Changes |
|------|-------------|
| `graph_rag/config.py` | Add Tabby, BGE, and Neo4j auth-none settings |
| `graph_rag/ingestion/pipeline.py` | Switch NVIDIA NIM → BGE; remove cloud rate-limit delays |
| `graph_rag/retrieval/vector_retriever.py` | Switch NVIDIA NIM → BGE |
| `graph_rag/chain/graph_rag_chain.py` | Switch LongCat → Tabby |
| `graph_rag/vector_store/chroma_store.py` | Remove Gemini-specific retry/rate-limit logic |
| `graph_rag/knowledge_graph/neo4j_store.py` | Support `NEO4J_AUTH=none` connection |
| `chat_api/main.py` | Switch Qwen → Tabby |
| `mosdac_agent/config.py` | Default LLM settings to Tabby |
| `mosdac_agent/agent.py` | Add `streaming=True` (required by Tabby) |
| `docker-compose.yml` | Remove Ollama/vLLM; update Neo4j to 5.18.0; point to Tabby |
| `.env` | Add Tabby + BGE vars; two-environment comment blocks |
| `requirement.txt` | Remove cloud SDKs; add sentence-transformers + langchain-huggingface |

---

## Phase 1 — BGE Local Embedding Model

### 1.1 Create `graph_rag/embeddings/bge_embedder.py`

```python
"""Offline local embedder using BAAI/bge-small-en-v1.5 via sentence-transformers.

Runs 100% offline once the model is cached. Set BGE_CACHE_DIR in .env to
point at the pre-downloaded model directory. Set TRANSFORMERS_OFFLINE=1 in
production to prevent any network calls.
"""
from __future__ import annotations

from functools import lru_cache
from langchain_core.embeddings import Embeddings


class BGEEmbedder(Embeddings):
    """Local BGE embedder — no network calls after first download."""

    def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers not installed. Run: pip install sentence-transformers"
            ) from exc
        self._model = SentenceTransformer(model_name, cache_folder=cache_dir)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, convert_to_numpy=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text], convert_to_numpy=True)[0].tolist()


@lru_cache(maxsize=1)
def get_embedder() -> Embeddings:
    """Return singleton BGE embedder. Config-driven — reads .env."""
    from graph_rag.config import settings as _s
    return BGEEmbedder(model_name=_s.bge_model_name, cache_dir=_s.bge_cache_dir or None)
```

### 1.2 Modify `graph_rag/config.py` — add BGE fields

Add these two fields to the `Settings` class:
```python
# BGE local embeddings (offline)
bge_model_name: str = "BAAI/bge-small-en-v1.5"
bge_cache_dir: str = "./models_cache"
```

### 1.3 Modify `graph_rag/ingestion/pipeline.py`

Replace the two lines importing `nvidia_embedder`:
```python
# BEFORE
from graph_rag.embeddings.nvidia_embedder import get_embedder

# AFTER
from graph_rag.embeddings.bge_embedder import get_embedder
```

Remove `_EMBED_BATCH_DELAY` sleeps (module-level constants and `time.sleep()` calls) —
local models need no rate limiting. Replace the batch loop with a single
`store.add_documents(chunks)` call; sentence-transformers handles batching internally.

### 1.4 Modify `graph_rag/retrieval/vector_retriever.py`

Line 23 — replace the import:
```python
# BEFORE
from graph_rag.embeddings.nvidia_embedder import get_embedder

# AFTER
from graph_rag.embeddings.bge_embedder import get_embedder
```

### 1.5 Simplify `graph_rag/vector_store/chroma_store.py`

The Gemini rate-limiting retry loop (lines 76–108) is replaced with a plain
`self._store.add_documents(documents=new_docs, ids=new_ids)` call.
BGE is a local call and never returns HTTP 429, so all retry/backoff logic is dead code.

---

## Phase 2 — Tabby ML LLM Client

### 2.1 Create `graph_rag/llm/tabby_client.py`

```python
"""LangChain client for Tabby ML (OpenAI-compatible endpoint).

Tabby ML REQUIRES streaming=True to avoid connection timeout — confirmed in test_tabby.py.
Both home dev and ISRO production share this client; only the base_url and token differ.

Set in .env:
    TABBY_BASE_URL=http://localhost:8080/v1          # home dev
    TABBY_API_TOKEN=your_tabby_token_here
    TABBY_MODEL=Qwen2-1.5B-Instruct

    # ISRO production (uncomment to switch):
    # TABBY_BASE_URL=http://192.168.100.101:8080/v1
    # TABBY_API_TOKEN=isro_wala_token
"""
from __future__ import annotations

from functools import lru_cache
from langchain_openai import ChatOpenAI
from graph_rag.config import settings


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.1, max_tokens: int = 2048) -> ChatOpenAI:
    """Return a ChatOpenAI instance pointed at the local Tabby ML endpoint."""
    return ChatOpenAI(
        model=settings.tabby_model,
        api_key=settings.tabby_api_token,
        base_url=settings.tabby_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=True,  # Tabby requires streaming=True or calls time out
    )
```

### 2.2 Modify `graph_rag/config.py` — add Tabby fields

Add to the `Settings` class:
```python
# Tabby ML LLM (replaces Ollama / LongCat / vLLM)
tabby_base_url: str = "http://localhost:8080/v1"
tabby_api_token: str = "your_tabby_token_here"
tabby_model: str = "Qwen2-1.5B-Instruct"
```

Keep existing `qwen_*` and `longcat_*` fields for backwards compatibility — they are
simply unused once the chain and chat_api import from `tabby_client.py`.

### 2.3 Modify `graph_rag/chain/graph_rag_chain.py` — line 11

```python
# BEFORE
from graph_rag.llm.longcat_client import get_llm

# AFTER
from graph_rag.llm.tabby_client import get_llm
```

### 2.4 Modify `chat_api/main.py` — line 63

```python
# BEFORE
from graph_rag.llm.qwen_client import get_llm

# AFTER
from graph_rag.llm.tabby_client import get_llm
```

### 2.5 Modify `mosdac_agent/config.py` — update LLM defaults

```python
# BEFORE
agent_llm_base_url: str = "http://localhost:11434/v1"  # Ollama OpenAI-compat
agent_llm_model: str = "qwen2.5:32b"
agent_llm_api_key: str = "ollama"

# AFTER
agent_llm_base_url: str = "http://localhost:8080/v1"   # Tabby ML
agent_llm_model: str = "Qwen2-1.5B-Instruct"
agent_llm_api_key: str = "your_tabby_token_here"
```

These remain overridable via `.env` vars `AGENT_LLM_BASE_URL`, `AGENT_LLM_MODEL`,
`AGENT_LLM_API_KEY` so the ISRO token can be injected without code changes.

### 2.6 Modify `mosdac_agent/agent.py` — add streaming=True

In `_build_default_llm()` (lines 64–71):
```python
# BEFORE
return ChatOpenAI(
    ...
    streaming=False,
)

# AFTER
return ChatOpenAI(
    ...
    streaming=True,  # Tabby ML requires streaming or calls time out
)
```

---

## Phase 3 — Neo4j Auth-None Support

### Docker run command (both environments)
```bash
# Load image — ISRO production only (home dev pulls from Docker Hub)
docker load -i neo4j_5.tar

# Start container — same command for both environments
docker run -d \
  --name mosdac_graph_home \
  -p 7474:7474 \
  -p 7687:7687 \
  -v D:\AI_agents\neo4j_data:/data \
  -e NEO4J_AUTH=none \
  neo4j:5.18.0
```

With `NEO4J_AUTH=none`, Neo4j ignores any credentials the client sends.
The existing `neo4j_store.py` code connecting with `auth=(username, password)` continues
to work — Neo4j simply does not verify the credentials. No change is needed in
`neo4j_store.py` as long as `.env` still provides placeholder username/password values.

### Update `.env` Neo4j block

```env
# Neo4j (NEO4J_AUTH=none in container — any placeholder creds work)
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=dummy
NEO4J_DATABASE=neo4j
```

Inside Docker Compose, the `chat_api` container talks to the `neo4j` container via
the service name as host. The compose file already overrides `NEO4J_URI` to
`bolt://neo4j:7687` in the environment block.

---

## Phase 4 — docker-compose.yml Rewrite

### Remove entirely
- `ollama:` service block (profiles: [ollama])
- `vllm:` service block (profiles: [vllm])
- `ollama_data:` named volume
- `hf_cache:` named volume
- `depends_on` entries for `ollama` and `vllm` under `chat_api`

### Update `neo4j:` service
```yaml
neo4j:
  image: neo4j:5.18.0               # was: neo4j:2025.04.0-community
  container_name: mosdac_neo4j
  restart: unless-stopped
  ports:
    - "7474:7474"
    - "7687:7687"
  environment:
    NEO4J_AUTH: none                 # was: "neo4j/${NEO4J_PASSWORD}"
    NEO4J_server_memory_heap_max__size: "2G"
  volumes:
    - ./neo4j_data:/data             # bind mount — data persists across container recreates
    - neo4j_logs:/logs
  healthcheck:
    test: ["CMD-SHELL", "wget -qO- http://localhost:7474 > /dev/null 2>&1 || exit 1"]
    interval: 15s
    timeout: 10s
    retries: 10
    start_period: 60s
```

### Update `chat_api:` service
```yaml
chat_api:
  build:
    context: .
    dockerfile: Dockerfile.api
  container_name: mosdac_chat_api
  restart: unless-stopped
  ports:
    - "8000:8000"
  env_file:
    - .env
  environment:
    # Tabby ML runs as a separate container outside compose — reach it via host
    TABBY_BASE_URL: ${TABBY_BASE_URL:-http://host.docker.internal:8080/v1}
    TABBY_API_TOKEN: ${TABBY_API_TOKEN:-your_tabby_token_here}
    TABBY_MODEL: ${TABBY_MODEL:-Qwen2-1.5B-Instruct}
    BGE_CACHE_DIR: /app/models_cache
    NEO4J_URI: bolt://neo4j:7687     # always use service name inside compose
  extra_hosts:
    - "host.docker.internal:host-gateway"  # Linux: lets container reach host Tabby
  volumes:
    - ./chroma_db:/app/chroma_db
    - ./prompts:/app/prompts
    - ./models_cache:/app/models_cache:ro  # pre-downloaded BGE model
    - ${DOWNLOADS_DIR:-./downloads}:/app/downloads:ro
    - ${ATLASES_DIR:-./atlases_pdfs}:/app/atlases:ro
  depends_on:
    neo4j:
      condition: service_healthy
```

### Updated `volumes:` block
```yaml
volumes:
  neo4j_logs:
  # neo4j_data is a bind mount (./neo4j_data) — not a named volume
  # ollama_data and hf_cache removed — Tabby runs externally
```

---

## Phase 5 — .env Restructure

Replace the current `.env` content with a clean two-environment layout:

```env
# ═══════════════════════════════════════════════════════════
# MOSDAC GraphRAG — Offline Configuration
# ═══════════════════════════════════════════════════════════

# ── HOME DEVELOPMENT SETUP ──────────────────────────────────
TABBY_BASE_URL=http://localhost:8080/v1
TABBY_API_TOKEN=your_tabby_token_here
TABBY_MODEL=Qwen2-1.5B-Instruct

# ── ISRO PRODUCTION SETUP (uncomment + comment home block) ──
# TABBY_BASE_URL=http://192.168.100.101:8080/v1
# TABBY_API_TOKEN=isro_wala_token
# TABBY_MODEL=Qwen2-1.5B-Instruct
# TRANSFORMERS_OFFLINE=1     # prevents any HuggingFace network calls
# HF_DATASETS_OFFLINE=1

# ── BGE Local Embeddings ─────────────────────────────────────
BGE_MODEL_NAME=BAAI/bge-small-en-v1.5
BGE_CACHE_DIR=./models_cache   # pre-downloaded model lives here

# ── Neo4j (NEO4J_AUTH=none in container — creds are ignored) ─
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=dummy
NEO4J_DATABASE=neo4j

# ── System prompt ─────────────────────────────────────────────
SYSTEM_PROMPT_PATH=./prompts/system_prompt.txt

# ── ChromaDB ──────────────────────────────────────────────────
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=graph_rag

# ── OCR (Tesseract + Poppler) — fallback for image-only PDFs ──
TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
POPPLER_PATH=C:/Users/HP/Downloads/Release-24.08.0-0/poppler-24.08.0/Library/bin

# ── Data sources ──────────────────────────────────────────────
DOWNLOADS_DIR=D:/AI_agents/downloads
ATLASES_DIR=D:/AI_agents/atlases_pdfs

# ── Chunking ──────────────────────────────────────────────────
CHUNK_SIZE=800
CHUNK_OVERLAP=100

# ── Retrieval ─────────────────────────────────────────────────
TOP_K_VECTOR=5
TOP_K_GRAPH=10
GRAPH_DEPTH=2
```

---

## Phase 6 — requirements.txt Update

### Remove (cloud-only, no longer needed)
```
langchain-google-genai>=2.0
langchain-nvidia-ai-endpoints>=0.3
google-genai
```

Keep `anthropic` (used by optional MCP tooling).

### Add (offline / local)
```
sentence-transformers>=2.7.0
langchain-huggingface>=0.1.0
torch                           # CPU build sufficient for BGE-small; GPU auto-detected
```

### Resulting `requirement.txt` (offline-ready)
```
# Core LangChain
langchain>=0.3
langchain-community>=0.3
langchain-core>=0.3
langchain-openai>=0.2
langchain-text-splitters>=0.3
langchain-chroma>=0.1
langchain-neo4j>=0.1
langchain-huggingface>=0.1.0

# LLM SDKs
openai>=1.0
anthropic

# Local embeddings (replaces Gemini + NVIDIA NIM)
sentence-transformers>=2.7.0
torch

# Vector DB
chromadb>=0.5

# Graph DB
neo4j>=5.0

# Document loaders
pypdf>=4.0
pymupdf>=1.24
pytesseract>=0.3
pdf2image>=1.17
Pillow>=10.0
beautifulsoup4>=4.12
lxml>=5.0
unstructured>=0.14

# NLP — entity & relationship extraction
spacy>=3.7
# After install: python -m spacy download en_core_web_sm

# Web framework
fastapi>=0.100
uvicorn[standard]>=0.23

# Utilities
python-dotenv
pydantic>=2.0
pydantic-settings>=2.0
tqdm
typer>=0.12

# Testing
pytest>=8.0
pytest-mock>=3.12
httpx>=0.27

# MOSDAC agent stack
langgraph>=0.2
fastmcp>=2.5
streamlit>=1.36
requests>=2.31
```

---

## Phase 7 — Pre-Download BGE Model (One-Time, Requires Internet)

Run this **before** taking the system offline. Populates `./models_cache` with BGE
model weights so all future runs work without internet.

```bash
# Install sentence-transformers first
pip install sentence-transformers

# Download and cache the model
python -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('BAAI/bge-small-en-v1.5', cache_folder='./models_cache')
vecs = model.encode(['test sentence'])
print(f'BGE model ready. Embedding dim={vecs.shape[1]}')  # Should print 384
print('Cached to: ./models_cache')
"
```

For ISRO production: copy the entire `./models_cache` folder to the server alongside
the rest of the repo. Then set `TRANSFORMERS_OFFLINE=1` in `.env` to prevent any
accidental network calls.

Also pre-download the spaCy model while online:
```bash
python -m spacy download en_core_web_sm
```

---

## Verification Checklist

### Step 1 — Start Neo4j
```bash
# ISRO production only — skip on home dev if neo4j:5.18.0 is already pulled
docker load -i neo4j_5.tar

# Both environments
docker run -d \
  --name mosdac_graph_home \
  -p 7474:7474 \
  -p 7687:7687 \
  -v D:\AI_agents\neo4j_data:/data \
  -e NEO4J_AUTH=none \
  neo4j:5.18.0

# Confirm Neo4j is up (wait ~20 seconds)
# Browser: http://localhost:7474
```

### Step 2 — Verify Tabby ML Connection
```bash
python test_tabby.py
# Expected: streaming response printed character-by-character
# Expected final line: ✅ Connection 100% Successful! Pipeline Ready!
```

### Step 3 — Verify Neo4j + Tabby Together
```bash
python test_graph.py
# Expected:
# ✅ Neo4j Connection & Write Test 100% Successful!
# ✅ Tabby Connection 100% Successful!
# 🚀 BINGO! Tumhara poora offline backend setup completely taiyar hai!
```

### Step 4 — Verify BGE Embedder
```bash
python -c "
from graph_rag.embeddings.bge_embedder import get_embedder
emb = get_embedder()
v = emb.embed_query('MOSDAC satellite data')
print(f'BGE embedding dim: {len(v)}')  # Must be 384
print('BGE embedder OK')
"
```

### Step 5 — Run Ingestion Pipeline
```bash
python -m graph_rag.ingestion.pipeline
# Expected output:
# Step 1/4 — discovering and loading documents
# Step 2/4 — splitting N documents into chunks
# Step 3/4 — embedding & storing in ChromaDB  (fast, no sleep delays)
# Step 4/4 — extracting triples & storing in Neo4j
# Ingestion summary: documents loaded: N, chunks indexed: M, ...
```

### Step 6 — Start Chat API
```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload

# Test a chat question
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"test\", \"message\": \"What is MOSDAC?\"}"
```

### Step 7 — Full Docker Compose (optional)
```bash
docker compose up --build
docker compose ps   # all services should show "healthy"
```

---

## Implementation Order

Execute phases in this sequence to avoid broken intermediate states:

1. **Phase 6** — Update `requirement.txt`, then `pip install -r requirement.txt`
2. **Phase 7** — Pre-download BGE model + spaCy model while internet is still available
3. **Phase 1** — Create `bge_embedder.py` + update `config.py` + `pipeline.py` + `vector_retriever.py` + `chroma_store.py`
4. **Phase 2** — Create `tabby_client.py` + update `graph_rag_chain.py` + `chat_api/main.py` + `mosdac_agent/config.py` + `mosdac_agent/agent.py`
5. **Phase 3** — Update `.env` Neo4j block
6. **Phase 4** — Rewrite `docker-compose.yml`
7. **Phase 5** — Restructure `.env` with two environment blocks
8. **Verification** — Run checklist steps 1–7

---

## Architecture Diagram (Post-Conversion)

```
┌─────────────────────────────────────────────────────────────┐
│                   OFFLINE STACK                             │
│                                                             │
│  ┌──────────────┐    ┌──────────────────────────────────┐  │
│  │  Tabby ML    │    │   FastAPI Chat Gateway            │  │
│  │  (Docker)    │◄───│   chat_api/main.py               │  │
│  │  :8080/v1    │    │   graph_rag/chain/graph_rag_chain │  │
│  │  Qwen2-1.5B  │    └───────────┬──────────────────────┘  │
│  └──────────────┘                │                          │
│                         ┌────────┴──────────┐              │
│                         │   HybridRetriever │              │
│                         └────────┬──────────┘              │
│                       ┌──────────┴──────────┐              │
│                       │                     │              │
│               ┌───────▼────────┐  ┌────────▼────────┐     │
│               │ VectorRetriever│  │  GraphRetriever  │     │
│               │ ChromaDB       │  │  Neo4j 5.18.0    │     │
│               │ (./chroma_db)  │  │  (Docker :7687)  │     │
│               └───────▲────────┘  └──────────────────┘     │
│                       │                                     │
│               ┌───────┴────────┐                           │
│               │  BGEEmbedder   │                           │
│               │ bge-small-en   │                           │
│               │ ./models_cache │                           │
│               └────────────────┘                           │
│                                                             │
│  IngestionPipeline:                                         │
│    load_docs → split → BGEEmbedder → ChromaDB              │
│                     → spaCy NER   → Neo4j 5.18.0           │
└─────────────────────────────────────────────────────────────┘
```

---

## Notes and Gotchas

- **Tabby streaming requirement**: `streaming=True` must be set on every `ChatOpenAI` instance that talks to Tabby. Non-streaming calls time out silently. Confirmed by `test_tabby.py` which uses `.stream()` exclusively.

- **BGE embedding dimension**: `BAAI/bge-small-en-v1.5` produces **384-dimensional** vectors. If an existing ChromaDB collection was built with Gemini (768-dim) or NVIDIA NIM (1024-dim), the collection must be reset before re-ingesting:
  ```bash
  python -c "from graph_rag.vector_store.chroma_store import ChromaStore; ChromaStore().reset()"
  ```
  Then re-run the ingestion pipeline.

- **Neo4j version lock**: The container must use `neo4j:5.18.0` to match the `.tar` file. Do not upgrade without a new `.tar` archive.

- **`models_cache` folder**: Must be transferred to ISRO alongside the repo. It is ~130 MB. 

- **`neo4j_data` folder**: Already present at `D:\AI_agents\neo4j_data`. Transfer it separately when migrating environments to preserve the ingested knowledge graph.

- **ISRO offline flags**: Set `TRANSFORMERS_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` in `.env` before starting in production. With these set, `sentence-transformers` loads from `models_cache` only and raises a clear error if the model is missing rather than silently trying (and failing) to download.

- **spaCy offline**: The entity extractor uses `en_core_web_sm`. Verify it is installed (not just downloaded) before going offline:
  ```bash
  python -c "import spacy; spacy.load('en_core_web_sm'); print('spaCy model OK')"
  ```
