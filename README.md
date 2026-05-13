# MOSDAC Agent + Graph RAG Chat API

This repository provides a modular AI assistant platform with:

- **Graph RAG chatbot** for document-grounded Q&A
- **FastAPI chat gateway** with session management and optional screenshot input
- **MOSDAC ordering agent** (LangGraph ReAct) for satellite data workflows
- **MCP tool server** exposing MOSDAC tools to MCP-compatible clients
- **Docker-based deployment** for Ollama, Neo4j, and API services

---

## Repository Architecture

### Core packages

- `graph_rag/`  
  Ingestion, embeddings, vector store, knowledge graph, retrieval, and RAG chain.
- `chat_api/`  
  FastAPI app factory, HTTP routes, request/response models, and session-backed chat service.
- `mosdac_agent/`  
  MOSDAC tool layer, LangGraph agent, MCP server, mock backend, and MOSDAC-specific API routes.

### Entry points

- `main.py` — CLI for Graph RAG (`ingest`, `chat`, `test`)
- `chat_api/main.py` — FastAPI app (`uvicorn chat_api.main:app ...`)
- `mosdac_agent/mcp_server.py` — MCP server (`python -m mosdac_agent.mcp_server`)
- `mosdac_agent/mock_mosdac.py` — mock MOSDAC backend
- `mosdac_agent/streamlit_app.py` — optional Streamlit UI

---

## Key Features

## 1) Graph RAG pipeline

- Loads source documents (HTML/PDF)
- Splits into chunks
- Embeds and stores chunks in ChromaDB
- Extracts entity relations and stores triples in Neo4j
- Performs hybrid retrieval (vector + graph context) for answering

## 2) Chat API gateway

- Factory-based FastAPI composition (`create_app`)
- `/chat` endpoint with conversation memory
- Optional screenshot payload validation + multimodal response path
- CORS, branding, and session backend configured via environment variables

## 3) MOSDAC agent stack

- Tool-backed order assistant:
  - search products
  - place order
  - check order status
  - list orders
- Pluggable execution mode:
  - local in-process tools
  - MCP transport mode
- Optional `/mosdac/*` route mounting in the same FastAPI app

## 4) MCP integration

- FastMCP server exposing MOSDAC tools
- Supports `stdio` and `streamable-http` transport

---

## Project Structure

```text
.
├── chat_api/
├── graph_rag/
├── mosdac_agent/
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

## Prerequisites

- Python 3.11+ (project metadata currently specifies `>=3.13` in `pyproject.toml`)
- Neo4j (for graph storage)
- ChromaDB (local persistence directory)
- Optional: Ollama/local OpenAI-compatible endpoint for Qwen models
- Optional: MOSDAC credentials for live ordering mode

## Install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirement.txt
```

## Configure environment

```bash
cp .env.example .env
```

Then update required values in `.env` (API keys, Neo4j password, paths, model endpoints, etc.).

---

## Run Modes

## A) Graph RAG CLI

```bash
python main.py ingest   # run ingestion pipeline
python main.py chat     # interactive CLI chat
python main.py test     # component smoke checks
```

## B) FastAPI chat server

```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

Primary endpoints:

- `GET /health`
- `GET /config`
- `POST /chat`
- `DELETE /chat/{session_id}`

## C) Enable MOSDAC routes

Set in `.env`:

```dotenv
MOSDAC_ENABLE_MOSDAC_ENDPOINT=true
```

Additional endpoints (default prefix `/mosdac`):

- `GET /mosdac/health`
- `GET /mosdac/config`
- `POST /mosdac/chat`
- `DELETE /mosdac/chat/{session_id}`

## D) Run MCP server

```bash
python -m mosdac_agent.mcp_server
```

For HTTP transport, configure MCP settings in `.env` (`MCP_TRANSPORT=streamable-http`, host, port).

## E) Optional mock backend and Streamlit UI

```bash
python -m mosdac_agent.mock_mosdac
streamlit run mosdac_agent/streamlit_app.py
```

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
pytest tests/test_mosdac_tools.py -v
pytest tests/test_mosdac_integration.py -v
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

### MOSDAC agent

- `MOSDAC_*` for backend auth, endpoint mounting, branding, safety limits
- `AGENT_*` for LLM endpoint/model/temperature and tool mode
- `MCP_*` for MCP host/port/transport

Refer to `.env.example` and `deployments/*.env` templates for full values.

---

## Documentation Map

- `documentation.md` — broad beginner-focused end-to-end guide
- `guide.md` — MOSDAC agent implementation/testing details
- `MOSDAC_Chatbot_Integration_Guide.md` — portal integration details
- `docker_guide.md` — detailed Docker operations and troubleshooting
- `deployments/README.md` — per-domain deployment customization
