# Complete Beginner's Guide to the AI Agents Codebase

Welcome! This guide explains everything in plain language — no experience required.
Think of this as a friendly tour through a LEGO set: we will look at each brick,
explain what it does, and show you how to put them together.

---

## Table of Contents

1. [Glossary — "What does that word mean?"](#1-glossary)
2. [Big Picture — how the three features fit together](#2-big-picture)
3. [Installation — getting the project running on your computer](#3-installation)
4. [Feature 1 — GraphRAG Chatbot](#4-feature-1--graphrag-chatbot)
5. [Feature 2 — MOSDAC Web Portal Integration](#5-feature-2--mosdac-web-portal-integration)
6. [Feature 3 — MCP Tool Feature](#6-feature-3--mcp-tool-feature)
7. [How to add new features](#7-how-to-add-new-features)
8. [Running all tests](#8-running-all-tests)
9. [File-by-file reference](#9-file-by-file-reference)

---

## 1. Glossary

Before anything else, here are the ten words that appear everywhere.
Understanding these makes the rest of the guide easy.

| Word | Simple explanation |
|------|--------------------|
| **LLM** | A large language model — the AI brain (like ChatGPT). It reads text and writes replies. |
| **RAG** | Retrieval-Augmented Generation. Instead of guessing, the AI first *searches* a database for relevant facts, then writes an answer based on those facts. Much more accurate. |
| **Knowledge Graph** | A database that stores *relationships*: "INSAT-3D `has_sensor` Imager". Think of it as a map of connected facts. |
| **Vector Store** | A database that stores documents as lists of numbers (embeddings) so you can find the most *similar* document to a question. |
| **FastAPI** | A Python library for building web APIs (the thing a browser or app talks to). |
| **Endpoint** | A URL the server listens on, e.g. `POST /chat`. |
| **MCP** | Model Context Protocol — Anthropic's standard way for AI assistants to call external tools. Like a USB standard, but for AI tools. |
| **Agent** | An AI that can *decide* to call tools, look at the result, and decide again — repeatedly — until it has a complete answer. |
| **Session** | One ongoing conversation. Each user gets a unique session ID so the server remembers their previous messages. |
| **SFTP** | Secure File Transfer Protocol — a way to download files from a server, like a private FTP. MOSDAC delivers ordered satellite data here. |

---

## 2. Big Picture

Imagine three Lego sets that can be played separately or connected:

```
+------------------------------------------------------+
|                  Your Browser or App                  |
+---------------------------+--------------------------+
                            |  HTTP requests
          +-----------------v-----------------+
          |    FastAPI Web Server              |   chat_api/main.py
          |   (port 8000)                      |
          |                                    |
          |  /chat  --------->  Feature 1      |  GraphRAG Chatbot
          |  /mosdac/chat ---->  Feature 2     |  MOSDAC Agent
          +--------------------+---------------+
                   |           |
          +--------v--+   +----v-------------------+
          | Neo4j     |   |   MOSDAC Agent          |
          | ChromaDB  |   |   (LangGraph ReAct)     |
          | (Feature 1|   |                         |
          | databases)|   |  -----> Feature 3 (MCP) |
          +-----------+   +-------------------------+
```

- **Feature 1** (GraphRAG): You ask a question, the server searches Neo4j + ChromaDB, and the LLM answers from real data.
- **Feature 2** (MOSDAC Portal): You order satellite data in plain English, the AI agent calls tools, and the order is placed on MOSDAC.
- **Feature 3** (MCP): The same four tools (search, order, status, list) are published as an MCP server so *any* MCP-compatible AI (Claude Desktop, etc.) can call them.

---

## 3. Installation

### Step 1 — Install Python

You need Python 3.11 or 3.12. Check what you have:

```bash
python --version
```

Download from https://www.python.org/downloads/ if needed.

### Step 2 — Get the code

If you received a ZIP, unzip it. If using git:

```bash
git clone <your-repo-url>
cd AI_agents
```

### Step 3 — Create a virtual environment

A virtual environment keeps this project's packages separate from your system.

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\activate

# Mac / Linux
python -m venv .venv
source .venv/bin/activate
```

Your terminal prompt will now show `(.venv)`.

### Step 4 — Install all packages

```bash
pip install -r requirement.txt
```

This installs everything: FastAPI, LangChain, LangGraph, ChromaDB, Neo4j driver, etc.

### Step 5 — Create your configuration file

Copy the example below and save it as `.env` in the `AI_agents` folder.
Fill in the values that say `YOUR_...`:

```dotenv
# --- LLM (which AI brain to use) --------------------------------
LONGCAT_API_KEY=YOUR_LONGCAT_KEY
LONGCAT_MODEL=LongCat-Flash-Chat
LONGCAT_API_BASE=https://api.longcat.chat/openai

# --- Embeddings (turns text into searchable numbers) ------------
NVIDIA_API_KEY=YOUR_NVIDIA_KEY
NVIDIA_EMBEDDING_MODEL=nvidia/llama-nemotron-embed-1b-v2

# --- Neo4j (knowledge graph database) --------------------------
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=neo4j_password

# --- ChromaDB (vector store) ------------------------------------
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=graph_rag

# --- Local Qwen LLM via Ollama (for the MOSDAC agent) -----------
QWEN_API_BASE=http://localhost:11434/v1
QWEN_MODEL=qwen2.5:32b
QWEN_API_KEY=ollama

# --- MOSDAC agent settings --------------------------------------
MOSDAC_USE_MOCK=true        # set to false to call real MOSDAC
MOSDAC_ENABLE_MOSDAC_ENDPOINT=true   # mount /mosdac/* routes
```

---

## 4. Feature 1 — GraphRAG Chatbot

### What is it?

A chatbot that answers questions about MOSDAC satellite data.
Instead of guessing, it *retrieves* real facts from two databases before answering:

1. **ChromaDB** (vector store) — finds the most similar document passages to your question.
2. **Neo4j** (knowledge graph) — finds relationship paths, e.g. "INSAT-3D -> has_sensor -> Imager -> measures -> TIR-1".

The LLM then writes a grounded answer using those facts. This is called **RAG**.

### Prerequisites

You need Neo4j Desktop (free) running locally.

1. Download Neo4j Desktop from https://neo4j.com/download/
2. Create a database with:
   - URI: `bolt://localhost:7687`
   - Username: `neo4j`
   - Password: `neo4j_password` (or whatever you put in `.env`)
3. Start the database.

You also need an NVIDIA API key for embeddings, and a LongCat API key for the LLM.
Both offer free tiers. Put them in `.env` as shown in Step 5.

### Step 1 — Ingest documents into the databases

This step reads the HTML/PDF files in `downloads/` and `atlases_pdfs/`,
chops them into small pieces (chunks), converts those pieces to numbers (embeddings),
and stores them in ChromaDB and Neo4j.

**You only need to do this once.** After that the data persists on disk.

```bash
# From the AI_agents folder, with .venv activated:
python main.py ingest
```

Watch the progress bars. A full ingest of hundreds of documents takes 10-30 minutes.

To ingest only into ChromaDB (skip Neo4j):

```bash
python main.py ingest --skip-graph
```

To ingest only into Neo4j (skip ChromaDB):

```bash
python main.py ingest --skip-vector
```

### Step 2 — Test the chatbot in the terminal

```bash
python main.py chat
```

Type your question and press Enter. Type `quit` to exit.

Example session:

```
You: What satellites does MOSDAC operate?
Assistant: MOSDAC operates INSAT-3D, INSAT-3DR, SCATSAT-1, and several others...

You: What sensor does INSAT-3D have?
Assistant: INSAT-3D carries the Imager sensor with 6 bands: VIS, SWIR, MIR, WV, TIR-1, TIR-2...
```

### Step 3 — Start the web API

This exposes the chatbot as an HTTP endpoint that a browser or app can call.

```bash
# Windows PowerShell
$env:MOSDAC_ENABLE_MOSDAC_ENDPOINT="false"   # keep it simple for now
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

You should see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 4 — Send a chat message

Open a second terminal:

```bash
# Windows PowerShell
Invoke-WebRequest -Uri http://localhost:8000/chat `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"session_id":"my-session","message":"What is MOSDAC?"}'
```

Or with curl (if installed):

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"my-session","message":"What is MOSDAC?"}'
```

The server replies with JSON: `{"answer": "MOSDAC is ..."}`.

### Step 5 — Run the unit tests

```bash
pytest tests/test_chat_api.py -v
```

All 19 tests should pass. No Ollama or Neo4j needed — the tests use fake (mock) backends.

### How the GraphRAG chatbot works (step by step)

```
User message
     |
     v
ChatService.chat()                   -- chat_api/service.py
     |
     +-- SessionStore.get(session_id)    -- loads previous turns
     |
     v
HybridRetriever.retrieve(question)   -- graph_rag/retrieval/hybrid_retriever.py
     |
     +-- VectorRetriever.as_context()   -- searches ChromaDB for similar passages
     |       graph_rag/retrieval/vector_retriever.py
     |
     +-- GraphRetriever.as_context()    -- searches Neo4j for connected facts
             graph_rag/retrieval/graph_retriever.py
     |
     v
build_graph_rag_chain                -- graph_rag/chain/graph_rag_chain.py
     |
     +-- ChatPromptTemplate (fills in graph_context + vector_context + question)
     v
LLM (LongCat / Qwen / any model)
     |
     v
String answer  -->  SessionStore.append() saves both turns
     |
     v
HTTP response back to user
```

### Files explained — GraphRAG Chatbot

**`graph_rag/config.py`**
The settings file. Every configurable value — API keys, Neo4j URL, ChromaDB path,
chunk size, how many documents to retrieve — lives here. Values come from your `.env` file.
Think of it as the control panel.

**`graph_rag/ingestion/loader.py`**
Reads HTML pages and PDF files from disk and converts them into LangChain `Document` objects
(a document is just a text string + metadata like file path and page number).

**`graph_rag/ingestion/splitter.py`**
Cuts long documents into small overlapping chunks (default: 800 characters, 100-character overlap).
Smaller chunks retrieve better because they are more focused.

**`graph_rag/ingestion/pipeline.py`**
The orchestrator. Calls `loader -> splitter -> ChromaStore -> Neo4jStore` in order.
Runs when you type `python main.py ingest`.

**`graph_rag/embeddings/nvidia_embedder.py`**
Converts text into a list of numbers (a "vector") using NVIDIA's NIM embedding API.
Two texts that mean similar things will have similar vectors.

**`graph_rag/embeddings/gemini_embedder.py`**
Same as above but uses Google Gemini embeddings. Kept as an alternative.

**`graph_rag/embeddings/ollama_embedder.py`**
Same but uses a local Ollama model (no API key needed, runs on your GPU).

**`graph_rag/vector_store/chroma_store.py`**
Wraps ChromaDB. `add_documents(chunks)` stores chunks with their embeddings.
`query(question, k=5)` returns the 5 most similar chunks.

**`graph_rag/knowledge_graph/extractor.py`**
Reads a text chunk and asks the LLM to pull out (subject -> relation -> object) triples.
For example: "INSAT-3D captures TIR-1 images" becomes
`(INSAT-3D, captures, TIR-1 images)`.

**`graph_rag/knowledge_graph/neo4j_store.py`**
Stores those triples in Neo4j. Also runs graph queries: given a word, find all nodes
up to 2 hops away. That gives the LLM relationship context.

**`graph_rag/retrieval/vector_retriever.py`**
Given a question, queries ChromaDB and formats the top passages as readable text.

**`graph_rag/retrieval/graph_retriever.py`**
Given a question, extracts key terms, searches Neo4j, and formats the relationship
paths as readable text.

**`graph_rag/retrieval/hybrid_retriever.py`**
Calls both retrievers in parallel and merges their outputs. If one fails (e.g. Neo4j
is offline) it gracefully falls back to the other.

**`graph_rag/chain/graph_rag_chain.py`**
The LCEL (LangChain Expression Language) chain. Takes `{question, history}`,
retrieves context, fills a prompt template, calls the LLM, and returns a string.

**`graph_rag/llm/longcat_client.py`**
Returns a `ChatOpenAI` client pointed at the LongCat API.

**`graph_rag/llm/qwen_client.py`**
Returns a `ChatOpenAI` client pointed at your local Ollama running Qwen2.5.

**`graph_rag/chat/chatbot.py`**
A simple multi-turn wrapper. Keeps a rolling window of the last 10 turns and
prepends them to each new question so the LLM has context.
Used by `python main.py chat` (the terminal chat).

**`chat_api/config.py`**
Settings specific to the HTTP layer: title, CORS origins, max history turns, screenshot limits.

**`chat_api/models.py`**
Pydantic models: `ChatRequest` (session_id + message) and `ChatResponse` (answer).
Pydantic validates data shapes — if a field is missing, the server returns a 422 error.

**`chat_api/session.py`**
Stores conversation history. Two implementations:
- `InMemorySessionStore` — data lives in RAM, lost when the server restarts (fine for dev).
- `RedisSessionStore` — stores in Redis, survives restarts (use in production).
`build_session_store()` picks one based on the `REDIS_URL` env var.

**`chat_api/service.py`**
The business logic layer. `chat(session_id, message)` calls the retriever, chain, and LLM,
then updates session history. Also handles screenshot uploads for multimodal questions.

**`chat_api/routes.py`**
Wires HTTP routes. `POST /chat` calls `service.chat()`. `DELETE /chat/{session}` clears history.

**`chat_api/main.py`**
The FastAPI application factory. `create_app()` assembles all the pieces.
Also conditionally mounts the MOSDAC agent routes (Feature 2) if the env var says to.

**`main.py`** (root level)
CLI entry point. Three commands:
- `python main.py ingest` — builds the databases.
- `python main.py chat` — opens the terminal chatbot.
- `python main.py test` — runs a quick smoke test against the API.

---

## 5. Feature 2 — MOSDAC Web Portal Integration

### What is it?

A conversational AI agent that lets a MOSDAC user *order satellite data in plain English*.

Example conversation:

```
User: I need INSAT-3D TIR-1 L1B data for Tamil Nadu from 14 to 18 August 2024.

Bot:  I'll search for that product first...
      [calls search_products tool]
      Found dataset_id: 3SIMG_L1B_STD
      [calls place_order tool]
      Order has been placed. Check your SFTP account.
      Order ID: MOCK-20240814-A3B7C2
      Your files will be at sftp://ftp.mosdac.gov.in/MOCK-20240814-A3B7C2/
```

The agent is a **LangGraph ReAct agent** — it loops: think -> call a tool -> look at
the result -> think again -> call another tool -> ... -> final answer.

### How it connects to the existing chatbot

Both features share the same FastAPI server and the same session store.
The only difference is the URL:

| URL | Feature |
|-----|---------|
| `POST /chat` | GraphRAG chatbot (Feature 1) |
| `POST /mosdac/chat` | MOSDAC agent (Feature 2) |

If a user switches between them in the same session, their conversation history
is preserved because both use the same `InMemorySessionStore`.

### Prerequisites (real MOSDAC)

For offline testing no prerequisites are needed — use the built-in mock.
For the real MOSDAC:
- A registered MOSDAC account at https://mosdac.gov.in
- Your username and password in `.env` as `MOSDAC_USERNAME` and `MOSDAC_PASSWORD`
- Set `MOSDAC_USE_MOCK=false`

For the AI agent you also need a local LLM. The cheapest option is Ollama:

```bash
# Download Ollama from https://ollama.com/download and install it.

# Then pull a model (choose based on your GPU RAM):
ollama pull qwen2.5:32b    # needs ~24 GB GPU RAM (most accurate)
ollama pull qwen2.5:14b    # needs ~16 GB GPU RAM
ollama pull qwen2.5:7b     # needs ~8 GB GPU RAM (for low-end machines)
```

Set the model name in `.env`:

```dotenv
AGENT_LLM_MODEL=qwen2.5:14b   # or whatever you pulled
AGENT_LLM_BASE_URL=http://localhost:11434/v1
AGENT_LLM_API_KEY=ollama
```

### Step 1 — Start the fake MOSDAC backend (for offline testing)

This runs a tiny fake MOSDAC server on port 9000 that accepts orders and returns
realistic responses — no real account needed.

```bash
# In Terminal A:
python -m mosdac_agent.mock_mosdac
# Output: INFO:     Uvicorn running on http://0.0.0.0:9000
```

To make the agent talk to it (instead of the in-process mock), add to `.env`:

```dotenv
MOSDAC_USE_MOCK=false
MOSDAC_BASE_URL=http://localhost:9000
MOSDAC_USERNAME=dev
MOSDAC_PASSWORD=dev
```

### Step 2 — Start the main API with MOSDAC enabled

```bash
# In Terminal B (PowerShell):
$env:MOSDAC_ENABLE_MOSDAC_ENDPOINT="true"
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload
```

You should see in the log:
```
INFO: MOSDAC agent endpoints mounted under /mosdac
```

### Step 3 — Check the MOSDAC health endpoint

```bash
# PowerShell:
Invoke-WebRequest -Uri http://localhost:8000/mosdac/health | Select-Object -ExpandProperty Content
```

Should reply:
```json
{"status":"ok","bot_name":"MOSDAC-Bot","mock_mode":true,"agent_use_local_tools":true}
```

### Step 4 — Send an order request

```bash
# PowerShell:
Invoke-WebRequest -Uri http://localhost:8000/mosdac/chat `
  -Method POST `
  -ContentType "application/json" `
  -Headers @{"X-MOSDAC-User"="dev"} `
  -Body '{"session_id":"demo","message":"Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP"}'
```

The agent will think, call tools, and reply with something like:
```
Order has been placed. Check your SFTP account.
Order ID: MOCK-20240814-A3B7C2
```

### Step 5 — Use the Streamlit UI (optional)

Streamlit is a Python library that builds simple web UIs in pure Python.

```bash
# In Terminal C:
$env:CHAT_API="http://localhost:8000/mosdac/chat"
$env:MOSDAC_USER="dev"
streamlit run mosdac_agent/streamlit_app.py
```

Open http://localhost:8501 in your browser. You will see a chat box.

### Step 6 — Run the tests

```bash
# Tools layer (no network, no Ollama needed):
pytest tests/test_mosdac_tools.py -v

# Fake MOSDAC HTTP server tests:
pytest tests/test_mosdac_mock_server.py -v

# Integration tests (both endpoints on one app):
pytest tests/test_mosdac_integration.py -v

# Agent layer (fake LLM, no Ollama needed):
pytest tests/test_mosdac_agent.py -v
```

### Architecture deep-dive

The agent feature has four clean layers:

```
Layer 4:  HTTP routes         mosdac_agent/routes.py
               |
Layer 3:  Agent service       mosdac_agent/agent.py  (MosdacAgentService)
               |
Layer 2:  AgentRunner         mosdac_agent/agent.py  (AgentRunner)
               |  LangGraph ReAct loop
Layer 1:  Tool implementations  mosdac_agent/tools.py
               |
Layer 0:  Clients + stores
           mosdac_agent/client.py  (HTTP or Mock)
           mosdac_agent/store.py   (SQLite or Memory)
```

Each layer is independently testable. You can test Layer 1 tools without any LLM.

### Files explained — MOSDAC Agent

**`mosdac_agent/__init__.py`**
The public face of the package. Exposes `build_agent`, `build_mcp_server`, etc.
All heavy imports happen lazily so importing the package is instant.

**`mosdac_agent/config.py`**
All MOSDAC settings in one class (`MosdacSettings`). Every value reads from `.env`.
Key settings:
- `mosdac_use_mock=true` — use built-in mock, never touches the real API.
- `enable_mosdac_endpoint=false` — the `/mosdac/*` routes are NOT mounted (default). Set to `true` to enable.
- `agent_llm_model` — which AI model the agent uses.
- `max_orders_per_user_per_hour=10` — safety limit to prevent accidental floods.

**`mosdac_agent/exceptions.py`**
Custom error classes: `ValidationError` (bad input), `AuthError` (login failed),
`RateLimitError` (too many orders), `NotFoundError`, `UpstreamError`.
Using specific error classes lets callers handle each case differently.

**`mosdac_agent/catalog.py`**
A built-in list of 6 INSAT satellite products and 17 Indian state bounding boxes.
`search_catalogue(query)` filters by name/keyword/satellite/sensor.
`resolve_region("Tamil Nadu")` returns `"76.2,8.0,80.4,13.6"` (min_lon,min_lat,max_lon,max_lat).
You can override both with your own JSON files via env vars — no code changes needed.

**`mosdac_agent/store.py`**
Stores two things:
1. **Idempotency keys** — if the agent accidentally calls `place_order` twice with
   the same key, the second call returns the first order (no duplicate).
2. **Order audit log** — every order is recorded with timestamp, so we can enforce
   the per-hour rate limit.
Two implementations: `SqliteStore` (file on disk) and `InMemoryStore` (RAM, for tests).

**`mosdac_agent/client.py`**
Knows how to talk to MOSDAC.
- `HttpMosdacClient`: logs in via Keycloak (SSO), caches the token, and calls the REST API.
- `MockMosdacClient`: fully in-process, returns realistic fake responses. No internet needed.

**`mosdac_agent/tools.py`**
The core logic. Four functions that do the real work:
1. `search_products_impl(ctx, query)` — search the product catalogue.
2. `place_order_impl(ctx, dataset_id, start_date, ...)` — validate inputs then call client.
3. `check_order_status_impl(ctx, order_id)` — ask the backend for current status.
4. `list_my_orders_impl(ctx, limit)` — show this user's recent orders.
`build_local_tools(ctx)` wraps these as LangChain `StructuredTool`s so the agent can call them.

**`mosdac_agent/agent.py`**
Three things in one file:
- `build_agent(...)` — assembles a LangGraph ReAct agent from the LLM + tools.
- `AgentRunner` — thread-safe wrapper. `chat(thread_id, message)` runs one turn.
- `MosdacAgentService` — high-level service that stores turns in the session store.

**`mosdac_agent/routes.py`**
Four HTTP endpoints:
- `GET /mosdac/health` — is the agent running?
- `GET /mosdac/config` — branding info for the JavaScript widget.
- `POST /mosdac/chat` — send a message, get a reply.
- `DELETE /mosdac/chat/{session_id}` — clear conversation history.

**`mosdac_agent/mock_mosdac.py`**
A fake MOSDAC FastAPI app on port 9000. Useful for manual testing when you do not
have a real MOSDAC account. Simulates login, order creation, and status queries.

**`mosdac_agent/streamlit_app.py`**
A tiny Python web UI built with Streamlit. Posts messages to `/mosdac/chat` and
shows the replies in a scrollable chat window. Good for demos.

**`mosdac_agent/widget/widget.html`**
An embeddable chat widget. The MOSDAC portal webmaster can paste one `<iframe>` tag
into any web page and users get a floating chat bubble without any coding.

**`mosdac_agent/widget/widget.css`**
Styles for the widget: MOSDAC navy blue (`#002b5c`) header, rounded bubbles, etc.

**`mosdac_agent/widget/widget.js`**
The JavaScript that powers the widget. On load it fetches `/mosdac/config` to
get the bot name, then sends user messages to `/mosdac/chat` and displays replies.
`window.MOSDAC_API` lets the same JS file serve multiple deployments by setting a different URL.

---

## 6. Feature 3 — MCP Tool Feature

### What is it?

MCP (Model Context Protocol) is Anthropic's standard for connecting AI assistants
to external tools — think of it as a USB plug: any MCP-compatible AI can use
any MCP-compatible tool.

This feature exposes the same four MOSDAC tools as an MCP server.
That means Claude Desktop, any OpenAI-compatible assistant, or a custom agent
can discover and call them without changing the tool code.

### Two ways to use the MCP server

**Way 1 — stdio (talk via keyboard/pipe)**
Used by Claude Desktop and MCP Inspector. The host program launches this server
as a subprocess and talks to it over stdin/stdout.

```bash
# Start in stdio mode (default):
python -m mosdac_agent.mcp_server
```

**Way 2 — Streamable HTTP (talk over the network)**
Used when the MCP server runs on a different machine from the agent.

```bash
# PowerShell:
$env:MCP_TRANSPORT="streamable-http"
python -m mosdac_agent.mcp_server
# Output: Starting MCP server on http://127.0.0.1:8765/mcp/
```

### Step 1 — Explore tools with MCP Inspector

MCP Inspector is a web UI that lets you browse and test MCP servers interactively.

```bash
# You need Node.js installed. Then:
npx @modelcontextprotocol/inspector
```

Open http://localhost:6274 in your browser.
Connect to `http://127.0.0.1:8765/mcp` (if your server is in HTTP mode).
You will see four tools listed: `search_products`, `place_order`,
`check_order_status`, `list_my_orders`. Click any to call it manually.

### Step 2 — Connect to Claude Desktop

1. Install Claude Desktop from https://claude.ai/download
2. Open `claude_desktop_config.json`:
   - **Mac**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
3. Add this configuration:

```json
{
  "mcpServers": {
    "mosdac": {
      "command": "python",
      "args": ["-m", "mosdac_agent.mcp_server"],
      "cwd": "D:\\AI_agents"
    }
  }
}
```

4. Restart Claude Desktop.
5. In a new conversation, ask: "What MOSDAC tools do you have?"
   Claude will list the four tools and you can ask it to order satellite data.

### Step 3 — Make the MOSDAC agent use the MCP server instead of local tools

By default the agent calls tools in-process (fast, no network needed).
To use the MCP server over HTTP instead:

```dotenv
AGENT_USE_LOCAL_TOOLS=false
```

The agent will then call `http://127.0.0.1:8765/mcp` to invoke tools.
This is useful if you run the tool server on a GPU machine and the agent elsewhere.

### What makes MCP special

```
Without MCP:                    With MCP:
Each AI needs custom code       One MCP server, any AI can connect

Claude needs Claude tools
GPT needs OpenAI functions
                        ------> MOSDAC MCP Server <------
                                /  /  \  \
                         Claude  GPT  Qwen  Your own agent
```

### Files explained — MCP Server

**`mosdac_agent/mcp_server.py`**
Contains `build_mcp_server()` and `main()`.
`build_mcp_server()` creates a `FastMCP` instance and registers four `@mcp.tool`
decorated functions. Each function wraps the matching `*_impl()` function from
`tools.py`, converting `MosdacError` exceptions into `ToolError` (the MCP standard
for tool failures).
`main()` checks `MCP_TRANSPORT` and starts the server in the right mode.

---

## 7. How to add new features

### Add a new chatbot data source (extend Feature 1)

**Scenario**: You want the bot to also answer questions about weather forecasts,
using a separate database.

1. Create `graph_rag/retrieval/weather_retriever.py`:

```python
class WeatherRetriever:
    def as_context(self, query: str) -> str:
        # query your weather database here
        return "Weather data: ..."
```

2. Update `HybridRetriever` in `graph_rag/retrieval/hybrid_retriever.py` to call
   your new retriever and add the result to the returned dict.

3. Update the system prompt in `graph_rag/chain/graph_rag_chain.py` to include
   a `{weather_context}` placeholder.

That is all. The chain handles the rest.

### Add a new MOSDAC tool (extend Feature 2 and 3)

**Scenario**: You want to add a `cancel_order` tool.

1. Add the implementation in `mosdac_agent/tools.py`:

```python
def cancel_order_impl(ctx: ToolContext, order_id: str) -> dict:
    """Cancel a previously placed order."""
    if not order_id:
        raise ValidationError("order_id is required.")
    return ctx.client.cancel_order(order_id)
```

2. Add `cancel_order` to the `MosdacClient` protocol in `mosdac_agent/client.py`
   and implement it in both `HttpMosdacClient` and `MockMosdacClient`.

3. Register it in `build_local_tools()` inside `tools.py`:

```python
def _cancel_order(order_id: str) -> dict:
    """Cancel a previously placed order."""
    return cancel_order_impl(ctx, order_id=order_id)

return [
    ...,
    StructuredTool.from_function(_cancel_order, name="cancel_order"),
]
```

4. Register it in the MCP server in `mosdac_agent/mcp_server.py`:

```python
@mcp.tool
def cancel_order(order_id: str) -> dict:
    """Cancel a previously placed order."""
    return _wrap(cancel_order_impl)(order_id=order_id)
```

5. Update the system prompt in `mosdac_agent/agent.py` to mention the new tool.

6. Write a test in `tests/test_mosdac_tools.py`.

### Add a new HTTP endpoint (extend Feature 2)

**Scenario**: You want a `GET /mosdac/orders/{user}` endpoint to list orders.

In `mosdac_agent/routes.py`, inside `build_mosdac_router()`:

```python
@router.get("/orders/{user_id}")
def list_orders(user_id: str, limit: int = 10):
    from mosdac_agent.tools import ToolContext, list_my_orders_impl
    from mosdac_agent.client import build_default_client
    from mosdac_agent.store import build_default_store
    ctx = ToolContext(
        user=user_id,
        store=build_default_store(),
        client=build_default_client(),
    )
    return list_my_orders_impl(ctx, limit=limit)
```

### Deploy to a different domain (e.g. a coastal monitoring portal)

All branding and backend URLs are env-var driven. Create a new `.env` file:

```dotenv
# Rebrand
MOSDAC_BOT_NAME=CoastalBot
MOSDAC_FINAL_SUCCESS_SENTENCE=Your data request has been submitted.
MOSDAC_SFTP_BASE_URL=sftp://ftp.coastaldata.example.com

# Different backend
MOSDAC_BASE_URL=https://api.coastaldata.example.com
MOSDAC_USERNAME=your_username
MOSDAC_PASSWORD=your_password

# Different LLM
AGENT_LLM_MODEL=gpt-4o
AGENT_LLM_BASE_URL=https://api.openai.com/v1
AGENT_LLM_API_KEY=sk-your-key
```

No code changes needed.

### Replace SQLite with Redis for multi-server deployments

`SqliteStore` works fine on one machine. To share order state across multiple
servers, implement the `Store` protocol with Redis:

```python
# mosdac_agent/redis_store.py
import redis, json, time

class RedisStore:
    def __init__(self, url: str):
        self._r = redis.from_url(url)

    def find_idempotent(self, key: str):
        val = self._r.get(f"idem:{key}")
        return val.decode() if val else None

    def save_idempotent(self, key: str, order_id: str) -> None:
        self._r.set(f"idem:{key}", order_id, nx=True)  # nx = only set if not exists

    def record_order(self, user: str, order_id: str, payload: dict) -> None:
        entry = json.dumps({"order_id": order_id, "payload": payload, "created_at": time.time()})
        self._r.lpush(f"orders:{user}", entry)
        self._r.ltrim(f"orders:{user}", 0, 999)  # keep last 1000

    def orders_in_last_hour(self, user: str) -> int:
        cutoff = time.time() - 3600
        entries = self._r.lrange(f"orders:{user}", 0, -1)
        return sum(1 for e in entries if json.loads(e)["created_at"] > cutoff)

    def list_orders(self, user: str, limit: int = 20):
        entries = self._r.lrange(f"orders:{user}", 0, limit - 1)
        return [json.loads(e) for e in entries]
```

Then inject it: `build_agent(store=RedisStore("redis://localhost:6379"))`.

---

## 8. Running all tests

```bash
# All tests at once:
pytest tests/ -v

# Individual test files:
pytest tests/test_chat_api.py -v           # 19 tests -- existing chatbot API
pytest tests/test_mosdac_tools.py -v       # 22 tests -- tool layer (no LLM needed)
pytest tests/test_mosdac_mock_server.py -v # 5 tests  -- fake MOSDAC HTTP server
pytest tests/test_mosdac_integration.py -v # 7 tests  -- both features on one app
pytest tests/test_mosdac_agent.py -v       # 3 tests  -- agent layer (fake LLM)

# Run only tests matching a keyword:
pytest tests/ -k "order" -v

# Show which lines are not covered by tests:
pytest tests/ --cov=mosdac_agent --cov-report=term-missing
```

All 56 tests should pass without any running database, LLM, or internet connection.

---

## 9. File-by-file reference

A flat list of every source file and one sentence about what it does.

### Root files

| File | What it does |
|------|-------------|
| `main.py` | CLI: `ingest`, `chat`, `test` commands |
| `requirement.txt` | All Python packages the project needs |
| `.env` | Your secret keys and settings (never commit to git) |
| `.gitignore` | Tells git to ignore `.env`, `.venv`, `chroma_db`, etc. |
| `guide.md` | Technical reference for developers |
| `documentation.md` | This file — beginner guide |

### `chat_api/` — HTTP layer

| File | What it does |
|------|-------------|
| `main.py` | FastAPI app factory; assembles all pieces and optionally mounts MOSDAC |
| `config.py` | HTTP-layer settings (title, CORS, max history, screenshot limits) |
| `models.py` | `ChatRequest` and `ChatResponse` data shapes |
| `service.py` | Business logic: retrieves context, calls chain, updates history |
| `session.py` | Conversation history storage (in-memory or Redis) |
| `routes.py` | HTTP routes: `POST /chat`, `DELETE /chat/{session}` |

### `graph_rag/` — GraphRAG engine

| File | What it does |
|------|-------------|
| `config.py` | All RAG settings (API keys, database URLs, chunk sizes) |
| `ingestion/loader.py` | Loads HTML and PDF files from disk |
| `ingestion/splitter.py` | Cuts documents into overlapping chunks |
| `ingestion/pipeline.py` | Orchestrates the full ingest: load -> split -> embed -> graph |
| `embeddings/nvidia_embedder.py` | NVIDIA NIM text embeddings |
| `embeddings/gemini_embedder.py` | Google Gemini text embeddings |
| `embeddings/ollama_embedder.py` | Local Ollama embeddings |
| `vector_store/chroma_store.py` | ChromaDB wrapper: store and query chunks |
| `knowledge_graph/extractor.py` | LLM-based triple extraction from text |
| `knowledge_graph/neo4j_store.py` | Neo4j wrapper: upsert triples, query neighbors |
| `retrieval/vector_retriever.py` | Search ChromaDB, format as text |
| `retrieval/graph_retriever.py` | Search Neo4j, format as text |
| `retrieval/hybrid_retriever.py` | Run both retrievers, merge results |
| `chain/graph_rag_chain.py` | LCEL chain: retrieve -> prompt -> LLM -> string |
| `llm/longcat_client.py` | LongCat LLM client |
| `llm/qwen_client.py` | Ollama/Qwen LLM client |
| `chat/chatbot.py` | Multi-turn terminal chatbot with history window |

### `mosdac_agent/` — MOSDAC agent

| File | What it does |
|------|-------------|
| `__init__.py` | Public facade with lazy imports |
| `config.py` | All MOSDAC + agent settings |
| `exceptions.py` | Custom error classes: Validation, Auth, RateLimit, NotFound, Upstream |
| `catalog.py` | Built-in product catalogue + Indian state bounding boxes |
| `store.py` | Idempotency + audit trail (SQLite and in-memory implementations) |
| `client.py` | MOSDAC API clients (HTTP with Keycloak SSO, and Mock) |
| `tools.py` | Four tool implementations + LangChain wrappers |
| `mcp_server.py` | FastMCP server exposing the four tools |
| `agent.py` | LangGraph ReAct agent + runner + session-aware service |
| `routes.py` | FastAPI router: health, config, chat, clear |
| `mock_mosdac.py` | Fake MOSDAC server (port 9000) for offline testing |
| `streamlit_app.py` | Simple Streamlit chat UI |
| `widget/widget.html` | Embeddable iframe chat widget |
| `widget/widget.css` | Widget styles (MOSDAC navy blue theme) |
| `widget/widget.js` | Widget JavaScript: sends messages, shows replies |

### `tests/` — automated tests

| File | What it tests |
|------|--------------|
| `conftest.py` | Shared pytest fixtures used by multiple test files |
| `test_chat_api.py` | 19 tests for the GraphRAG HTTP API |
| `test_mosdac_tools.py` | 22 tests for the tool layer (no LLM, no network) |
| `test_mosdac_mock_server.py` | 5 tests for the fake MOSDAC HTTP server |
| `test_mosdac_integration.py` | 7 tests for both endpoints on one FastAPI app |
| `test_mosdac_agent.py` | 3 tests for the LangGraph agent with a fake LLM |

---

## Quick-start cheat sheet

```bash
# 1. Install
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux
pip install -r requirement.txt

# 2. Feature 1 -- GraphRAG chatbot
python main.py ingest               # one-time: build databases
python main.py chat                 # terminal chat
uvicorn chat_api.main:app --reload  # web API on port 8000
pytest tests/test_chat_api.py -v    # run tests

# 3. Feature 2 -- MOSDAC agent
$env:MOSDAC_ENABLE_MOSDAC_ENDPOINT="true"
uvicorn chat_api.main:app --reload  # web API with /mosdac/* routes
streamlit run mosdac_agent/streamlit_app.py  # Streamlit UI on port 8501
pytest tests/test_mosdac_tools.py tests/test_mosdac_agent.py -v

# 4. Feature 3 -- MCP server
python -m mosdac_agent.mcp_server                             # stdio mode
$env:MCP_TRANSPORT="streamable-http"
python -m mosdac_agent.mcp_server                             # HTTP mode on port 8765
npx @modelcontextprotocol/inspector                           # visual test tool

# 5. All tests
pytest tests/ -v
```

---

*This documentation covers the entire codebase as of May 2026.
When you add new files, add a row to the file-by-file reference table in Section 9.*
