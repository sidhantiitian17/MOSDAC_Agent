# Graph RAG Chat API

This repository provides a modular AI assistant platform with:

- **Graph RAG chatbot** for document-grounded Q&A
- **FastAPI chat gateway** with session management and optional screenshot input
- **Docker-based deployment** for Ollama, Neo4j, and API services

---

## Repository Architecture

### Core packages

- `graph_rag/`  
  Ingestion, embeddings, vector store, knowledge graph, retrieval, and RAG chain.
- `chat_api/`  
  FastAPI app factory, HTTP routes, request/response models, and session-backed chat service.

### Entry points

- `main.py` — CLI for Graph RAG (`ingest`, `chat`, `test`)
- `chat_api/main.py` — FastAPI app (`uvicorn chat_api.main:app ...`)

---

## Key Features

### 1) Graph RAG pipeline

- Loads source documents (HTML/PDF)
- Splits into chunks
- Embeds and stores chunks in ChromaDB
- Extracts entity relations and stores triples in Neo4j
- Performs hybrid retrieval (vector + graph context) for answering

### 2) Chat API gateway

- Factory-based FastAPI composition (`create_app`)
- `/chat` endpoint with conversation memory
- Optional screenshot payload validation + multimodal response path
- CORS, branding, and session backend configured via environment variables

---

## Project Structure

```text
.
├── chat_api/
├── graph_rag/
├── deployments/
├── prompts/
├── static/
├── tests/
├── docker-compose.yml
├── Dockerfile.api
├── main.py
├── requirement.txt
└── .env.example
```

---

## Setup

### Prerequisites

- Python 3.11+ (as declared in `pyproject.toml`)
- Neo4j (for graph storage)
- ChromaDB (local persistence directory)
- Optional: Ollama/local OpenAI-compatible endpoint for Qwen models

### Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirement.txt
```

### Configure environment

```bash
cp .env.example .env
```

Then update required values in `.env` (API keys, Neo4j password, paths, model endpoints, etc.).

---

## Run Modes

### A) Graph RAG CLI

```bash
python main.py ingest   # run ingestion pipeline
python main.py chat     # interactive CLI chat
python main.py test     # component smoke checks
```

### B) FastAPI chat server

```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

Primary endpoints:

- `GET /health`
- `GET /config`
- `POST /chat`
- `DELETE /chat/{session_id}`

---

## Docker Deployment

Start full stack (as defined in `docker-compose.yml`):

```bash
docker compose up -d --build
```

Included services:

- `ollama`
- `neo4j`
- `chat_api`

Also see:

- `docker_guide.md`
- `deployments/README.md`
- `deployments/widget-snippets/`

---

## Testing

Run all tests:

```bash
pytest -q
```

Useful targeted suites:

```bash
pytest tests/test_chat_api.py -v
pytest tests/test_pipeline.py -v
```

---

## Configuration Overview

### Graph RAG / embeddings / stores

- `LONGCAT_*`
- `QWEN_*`
- `NVIDIA_*`, `GEMINI_*`
- `NEO4J_*`
- `CHROMA_*`
- `DOWNLOADS_DIR`, `ATLASES_DIR`
- `SYSTEM_PROMPT_PATH`

### Chat API (`CHAT_API_*`)

- App title/version/bot name
- CORS policy
- Session backend (`memory` or `redis`)
- Screenshot toggle and max payload size

Refer to `.env.example` and `deployments/*.env` templates for full values.

---

## Documentation Map

- `documentation.md` — broad beginner-focused end-to-end guide
- `MOSDAC_Chatbot_Integration_Guide.md` — portal integration details
- `docker_guide.md` — detailed Docker operations and troubleshooting
- `deployments/README.md` — per-domain deployment customization
