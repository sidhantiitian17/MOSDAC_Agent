# `graph_rag/vector_store/` — The ChromaDB Vector Store

This package wraps **ChromaDB**, the vector database that stores the embedded document
chunks and answers "find the most semantically similar chunks to this query." It is the
**only datastore that runs in-process** (no server) — it persists to the folder named by
`CHROMA_PERSIST_DIR` (default `./chroma_db`).

---

## File-by-file

### [chroma_store.py](chroma_store.py) — `ChromaStore`
A thin, **idempotent**, persistent wrapper over a Chroma collection.
- **Responsibilities:** open/create the persistent collection (`CHROMA_COLLECTION`), add
  chunks **deduped by `chunk_id`** (re-ingesting the same chunk is a no-op), store text +
  metadata (incl. text-feature tags), run similarity search, and expose the collection
  count (used to detect re-ingests for BM25 auto-refresh).
- **Depends on:** `config`, `chromadb` (and `langchain-chroma`).
- **Used by:** the ingestion pipeline (writes), [retrieval/vector_retriever.py](../retrieval/vector_retriever.py)
  and [retrieval/bm25_retriever.py](../retrieval/bm25_retriever.py) (reads),
  [knowledge_graph/community.py](../knowledge_graph/community.py),
  [graph_rag/health.py](../health.py) (`check_chroma`).

### [__init__.py](__init__.py)
Re-exports `ChromaStore`.

---

## How it fits

```
INGEST:  chunks ──► embeddings.embed_documents ──► ChromaStore.add (dedup by chunk_id)
QUERY:   query  ──► embeddings.embed_query ──────► ChromaStore.similarity_search ──► VectorHit
```

### Operational notes
- **Persistence is a plain folder.** `./chroma_db/` holds `chroma.sqlite3` + the HNSW
  index files. Back it up by copying the folder (see
  [docs/BACKUP_RESTORE.md](../../docs/BACKUP_RESTORE.md)).
- **Read-write even for reads.** ChromaDB opens its SQLite store read-write (WAL +
  migrations) even to *query*, so in Docker the mount must be owned by the app user —
  handled by [docker-entrypoint.sh](../../docker-entrypoint.sh) (chowns `/app/chroma_db`).
- **Idempotency** here (dedup by `chunk_id`) is what makes the whole ingestion pipeline
  safe to re-run.
