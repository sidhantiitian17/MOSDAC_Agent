# `graph_rag/embeddings/` — Text → Vectors (Ollama / bge-large)

This tiny but **load-bearing** package turns text into embedding vectors by calling the
**Ollama** HTTP server running the **`bge-large`** model. Embeddings are the common
currency of the whole system: semantic search, reranking, entity resolution, community
search, **and** the guardrails (scope gate + injection detection) all embed through here.

> No model weights are loaded in-process — embeddings are an HTTP call to Ollama
> (`OLLAMA_BASE_URL`, default `http://localhost:11434`).

---

## File-by-file

### [ollama_embedder.py](ollama_embedder.py) — the embedder
`OllamaEmbedder` implements the LangChain `Embeddings` interface against Ollama's API.
- **`embed_query(text)`** — embeds a single query. Applies the **bge asymmetric prefix**
  (`EMBED_QUERY_INSTRUCTION`, "Represent this sentence for searching relevant passages:")
  which measurably improves recall, and uses a **process-level LRU cache**
  (`EMBED_QUERY_CACHE_SIZE`) so the same query — embedded several times per request
  (injection check, scope gate, vector search, passage rerank, graph rerank) — is computed
  once.
- **`embed_documents(texts)`** — batch-embeds passages via Ollama's **native batch
  endpoint** (`/api/embed`, one round-trip for N inputs), falling back automatically to
  per-item `/api/embeddings` on older Ollama builds. Document embeddings are **not** cached
  (unbounded text). Batch size capped by `OLLAMA_EMBED_BATCH_SIZE`.
- **`get_embedder()`** — returns a shared singleton instance (so the cache is shared).
- **Depends on:** `config`, `requests`. **External service:** Ollama.

### [__init__.py](__init__.py)
Re-exports `get_embedder` — the **one** function the rest of the codebase imports.

---

## Who calls `get_embedder()` (consumers)

This is the most widely-shared dependency after `config`:

| Caller | Why |
|--------|-----|
| [vector_store/chroma_store.py](../vector_store/chroma_store.py) | Embed chunks on ingest, embed queries on search. |
| [retrieval/vector_retriever.py](../retrieval/vector_retriever.py) | Semantic search. |
| [retrieval/bm25_retriever.py](../retrieval/bm25_retriever.py) | Reads embeddings/text from Chroma. |
| [retrieval/rerankers.py](../retrieval/rerankers.py) + `_rank_utils.py` | Bi-encoder cosine rerank. |
| [knowledge_graph/resolver.py](../knowledge_graph/resolver.py) | Embedding-based entity canonicalization. |
| [knowledge_graph/community.py](../knowledge_graph/community.py) | Community summary search. |
| [guardrails/input/scope.py](../../guardrails/input/scope.py) | On-topic centroid gate. |
| [guardrails/input/injection.py](../../guardrails/input/injection.py) | Embedding-similarity injection detection. |
| [guardrails/output/grounding_check.py](../../guardrails/output/grounding_check.py) | Sentence-grounding cosine checks. |
| [graph_rag/health.py](../health.py) | `check_embedder` readiness probe. |

> ⚠️ **Don't change the embedding model casually.** `bge-large` produces fixed-dimension
> vectors; switching models changes the dimension and **invalidates the existing
> ChromaDB index** — you'd have to re-ingest with `--force`. This is recorded in project
> memory (the model must be `bge-large`, not `nomic-embed-text`).
