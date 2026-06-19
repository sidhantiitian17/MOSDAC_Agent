# MOSDAC GraphRAG Offline Setup Guide

Complete step-by-step instructions to run the MOSDAC ChatBot in an air-gapped environment (ISRO on-prem or similar).

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Component 1: Tabby ML (LLM Server)](#component-1-tabby-ml-llm-server)
4. [Component 2: Neo4j Graph Database](#component-2-neo4j-graph-database)
5. [Component 3: Embedding Model (BGE-Large)](#component-3-embedding-model-bge-large)
6. [Component 4: Application Stack (docker-compose or local)](#component-4-application-stack)
7. [Running the Full Stack](#running-the-full-stack)
8. [Health Checks](#health-checks)
9. [Troubleshooting](#troubleshooting)
10. [Next Steps: Ingestion](#next-steps-ingestion)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    MOSDAC GraphRAG System                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    │
│  │  FastAPI     │    │  Neo4j       │    │  ChromaDB    │    │
│  │  Chat API    │◄──►│  Knowledge   │    │  Vector      │    │
│  │  (port 8000) │    │  Graph       │    │  Store       │    │
│  │              │    │  (7687)      │    │  (in-proc)   │    │
│  └──────────────┘    └──────────────┘    └──────────────┘    │
│        ▲                                         ▲              │
│        │                                         │              │
│    HTTP Requests                    BGE-Large Embeddings       │
│        │                            (./models_cache)           │
│        │                                         │              │
│  ┌─────▼──────────────────────────────────────────────┐        │
│  │     Tabby ML (Qwen2-1.5B-Instruct)                 │        │
│  │     OpenAI-compatible endpoint                     │        │
│  │     (192.168.100.101:8080 or localhost:8080)      │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Component Roles

| Component | Purpose | Port | Docker? |
|-----------|---------|------|---------|
| **Tabby ML** | Qwen2-1.5B LLM (text generation) | 8080 | Separate container / ISRO host |
| **Neo4j** | Knowledge graph (entities, relationships) | 7687 | Docker (or standalone) |
| **ChromaDB** | Vector store (semantic search) | In-process | Embedded in FastAPI |
| **BGE-Large-en** | Embedding model (text → 1024-dim vectors) | In-process | Loaded by FastAPI |
| **FastAPI Chat API** | HTTP gateway, orchestrates retrieval | 8000 | Docker (or local) |

---

## Prerequisites

### Hardware Requirements
- **Minimum**: 8 GB RAM (4 GB for Tabby, 2 GB for Neo4j, 2 GB for FastAPI)
- **Recommended**: 16 GB+ RAM
- **Disk**: 10 GB free (for models, ChromaDB, Neo4j data)

### Software Requirements
- **Windows 11** (or Linux with WSL2 for this guide)
- **Docker Desktop** (if running containers) — ensure Docker daemon is running
- **Python 3.11+** (if running FastAPI locally instead of Docker)
- **Git Bash** or **PowerShell** (terminal)

### Network
- **Air-gapped**: No internet access after setup
- **Tabby ML**: Running on ISRO LAN at `192.168.100.101:8080` (or adjust for your network)
- **Neo4j & FastAPI**: Communicate over localhost or Docker network

---

## Component 1: Tabby ML (LLM Server)

Tabby ML is the **language model server** that generates chat responses. It runs on a separate machine (or can run on the same host but in a separate process/container).

### Option A: ISRO Network (Recommended for Air-Gapped)

If Tabby ML is already running on `192.168.100.101:8080` on the ISRO network:

1. **Verify connectivity** from your development machine:
   ```powershell
   # Test if Tabby is reachable
   curl -I http://192.168.100.101:8080/v1/models
   ```

2. **Update `.env`** to point to the ISRO Tabby server:
   ```env
   TABBY_BASE_URL=http://192.168.100.101:8080/v1
   TABBY_API_TOKEN=<your_isro_token_here>
   TABBY_MODEL=Qwen2-1.5B-Instruct
   TRANSFORMERS_OFFLINE=1
   ```

3. **Skip to [Component 2](#component-2-neo4j-graph-database)** — Tabby is already running.

### Option B: Local Tabby (Development Only)

If you want to run Tabby ML locally:

#### Via Docker (if you have the image)

```powershell
# Load the Tabby image (if you have tabby.tar)
docker load -i tabby.tar

# Run Tabby ML container
docker run -d `
  --name tabby_ml `
  -p 8080:8080 `
  -e TABBY_CUDA_COMPUTE_CAP=all `
  tabby:latest serve

# Verify it's running
curl http://localhost:8080/v1/models
```

#### Via Local Process (Ollama or similar)

If using Ollama:
```powershell
# Install Ollama (https://ollama.ai) or use pre-installed

# Pull and run Qwen2-1.5B
ollama pull qwen2:1.5b-instruct

# Update .env
# TABBY_BASE_URL=http://localhost:11434/v1
# TABBY_API_TOKEN=ollama
# TABBY_MODEL=qwen2:1.5b-instruct
```

**Summary**: Tabby ML must be reachable via HTTP before proceeding. Verify with:
```powershell
# This should return {"object": "list", "data": [...]}
curl http://192.168.100.101:8080/v1/models
```

---

## Component 2: Neo4j Graph Database

Neo4j is the **knowledge graph** that stores extracted entities and relationships.

### Option A: Docker Compose (Recommended)

The `docker-compose.yml` file starts both Neo4j and FastAPI together.

**What `docker-compose.yml` does:**
- Starts **Neo4j 5.18.0** container (port 7687 for Bolt protocol)
- Starts **FastAPI** container (port 8000)
- Creates shared volumes for data persistence
- Sets environment variables from `.env`
- Configures healthchecks

**Usage:**

```powershell
# Navigate to project root
cd d:\AI_agents

# Start both Neo4j and Chat API
docker compose up --build

# Or run in background
docker compose up -d --build

# View logs
docker compose logs -f

# Stop everything
docker compose down

# Stop but keep data
docker compose down -v
```

### Option B: Docker Load (Air-Gapped)

If you have `neo4j_5.tar` (Docker image tarball):

```powershell
# Load the Neo4j image from tar
docker load -i neo4j_5.tar

# Run Neo4j standalone (not via compose)
docker run -d `
  --name mosdac_neo4j `
  -p 7474:7474 `
  -p 7687:7687 `
  -v D:\AI_agents\neo4j_data:/data `
  -e NEO4J_AUTH=none `
  -e NEO4J_server_memory_heap_max__size=2G `
  neo4j:5.18.0

# Wait for startup (~30 seconds)
# Verify connectivity
curl http://localhost:7474
```

If running Neo4j standalone, you must start FastAPI separately (see [Component 4](#component-4-application-stack)).

### Option C: Neo4j Desktop / Local Installation

If Neo4j is installed locally:

```powershell
# Start the local Neo4j service
# (Depends on your installation — usually a service or direct binary)

# Update .env to point to it
# NEO4J_URI=bolt://localhost:7687
# NEO4J_USERNAME=neo4j
# NEO4J_PASSWORD=<your_password>
```

### Neo4j Browser UI (Optional)

Once Neo4j is running, you can explore the graph visually:

```
http://localhost:7474/browser
```

Default credentials (if NEO4J_AUTH=none, just click "Connect"):
- Username: `neo4j`
- Password: (any, ignored when NEO4J_AUTH=none)

---

## Component 3: Embedding Model (BGE-Large)

The **BGE-Large-en-v1.5** embedding model must be pre-downloaded and stored locally (no internet calls during inference).

### Step 1: Download the Model (One-Time)

This step requires **internet access** and takes ~10-15 minutes (1.3 GB download).

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Download the model to ./models_cache
.\.venv\Scripts\python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('BAAI/bge-large-en-v1.5', cache_folder='./models_cache')
print('BGE-Large-en-v1.5 downloaded successfully to ./models_cache/')
"
```

**Expected output:**
```
BGE-Large-en-v1.5 downloaded successfully to ./models_cache/
```

**Verify the download:**
```powershell
ls .\models_cache\models--BAAI--bge-large-en-v1.5\
# Should show: snapshots, blobs, refs, (metadata files)
```

### Step 2: Set Offline Mode (For Air-Gapped)

Edit `.env` to enable offline mode:

```env
TRANSFORMERS_OFFLINE=1
BGE_CACHE_DIR=./models_cache
BGE_MODEL_NAME=BAAI/bge-large-en-v1.5
```

This ensures the model loads only from the local cache and fails fast if it's missing.

### Step 3: Test the Embedding Model

```powershell
.\.venv\Scripts\python -c "
from graph_rag.embeddings import get_embedder

embedder = get_embedder()
result = embedder.embed_query('What is INSAT-3D?')
print(f'Embedding shape: {len(result)} dimensions')
print(f'Sample values: {result[:5]}')
"
```

**Expected output:**
```
Embedding shape: 1024 dimensions
Sample values: [0.123, -0.456, 0.789, ...]
```

---

## Component 4: Application Stack

### Option A: Docker Compose (Full Stack, Recommended)

**Prerequisites**: Neo4j and Tabby ML are running (from Components 1 & 2).

1. **Review `docker-compose.yml`**:
   ```powershell
   cat docker-compose.yml
   ```

2. **Start the stack**:
   ```powershell
   docker compose up --build
   ```

3. **Wait for health checks** (~30 seconds):
   - Neo4j: http://localhost:7474
   - FastAPI: http://localhost:8000/docs

4. **Stop the stack**:
   ```powershell
   docker compose down
   ```

### Option B: Local Python (For Development)

If you prefer to run FastAPI locally without Docker:

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirement.txt

# Ensure Neo4j is running (docker or standalone)
# Ensure Tabby ML is reachable

# Start FastAPI
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

**Output should show:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Visit: http://localhost:8000/docs (Swagger UI for testing)

---

## Running the Full Stack

### Quick Start (All-In-One)

```powershell
# 1. Ensure Tabby ML is running (reachable at 192.168.100.101:8080)

# 2. Load Neo4j image (if first time)
docker load -i neo4j_5.tar

# 3. Start the full stack
docker compose up --build

# 4. Open browser to
# - Chat API: http://localhost:8000
# - Swagger API Docs: http://localhost:8000/docs
# - Neo4j Browser: http://localhost:7474
```

### Step-by-Step (Component by Component)

**Terminal 1: Verify Tabby ML**
```powershell
curl http://192.168.100.101:8080/v1/models
# Should return: {"object":"list","data":[...]}
```

**Terminal 2: Start Neo4j**
```powershell
docker run -d `
  --name mosdac_neo4j `
  -p 7474:7474 `
  -p 7687:7687 `
  -v D:\AI_agents\neo4j_data:/data `
  -e NEO4J_AUTH=none `
  neo4j:5.18.0
```

**Terminal 3: Start FastAPI**
```powershell
cd d:\AI_agents
.\.venv\Scripts\Activate.ps1
pip install -r requirement.txt
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000
```

**Terminal 4: Test the system**
```powershell
# Wait ~30 seconds for all services to start, then:
curl http://localhost:8000/health
# Should return: {"status":"ok"}
```

---

## Health Checks

Verify all components are running:

### 1. Tabby ML
```powershell
curl -s http://192.168.100.101:8080/v1/models | ConvertFrom-Json
# Should show: object: "list", data contains Qwen2-1.5B-Instruct
```

### 2. Neo4j
```powershell
curl -s http://localhost:7474/
# Should return HTML (Neo4j browser page)
```

### 3. FastAPI
```powershell
curl -s http://localhost:8000/health | ConvertFrom-Json
# Should show: {"status": "ok", ...}
```

### 4. Full Integration Test
```powershell
# Via FastAPI Swagger UI
# POST /chat
# Body: {"message": "What is INSAT-3D?", "session_id": "test"}

curl -X POST http://localhost:8000/chat `
  -H "Content-Type: application/json" `
  -d '{"message":"What is INSAT-3D?","session_id":"test"}'

# Should return a response (may be empty if knowledge graph is unpopulated)
```

---

## Troubleshooting

### Problem: "Connection refused" to Tabby ML

**Symptom:**
```
ConnectionError: Failed to establish a new connection to http://192.168.100.101:8080
```

**Solution:**
1. Verify Tabby ML is running on the ISRO network
2. Check network connectivity: `ping 192.168.100.101`
3. Update `.env` with correct IP and port
4. If on VPN, ensure split tunneling allows access to 192.168.100.101

### Problem: Neo4j container won't start

**Symptom:**
```
docker: Error response from daemon: driver failed programming external connectivity
```

**Solution:**
```powershell
# Stop and remove conflicting container
docker stop mosdac_neo4j
docker rm mosdac_neo4j

# Try again with docker compose
docker compose up --build
```

### Problem: "Model not found" or "No module named 'rank_bm25'"

**Symptom:**
```
ImportError: rank-bm25 not installed
```

**Solution:**
```powershell
pip install -r requirement.txt
# or manually:
pip install rank-bm25>=0.2.2
```

### Problem: FastAPI returns "ChromaDB is empty"

**Symptom:**
```json
{"vector_context": "(no relevant passages found)"}
```

**Solution:**
Run ingestion to populate the vector store (see [Next Steps](#next-steps-ingestion)).

### Problem: "Transformers offline mode failed"

**Symptom:**
```
RuntimeError: Model 'BAAI/bge-large-en-v1.5' is not in the local cache
```

**Solution:**
1. Ensure `BGE_CACHE_DIR=./models_cache` in `.env`
2. Re-download the model (see [Component 3](#component-3-embedding-model-bge-large))
3. Verify the directory exists: `ls .\models_cache\models--BAAI--bge-large-en-v1.5\`

### Problem: High memory usage

**Symptom:**
- Qwen2-1.5B: 2–4 GB RAM
- Neo4j: 1–2 GB RAM (controlled by `NEO4J_server_memory_heap_max__size`)
- FastAPI + BGE: 2–3 GB RAM

**Solution:**
- Reduce Neo4j heap: `NEO4J_server_memory_heap_max__size=1G`
- Disable vector/graph indexing if not needed (advanced)

---

## Next Steps: Ingestion

Once the full stack is running, populate the knowledge graph and vector store:

### 1. Place PDF/HTML files

```powershell
# Copy your documents to the source folders
Copy-Item -Path "your_documents\*.pdf" -Destination "D:\AI_agents\downloads\"
Copy-Item -Path "your_atlases\*.pdf" -Destination "D:\AI_agents\atlases_pdfs\"
```

### 2. Run the ingestion pipeline

```powershell
.\.venv\Scripts\Activate.ps1
cd d:\AI_agents

# Full ingestion (vector + graph)
python main.py ingest

# Or skip certain steps:
python main.py ingest --skip-vector    # Graph only
python main.py ingest --skip-graph     # Vector only
```

**Expected output:**
```
Step 1/4 — discovering and loading documents
  Loaded 5 documents from downloads and atlases
Step 2/4 — splitting documents into chunks
  Split into 234 chunks (800 char, 100 overlap)
Step 3/4 — indexing chunks in ChromaDB
  Indexed 234 chunks (BGE embeddings: 1024-dim)
Step 4/4 — extracting and indexing graph
  Extracted 1,250 entities, 3,400 relationships
Ingestion complete. [0:23:45]
```

### 3. Test with a query

```powershell
python main.py chat

# Then type a question:
> What is the spatial resolution of INSAT-3D IMAGER?
```

---

## Configuration Summary

### `.env` for ISRO Air-Gapped Setup

```env
# ── LLM: Tabby ML (ISRO network) ──────────────────────────────
TABBY_BASE_URL=http://192.168.100.101:8080/v1
TABBY_API_TOKEN=<your_isro_token>
TABBY_MODEL=Qwen2-1.5B-Instruct

# ── Embeddings: BGE-Large-en-v1.5 (offline, local) ─────────────
BGE_MODEL_NAME=BAAI/bge-large-en-v1.5
BGE_CACHE_DIR=./models_cache
TRANSFORMERS_OFFLINE=1

# ── Neo4j (container or local) ────────────────────────────────
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=dummy
NEO4J_DATABASE=neo4j

# ── ChromaDB (in-process vector store) ────────────────────────
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=graph_rag

# ── Retrieval (hybrid search: vector + BM25) ──────────────────
TOP_K_VECTOR=5
TOP_K_GRAPH=10
TOP_K_BM25=5
GRAPH_DEPTH=2
HYBRID_RRF_K=60

# ── Data sources ──────────────────────────────────────────────
DOWNLOADS_DIR=D:/AI_agents/downloads
ATLASES_DIR=D:/AI_agents/atlases_pdfs

# ── Chat API (FastAPI) ────────────────────────────────────────
CHAT_API_TITLE=MOSDAC Graph RAG Chatbot
CHAT_API_BOT_NAME=MOSDAC Assistant
CHAT_API_HOST=0.0.0.0
CHAT_API_PORT=8000
CHAT_API_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1,http://192.168.100.50
```

---

## docker-compose.yml Purpose & Structure

The `docker-compose.yml` file orchestrates the entire offline stack:

```yaml
services:
  neo4j:
    # Neo4j 5.18.0 graph database
    # Port: 7687 (Bolt), 7474 (HTTP)
    # Data persists in ./neo4j_data/
    # Auth disabled (NEO4J_AUTH=none)
    
  chat_api:
    # FastAPI Chat Gateway
    # Port: 8000
    # Depends on Neo4j healthcheck
    # Volume mounts: chroma_db, models_cache, prompts
    
volumes:
  neo4j_logs:  # Neo4j log files
```

**Key features:**
- **Automatic startup order**: FastAPI waits for Neo4j healthcheck
- **Persistent data**: Volumes keep data across container restarts
- **Environment injection**: Reads `.env` for all credentials
- **Port mapping**: Makes services accessible on localhost

**Common docker-compose commands:**
```powershell
# Start and build
docker compose up --build

# Daemonize (background)
docker compose up -d

# View logs
docker compose logs -f chat_api
docker compose logs -f neo4j

# Stop containers (keep data)
docker compose stop

# Stop and remove (keep data volumes)
docker compose down

# Stop and wipe everything
docker compose down -v

# Rebuild a single service
docker compose build chat_api
docker compose up chat_api
```

---

## Offline Checklist

Before going fully offline, ensure:

- [ ] **Tabby ML running** at `192.168.100.101:8080` (verified with `/v1/models` endpoint)
- [ ] **BGE-Large downloaded** to `./models_cache/models--BAAI--bge-large-en-v1.5/`
- [ ] **TRANSFORMERS_OFFLINE=1** in `.env` (forces offline mode)
- [ ] **Neo4j image loaded** (`docker load -i neo4j_5.tar`) or running
- [ ] **Docker compose up** and all services healthy
- [ ] **Network connectivity test** (curl commands from Troubleshooting section pass)
- [ ] **Documents placed** in `./downloads/` and `./atlases_pdfs/`
- [ ] **Ingestion run** successfully (`python main.py ingest`)
- [ ] **Chat test query** returns a meaningful answer

---

## Additional Resources

- **Neo4j Docs**: https://neo4j.com/docs/
- **ChromaDB Docs**: https://docs.trychroma.com/
- **Sentence Transformers**: https://www.sbert.net/
- **FastAPI**: https://fastapi.tiangolo.com/
- **Docker Compose**: https://docs.docker.com/compose/

---

**Last Updated**: 2026-05-18  
**Version**: 1.0 (Offline Setup with Hybrid Search)
