# `graph_rag/` — The RAG Core

This package is the **engine** of the project: everything that turns documents into
searchable knowledge and turns a question into a grounded answer. The
[chat_api/](../chat_api/) gateway and the [guardrails/](../guardrails/) wrap around it,
but the actual retrieval-augmented-generation happens here.

> Read [readme_main.md](../readme_main.md) first for the two end-to-end pipelines. This
> file is the map of the RAG core; each sub-folder has its own deep-dive `README.md`.

---

## Sub-packages (the assembly line)

The core is organized as a pipeline. Two halves:

**Ingestion half** (offline — builds the stores):

| Folder | Role |
|--------|------|
| [ingestion/](ingestion/) | Discover → load → split documents; the format registry, quality gate, manifest, and the top-level `IngestionPipeline`. |
| [preprocessing/](preprocessing/) | Docling-based cleaning, header-aware chunking, math/table safety, chunk enrichment. |
| [embeddings/](embeddings/) | Turn text into vectors via Ollama (`bge-large`) over HTTP. |
| [vector_store/](vector_store/) | The ChromaDB wrapper (persistent, idempotent). |
| [knowledge_graph/](knowledge_graph/) | Extract typed triples + quantities, resolve entities, store the graph in Neo4j, build community summaries. |

**Query half** (online — answers questions):

| Folder | Role |
|--------|------|
| [retrieval/](retrieval/) | Vector + BM25 + graph retrieval, RRF fusion, reranking, history-aware contextualization, query planning. |
| [chain/](chain/) | The LCEL RAG chain (prompt assembly → LLM → string) and the iterative reasoner. |
| [llm/](llm/) | The Tabby ML client (`get_llm`) + the process-wide concurrency throttle (`llm_slot`). |
| [chat/](chat/) | The stateful CLI chatbot + the rolling conversation summarizer. |
| [eval/](eval/) | The RAGAS production gate, custom metrics, scorecard, and golden-dataset loader. |

---

## Top-level files

### [config.py](config.py) — `Settings` (the master knob board)
**The single source of truth for RAG-core configuration.** A `pydantic-settings` class
that loads everything from `.env`: Neo4j, ChromaDB, OCR/Docling, ingestion + quality gate,
chunking, retrieval (top-k, RRF, rerank, feature boost), history-aware retrieval, the LLM
(Tabby) + KG-extraction LLM, embeddings (Ollama), and the system-prompt path. Imported by
**almost every module** in this package as `from graph_rag.config import settings`.
- Notable helpers: `source_folders()` (downloads + atlases), `extraction_model_name()`
  (KG model with fallback to the chat model).

### [health.py](health.py) — shared readiness probes
The **single** implementation of "is each dependency alive?", used by **both** the CLI
(`python main.py test`) and the live `/ready` endpoint — so they can never drift apart.
- **Functions:** `check_embedder`, `check_chroma`, `check_neo4j`, `check_llm`, and
  `readiness(cache_seconds, include_llm)` which aggregates them into a report.
- **Depends on:** `embeddings.get_embedder`, `vector_store.ChromaStore`,
  `knowledge_graph.neo4j_store.Neo4jStore`, `llm.tabby_client.get_llm`.
- **Used by:** [main.py](../main.py) `cmd_test`, [chat_api/routes.py](../chat_api/routes.py)
  `/ready`, and the Docker healthcheck.

### [text_features.py](text_features.py) — shared text heuristics
Dependency-light helpers used across ingestion, retrieval, BM25, and eval so structure
detection is consistent everywhere.
- **Functions:** `tokenize_symbolic` (symbol-aware tokenization), `normalize_for_match`,
  `extract_formula_fragments`, `looks_like_formula_query`, `has_formula`, `has_table`,
  `numeric_density`.
- **Used by:** `preprocessing` (chunk enrichment), `retrieval/hybrid_retriever` &
  `bm25_retriever` (feature boost, formula queries), and the eval custom metrics.

### [__init__.py](__init__.py)
Package marker; imports `settings` so `graph_rag` is configured on import.

---

## The dependency backbone

Three things are imported almost everywhere — know these and the rest follows:

```
graph_rag.config.settings        ← every module reads config from here
graph_rag.embeddings.get_embedder ← retrieval, rerankers, resolver, communities,
                                     AND guardrails (scope + injection) all embed via this
graph_rag.llm.tabby_client.get_llm / llm_slot
                                  ← chat, extraction, contextualizer, summarizer, titler
                                     all share one LLM client + one concurrency semaphore
```

External services this package talks to (all over HTTP/Bolt, none loaded in-process):
**Tabby ML** (LLM), **Ollama** (embeddings), **Neo4j** (graph). **ChromaDB** runs
in-process and persists to `CHROMA_PERSIST_DIR`.
