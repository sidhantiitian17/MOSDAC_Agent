# Graph RAG Chatbot — Implementation Plan

## Overview

Build a **Graph Retrieval-Augmented Generation (Graph RAG)** chatbot that combines:
- **Neo4j** — knowledge graph for  relationship-aware retrieval #ignore
- **ChromaDB** — vector database for semantic search
- **LongCat-Flash-Chat** — LLM via OpenAI-compatible API (swappable via `.env`)
- **Gemini Embedding (`gemini-embedding-2`)** — vector embeddings
- **LangChain** — orchestration layer tying KG + vector retrieval into a unified chain

Data is ingested from HTML files and PDFs in two source folders, converted to both graph triples and dense vectors, then served through a chatbot that routes queries to whichever retrieval mode fits best.

---

## Target Directory Structure

```
d:\AI_agents\
├── .env                        # credentials (never commit)
├── .gitignore
├── requirement.txt
├── main.py                     # CLI entry point
├── instruction.md              # this file
│
├── graph_rag/                  # core package
│   ├── __init__.py
│   ├── config.py               # loads .env, exposes typed settings
│   │
│   ├── ingestion/              # data ingestion pipeline
│   │   ├── __init__.py
│   │   ├── loader.py           # file discovery & raw text extraction
│   │   ├── splitter.py         # chunk documents into passages
│   │   └── pipeline.py        # orchestrates loader → splitter → store
│   │
│   ├── embeddings/             # embedding layer
│   │   ├── __init__.py
│   │   └── gemini_embedder.py  # Gemini embedding-2 client
│   │
│   ├── vector_store/           # ChromaDB integration
│   │   ├── __init__.py
│   │   └── chroma_store.py     # add, query, delete helpers
│   │
│   ├── knowledge_graph/        # Neo4j + NLP extraction
│   │   ├── __init__.py
│   │   ├── extractor.py        # entity & relationship extraction (NLP)
│   │   └── neo4j_store.py      # Cypher helpers (upsert nodes/rels, query)
│   │
│   ├── retrieval/              # dual retrieval strategies
│   │   ├── __init__.py
│   │   ├── vector_retriever.py # semantic search via ChromaDB
│   │   ├── graph_retriever.py  # Cypher-based KG traversal
│   │   └── hybrid_retriever.py # merge + re-rank both results
│   │
│   ├── llm/                    # LLM abstraction
│   │   ├── __init__.py
│   │   └── longcat_client.py   # OpenAI-compatible wrapper for LongCat
│   │
│   ├── chain/                  # LangChain RAG chain
│   │   ├── __init__.py
│   │   └── graph_rag_chain.py  # LCEL chain: retrieve → format → generate
│   │
│   └── chat/                   # conversation interface
│       ├── __init__.py
│       └── chatbot.py          # stateful chat session with memory
│
└── tests/                      # test suite
    ├── conftest.py
    ├── test_loader.py
    ├── test_embeddings.py
    ├── test_chroma.py
    ├── test_neo4j.py
    ├── test_extractor.py
    ├── test_retrieval.py
    ├── test_chain.py
    └── test_chatbot.py
```

---

## Environment Variables (`.env`)

```env
# LLM — LongCat (OpenAI-compatible)
LONGCAT_API_KEY=ak_...
LONGCAT_MODEL=LongCat-Flash-Chat
LONGCAT_API_BASE=https://api.longcat.chat/openai
LONGCAT_API_FORMAT=openai

# Embeddings — Gemini
GEMINI_API_KEY=AIza...
GEMINI_EMBEDDING_MODEL=gemini-embedding-2

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_neo4j_password

# ChromaDB
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=graph_rag

# Data sources
DOWNLOADS_DIR=D:/AI_agents/downloads
ATLASES_DIR=D:/AI_agents/atlases_pdfs

# Chunking
CHUNK_SIZE=800
CHUNK_OVERLAP=100
```

> **Swap LLM to a local Docker model**: change `LONGCAT_API_BASE` to your local endpoint (e.g., `http://localhost:11434/v1` for Ollama) and update `LONGCAT_MODEL`. No code changes needed.

---

## Dependencies (`requirement.txt`)

```
# Core LangChain
langchain>=0.3
langchain-community>=0.3
langchain-openai>=0.2
langchain-google-genai>=2.0

# LLM SDKs
openai>=1.0
anthropic

# Vector DB
chromadb>=0.5

# Graph DB
neo4j>=5.0
langchain-neo4j>=0.1

# Document loaders
pypdf>=4.0
beautifulsoup4>=4.12
unstructured[pdf,html]>=0.14

# NLP — entity & relationship extraction
spacy>=3.7
# run after install: python -m spacy download en_core_web_trf

# Utilities
python-dotenv
pydantic>=2.0
tqdm
```

---

## Module Implementation Guide

### 1. `graph_rag/config.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    longcat_api_key: str
    longcat_model: str = "LongCat-Flash-Chat"
    longcat_api_base: str = "https://api.longcat.chat/openai"

    gemini_api_key: str
    gemini_embedding_model: str = "gemini-embedding-2"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str

    chroma_persist_dir: str = "./chroma_db"
    chroma_collection: str = "graph_rag"

    downloads_dir: str = "D:/AI_agents/downloads"
    atlases_dir: str = "D:/AI_agents/atlases_pdfs"

    chunk_size: int = 800
    chunk_overlap: int = 100

settings = Settings()
```

---

### 2. `graph_rag/ingestion/loader.py`

Discovers all PDF and HTML files from both source directories and returns a list of `Document` objects with metadata (`source`, `file_type`, `file_name`).

**Key logic:**
- Use `pypdf` / `langchain_community.document_loaders.PyPDFLoader` for PDFs
- Use `beautifulsoup4` / `UnstructuredHTMLLoader` for HTML files
- Walk both `DOWNLOADS_DIR` and `ATLASES_DIR` recursively
- Attach source path metadata to every document

```python
# Pseudocode
def load_all_documents() -> list[Document]:
    docs = []
    for folder in [settings.downloads_dir, settings.atlases_dir]:
        for path in Path(folder).rglob("*"):
            if path.suffix == ".pdf":
                docs.extend(PyPDFLoader(str(path)).load())
            elif path.suffix in {".html", ".htm"}:
                docs.extend(UnstructuredHTMLLoader(str(path)).load())
    return docs
```

---

### 3. `graph_rag/ingestion/splitter.py`

Splits raw documents into overlapping passages using `RecursiveCharacterTextSplitter`:

```python
splitter = RecursiveCharacterTextSplitter(
    chunk_size=settings.chunk_size,
    chunk_overlap=settings.chunk_overlap,
    separators=["\n\n", "\n", ". ", " "],
)
```

Each chunk retains the parent document's metadata plus a `chunk_id`.

---

### 4. `graph_rag/embeddings/gemini_embedder.py`

Wraps the Gemini embedding API (`gemini-embedding-2`) as a LangChain `Embeddings` subclass:

```python
from langchain_google_genai import GoogleGenerativeAIEmbeddings

def get_embedder() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=settings.gemini_embedding_model,
        google_api_key=settings.gemini_api_key,
        task_type="retrieval_document",
    )
```

---

### 5. `graph_rag/vector_store/chroma_store.py`

Persists embeddings in ChromaDB. Key operations:
- `add_documents(chunks)` — embed and upsert chunks
- `similarity_search(query, k=5)` — top-k semantic results
- `get_collection_stats()` — document count for health checks

```python
from langchain_community.vectorstores import Chroma

def get_vector_store(embedder) -> Chroma:
    return Chroma(
        collection_name=settings.chroma_collection,
        embedding_function=embedder,
        persist_directory=settings.chroma_persist_dir,
    )
```

---

### 6. `graph_rag/knowledge_graph/extractor.py`

Extracts entities and relationships from text using spaCy (`en_core_web_trf`):

**Entity extraction:**
- spaCy NER: `ORG`, `PERSON`, `GPE`, `LOC`, `PRODUCT`, `EVENT`, `NORP`
- Noun chunks as fallback entities

**Relationship extraction:**
- Dependency parse: subject-verb-object (SVO) triples
- Pattern: `nsubj -> ROOT -> dobj / prep -> pobj`
- Each triple becomes: `(entity_1) -[RELATION]-> (entity_2)`

```python
# Output schema
@dataclass
class Triple:
    subject: str
    subject_type: str
    relation: str
    object_: str
    object_type: str
    source_chunk_id: str
    confidence: float
```

---

### 7. `graph_rag/knowledge_graph/neo4j_store.py`

Stores triples in Neo4j via the `neo4j` driver:

**Cypher schema:**
cypher
// Node
(:Entity {name: str, type: str, sources: [str]})

// Relationship — dynamic type from relation string
(:Entity)-[:RELATION {source_chunk_id: str, confidence: float}]->(:Entity)

// Indexes
CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name);
CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type);
```

**Key methods:**
- `upsert_triple(triple: Triple)` — MERGE nodes, MERGE relationship
- `query_neighbors(entity: str, depth: int = 2)` — subgraph around an entity
- `fulltext_search(query: str)` — Neo4j full-text index on `Entity.name`
- `schema_report()` — node/relationship counts for health check

---

### 8. `graph_rag/llm/longcat_client.py`

LangChain-compatible LLM wrapper using the OpenAI-compatible endpoint:

```python
from langchain_openai import ChatOpenAI

def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.longcat_model,
        api_key=settings.longcat_api_key,
        base_url=settings.longcat_api_base,
        temperature=0.2,
        max_tokens=2048,
    )
```

> **To switch to a local Docker model**: set `LONGCAT_API_BASE=http://localhost:11434/v1` and `LONGCAT_MODEL=llama3` (or any Ollama/LM Studio model). `longcat_client.py` requires no edits.

---

### 9. `graph_rag/retrieval/vector_retriever.py`

Returns top-k semantically similar chunks from ChromaDB, formatted as context passages with source metadata.

### 10. `graph_rag/retrieval/graph_retriever.py`

For a given query:
1. Extract named entities from the query using spaCy
2. Run `fulltext_search` on Neo4j for each entity
3. Fetch 2-hop subgraph (`query_neighbors`) for matched entities
4. Serialize subgraph paths as readable triples: `"EntityA --[RELATION]--> EntityB"`

### 11. `graph_rag/retrieval/hybrid_retriever.py`

Merges and deduplicates results from both retrievers:
- Vector results scored by cosine similarity
- Graph results scored by path relevance (hop count, entity match score)
- Final context = top vector passages + serialized graph paths, with source attribution

---

### 12. `graph_rag/chain/graph_rag_chain.py`

LCEL (LangChain Expression Language) chain:

```python
from langchain_core.runnables import RunnableParallel, RunnableLambda
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

SYSTEM_PROMPT = """You are an expert assistant with access to a knowledge graph and document database.

Use the following context to answer the user's question accurately.

GRAPH CONTEXT (entity relationships):
{graph_context}

DOCUMENT CONTEXT (relevant passages):
{vector_context}

Answer concisely. Cite sources when referencing specific facts.
If the answer is not found in the context, say so explicitly."""

prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("human", "{question}"),
])

# Chain definition
chain = (
    RunnableParallel({
        "graph_context": graph_retriever,
        "vector_context": vector_retriever,
        "question": RunnableLambda(lambda x: x["question"]),
    })
    | prompt
    | llm
    | StrOutputParser()
)
```

---

### 13. `graph_rag/chat/chatbot.py`

Stateful chat session with LangChain conversation memory:
- Uses `ConversationBufferWindowMemory` (last 10 turns)
- Exposes `chat(user_input: str) -> str`
- Logs retrieved context and sources for each turn
- Handles multi-turn follow-up questions via message history

---

### 14. `graph_rag/ingestion/pipeline.py`

Full ingestion orchestrator:

```
load_all_documents()
  -> split_into_chunks()
  -> [in parallel]
      embed_and_store_in_chroma()         # vector path
      extract_triples_and_store_neo4j()   # graph path
  -> report stats (doc count, chunk count, node count, edge count)
```

Progress is shown with `tqdm`. Ingestion is idempotent — re-running skips already-indexed chunk IDs.

---

### 15. `main.py`

CLI entry point with three commands:

```
python main.py ingest     # run full ingestion pipeline
python main.py chat       # start interactive chatbot REPL
python main.py test       # run health checks on all components
```

---

## Testing Plan (`tests/`)

Each test module is independent and can be run in isolation with `pytest`.

### `tests/test_loader.py`
- Asserts PDF and HTML files are discovered from both source folders
- Asserts `Document.metadata` contains `source` and `file_type`
- Asserts empty folders return empty list without error

### `tests/test_embeddings.py`
- Embeds a known sentence; asserts vector dimension > 0
- Asserts two semantically similar sentences have cosine similarity > 0.8
- Asserts API key error raises `ValueError` with clear message

### `tests/test_chroma.py`
- Adds 5 dummy documents; asserts `collection.count() == 5`
- Queries with a related sentence; asserts top result is correct document
- Tests persist + reload cycle: collection survives process restart

### `tests/test_neo4j.py`
- Connects to Neo4j; asserts connection is live (`RETURN 1`)
- Inserts a test node; queries it back; asserts match
- Inserts a triple; traverses 1-hop; asserts relationship returned
- Asserts `schema_report()` returns non-negative counts

### `tests/test_extractor.py`
- Runs extraction on a sample sentence: `"Apple acquired Beats Electronics in 2014."`
- Asserts entity `Apple` (ORG) and `Beats Electronics` (ORG) are extracted
- Asserts at least one triple with relation containing "acqui" is returned
- Tests on multi-sentence paragraph; asserts multiple triples extracted

### `tests/test_retrieval.py`
- **Vector retriever**: insert known chunk, query with paraphrase, assert retrieved
- **Graph retriever**: insert known triple, query entity by name, assert subgraph returned
- **Hybrid retriever**: combined query returns results from both paths
- Asserts deduplication: same chunk from both paths appears once in final context

### `tests/test_chain.py`
- Sends a factual question answerable from pre-loaded context
- Asserts answer is non-empty string
- Asserts answer does not contain hallucinated entities absent from context
- Tests "I don't know" response when context has no relevant information

### `tests/test_chatbot.py`
- Multi-turn: first turn asks fact, second turn asks follow-up using pronoun
- Asserts follow-up resolves correctly via memory
- Asserts source citations appear in response when configured
- Asserts chatbot recovers gracefully from Neo4j connection error (fallback to vector only)

Run all tests:
```bash
pytest tests/ -v --tb=short
```

---

## Implementation Order

Build in this sequence to enable incremental testing:

| Step | Module | Validates |
|------|--------|-----------|
| 1 | `config.py` | All env vars load correctly |
| 2 | `ingestion/loader.py` | File discovery works |
| 3 | `ingestion/splitter.py` | Chunking produces correct sizes |
| 4 | `embeddings/gemini_embedder.py` | Gemini API reachable |
| 5 | `vector_store/chroma_store.py` | ChromaDB persists and retrieves |
| 6 | `knowledge_graph/extractor.py` | spaCy extracts entities/triples |
| 7 | `knowledge_graph/neo4j_store.py` | Neo4j writes and reads triples |
| 8 | `ingestion/pipeline.py` | End-to-end ingestion from both folders |
| 9 | `llm/longcat_client.py` | LLM responds to a test prompt |
| 10 | `retrieval/vector_retriever.py` | Vector search returns ranked results |
| 11 | `retrieval/graph_retriever.py` | Graph traversal returns entity context |
| 12 | `retrieval/hybrid_retriever.py` | Merged context is coherent |
| 13 | `chain/graph_rag_chain.py` | End-to-end RAG answer generation |
| 14 | `chat/chatbot.py` | Multi-turn conversation with memory |
| 15 | `main.py` | CLI wires all components |
| 16 | `tests/` | Full test suite passes |

---

## Neo4j Setup (Local Docker)

```bash
docker run \
  --name neo4j-graphrag \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_neo4j_password \
  -e NEO4J_PLUGINS='["apoc", "n10s"]' \
  -v neo4j_data:/data \
  neo4j:5.19-community
```

Browser UI: `http://localhost:7474`

---

## Swapping LLM to Local Docker Model (Ollama)

1. Run Ollama:
   ```bash
   docker run -d -p 11434:11434 --name ollama ollama/ollama
   docker exec -it ollama ollama pull llama3
   ```
2. Update `.env`:
   ```env
   LONGCAT_API_BASE=http://localhost:11434/v1
   LONGCAT_MODEL=llama3
   LONGCAT_API_KEY=ollama
   ```
3. No code changes required.

---

## Deployment Notes

- **Package**: `graph_rag/` is a self-contained Python package; import it into any FastAPI/Flask app
- **API server**: wrap `chatbot.chat()` in a POST `/chat` endpoint
- **Scaling ChromaDB**: replace with a hosted Chroma server or swap to Pinecone/Weaviate by implementing the same interface in `vector_store/`
- **Scaling Neo4j**: point `NEO4J_URI` at Neo4j AuraDB or a self-hosted cluster — no code changes
- **Scaling embeddings**: replace `gemini_embedder.py` with any `langchain_core.embeddings.Embeddings` subclass
- **Config management**: `settings` uses pydantic-settings; inject via environment or a secrets manager in production
