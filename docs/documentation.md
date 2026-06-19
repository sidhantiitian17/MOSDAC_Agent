# Complete Beginner's Guide to the AI Agents Codebase

Welcome! This guide explains everything in plain language — no experience required.
Think of this as a friendly tour through a LEGO set: we will look at each brick,
explain what it does, and show you how to put them together.

---

## Table of Contents

1. [Glossary — "What does that word mean?"](#1-glossary)
2. [Big Picture — how the pieces fit together](#2-big-picture)
3. [Installation — getting the project running on your computer](#3-installation)
4. [Feature 1 — GraphRAG Chatbot](#4-feature-1--graphrag-chatbot)
5. [How to add new features](#5-how-to-add-new-features)
6. [Running all tests](#6-running-all-tests)
7. [File-by-file reference](#7-file-by-file-reference)

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
| **Session** | One ongoing conversation. Each user gets a unique session ID so the server remembers their previous messages. |

---

## 2. Big Picture

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
          +--------------------+---------------+
                   |
          +--------v--+
          | Neo4j     |
          | ChromaDB  |
          | databases |
          +-----------+
```

- **Feature 1** (GraphRAG): You ask a question, the server searches Neo4j + ChromaDB, and the LLM answers from real data.

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

This installs everything: FastAPI, LangChain, ChromaDB, Neo4j driver, etc.

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

**`main.py`** (root level)
CLI entry point. Three commands:
- `python main.py ingest` — builds the databases.
- `python main.py chat` — opens the terminal chatbot.
- `python main.py test` — runs a quick smoke test against the API.

---

## 5. How to add new features

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

---

## 6. Running all tests

```bash
# All tests at once:
pytest tests/ -v

# Individual test files:
pytest tests/test_chat_api.py -v           # 19 tests -- existing chatbot API

# Run only tests matching a keyword:
pytest tests/ -k "order" -v
```

---

## 7. File-by-file reference

A flat list of every source file and one sentence about what it does.

### Root files

| File | What it does |
|------|-------------|
| `main.py` | CLI: `ingest`, `chat`, `test` commands |
| `requirement.txt` | All Python packages the project needs |
| `.env` | Your secret keys and settings (never commit to git) |
| `.gitignore` | Tells git to ignore `.env`, `.venv`, `chroma_db`, etc. |
| `documentation.md` | This file — beginner guide |

### `chat_api/` — HTTP layer

| File | What it does |
|------|-------------|
| `main.py` | FastAPI app factory; assembles all pieces |
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

### `tests/` — automated tests

| File | What it tests |
|------|--------------|
| `conftest.py` | Shared pytest fixtures used by multiple test files |
| `test_chat_api.py` | 19 tests for the GraphRAG HTTP API |

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

# 3. All tests
pytest tests/ -v
```

---

*This documentation covers the entire codebase as of May 2026.
When you add new files, add a row to the file-by-file reference table in Section 7.*
