# `graph_rag/retrieval/` — Finding the Right Context

This package answers the central RAG question: **"given a user's query, what are the most
relevant pieces of our knowledge?"** It combines three different search strategies (vector,
keyword, graph), fuses them, reranks the result, and hands a single clean context block to
the LLM. It also rewrites follow-up questions into standalone queries.

> Query-pipeline context: [readme_main.md §7](../../readme_main.md). The orchestrator that
> calls this is [chat_api/service.py](../../chat_api/service.py) and
> [chain/graph_rag_chain.py](../chain/graph_rag_chain.py).

---

## The retrieval flow

```
search query
   │
   ├─► vector_retriever  (semantic, bge-large)   ┐
   ├─► bm25_retriever    (exact keywords)         ├─► Reciprocal Rank Fusion (RRF)
   └─► graph_retriever   (Neo4j 2-hop subgraph)   ┘   in hybrid_retriever
        │
        ▼  feature boost (numeric/formula queries)  + exact-formula verbatim injection
        ▼  passage rerank (rerankers: bi-encoder cosine OR external cross-encoder)
        ▼  context sanitization (indirect-injection defence)
        ▼
   { vector_context, graph_context, _hits }   ──► chain/LLM
```

---

## File-by-file

### [hybrid_retriever.py](hybrid_retriever.py) — `HybridRetriever` (the orchestrator)
**The main entry point.** Runs vector + BM25 + graph retrieval, fuses with **RRF**, applies
the feature boost and exact-formula fast path, reranks the passages, **sanitizes** the
retrieved text against indirect prompt injection, and assembles the final
`vector_context` / `graph_context` / `_hits`.
- **Key pieces:** `HybridRetriever` with `retrieve(query)`, `warm()` (build the BM25 index
  on boot), `reload()` (rebuild after a re-ingest); `_sanitize_context`.
- **Depends on:** `config`, `embeddings`, `retrieval.{vector_retriever, bm25_retriever,
  graph_retriever, rerankers}`, `text_features`, `guardrails.input.injection`
  (`sanitize_context`).
- **Used by:** `chat_api.service`, `chain/graph_rag_chain`, `chain/iterative_chain`,
  the eval harness.

### [vector_retriever.py](vector_retriever.py) — semantic search
Embeds the query and runs similarity search over ChromaDB; returns `VectorHit` objects with
text + source attribution (used everywhere as the canonical hit type).
- **Key pieces:** `VectorHit`, `VectorRetriever`.
- **Depends on:** `config`, `embeddings`, `vector_store.ChromaStore`.

### [bm25_retriever.py](bm25_retriever.py) — keyword search
A **BM25** keyword index built in-memory over the ChromaDB chunks, for exact-term matching
(satellite names, acronyms, symbols) that semantic search can miss. Symbol-aware
tokenization; auto-refreshes when the collection count changes (`BM25_AUTO_REFRESH`).
- **Key class:** `BM25Retriever` (returns `VectorHit`s for uniform fusion).
- **Depends on:** `config`, `embeddings`, `vector_store.ChromaStore`, `text_features`
  (`tokenize_symbolic`, `normalize_for_match`), `rank-bm25`.

### [graph_retriever.py](graph_retriever.py) — knowledge-graph search
Extracts entities from the query, looks them up in Neo4j (fulltext), walks a 2-hop subgraph
(`GRAPH_DEPTH`), and serializes the resulting triples into a readable `graph_context` block.
Optionally embedding-reranks the paths against the question (`GRAPH_RERANK`).
- **Key class:** `GraphRetriever`. *(File begins with a UTF-8 BOM — keep encoding.)*
- **Depends on:** `config`, `knowledge_graph.neo4j_store`, `guardrails.retrieval.cypher_safe`
  (sanitize entity names before Cypher/fulltext).

### [rerankers.py](rerankers.py) — pluggable passage rerankers
Re-scores the fused candidate pool so the most relevant passages reach the LLM.
- **Implementations:** `BiEncoderReranker` (local cosine via embeddings — default, offline),
  `CrossEncoderReranker` (stronger external `/rerank` endpoint, used when
  `ENABLE_CROSS_ENCODER_RERANK` + a reranker URL are set; falls back to the bi-encoder if
  unreachable). `get_reranker()` picks one. `BaseReranker` is the interface.
- **Depends on:** `config`, `retrieval._rank_utils` (`cosine`), `retrieval.vector_retriever`.

### [_rank_utils.py](_rank_utils.py) — shared rerank math
`cosine` similarity + `rerank_by_embedding` helper, shared by the rerankers and graph
rerank so the embedding-rerank logic lives in one place.

### [query_contextualizer.py](query_contextualizer.py) — history-aware rewriting
Rewrites a follow-up ("what's *its* resolution?") into a **standalone** search query using
recent turns, **before** retrieval — so the embedding/keyword search targets the right
entity. Gated: the LLM rewrite only fires on detected follow-ups, so most turns pay nothing.
- **Key pieces:** `QueryContextualizer.contextualize(...) -> ContextualizedQuery`.
- **Depends on:** `config`, `llm.tabby_client`, `retrieval.query_planner` (`_loads_obj`).
- **Used by:** `chat_api.service`, `chain/`.

### [query_planner.py](query_planner.py) — multi-hop query decomposition
Splits a complex question into sub-questions for guided multi-hop retrieval (Phase 6,
opt-in via `ENABLE_QUERY_DECOMPOSITION`). Also provides the tolerant JSON loader (`_loads_obj`)
reused by the contextualizer.
- **Key pieces:** `QueryPlan`, `QueryPlanner`.
- **Depends on:** `config`, `llm.tabby_client`.

### [__init__.py](__init__.py)
Re-exports `VectorRetriever`, `BM25Retriever`, `GraphRetriever`, `HybridRetriever`.

---

## Dependencies at a glance
- **Internal:** `graph_rag.config`, `graph_rag.embeddings`, `graph_rag.vector_store`,
  `graph_rag.knowledge_graph`, `graph_rag.llm`, `graph_rag.text_features`, and
  `guardrails.input.injection` / `guardrails.retrieval.cypher_safe` (security).
- **External:** `rank-bm25`, `chromadb`, `neo4j`.
- **Note on cross-package coupling:** retrieval imports two guardrail helpers
  (context sanitization + Cypher-safe names). This is intentional — defence runs *inside*
  retrieval, not just around it.
