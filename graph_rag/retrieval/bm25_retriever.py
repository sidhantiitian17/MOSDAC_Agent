"""BM25 keyword retriever — exact-term matching over ChromaDB chunks.

Builds a BM25Okapi index lazily on first query and caches it for the lifetime
of the instance.  Used alongside VectorRetriever in HybridRetriever to catch
exact technical terms (sensor IDs, acronyms, numeric values) that semantic
embeddings may under-rank.
"""
from __future__ import annotations

import logging

from graph_rag.config import settings
from graph_rag.retrieval.vector_retriever import VectorHit
from graph_rag.text_features import tokenize_symbolic
from graph_rag.vector_store.chroma_store import ChromaStore

logger = logging.getLogger(__name__)


class BM25Retriever:
    """Keyword retriever using BM25Okapi over the full ChromaDB chunk corpus.

    Tokenization is symbol-aware (``tokenize_symbolic``) so LaTeX commands,
    operators, Greek letters and sub/superscripts survive into the index and the
    query — making formulas and exact technical notation keyword-searchable
    instead of being stripped by a ``\\w+`` tokenizer.
    """

    def __init__(self, store: ChromaStore | None = None, k: int | None = None) -> None:
        self._store = store
        self._k = k or settings.top_k_bm25
        # Lazy-initialised on first retrieve() call (or eagerly via warm())
        self._bm25 = None
        self._docs: list[str] = []
        self._ids: list[str] = []
        self._sources: list[str] = []
        self._metas: list[dict] = []
        # Corpus size the index was built against — used to detect re-ingest (P1-4).
        self._indexed_count: int = -1

    @property
    def store(self) -> ChromaStore:
        if self._store is None:
            from graph_rag.embeddings import get_embedder
            self._store = ChromaStore(embedder=get_embedder())
        return self._store

    def reset(self) -> None:
        """Drop the in-memory index so the next retrieve() rebuilds it (P1-4)."""
        self._bm25 = None
        self._docs = []
        self._ids = []
        self._sources = []
        self._metas = []
        self._indexed_count = -1

    def warm(self) -> None:
        """Eagerly build the index (startup warm-up) so no user request pays for it."""
        self._build_index()

    def _stale(self) -> bool:
        """True when the underlying collection changed since the index was built."""
        if self._bm25 is None:
            return True
        if not settings.bm25_auto_refresh:
            return False
        try:
            return self.store.count() != self._indexed_count
        except Exception:
            return False

    def _build_index(self) -> None:
        # Rebuild when never built, or when the corpus changed and auto-refresh is on.
        if not self._stale():
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as exc:
            raise ImportError(
                "rank-bm25 not installed. Run: pip install rank-bm25"
            ) from exc

        raw = self.store.get_all_chunks()
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        ids = raw.get("ids") or []

        if not docs:
            logger.info("BM25: ChromaDB collection is empty — index skipped.")
            return

        self._docs = [d or "" for d in docs]
        self._ids = list(ids)
        self._metas = [m if isinstance(m, dict) else {} for m in metas]
        self._sources = [m.get("source", "unknown") for m in self._metas]

        tokenized = [tokenize_symbolic(d) for d in self._docs]
        self._bm25 = BM25Okapi(tokenized)
        self._indexed_count = len(self._docs)
        logger.info("BM25: index built over %d chunks.", len(self._docs))

    def retrieve(self, query: str, k: int | None = None) -> list[VectorHit]:
        self._build_index()
        if self._bm25 is None:
            return []

        top_k = k or self._k
        query_tokens = tokenize_symbolic(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)

        # Argsort descending, keep only positive-scoring results
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        max_score = scores[ranked[0]] if ranked else 0.0

        hits: list[VectorHit] = []
        for i in ranked[:top_k]:
            if scores[i] <= 0:
                break
            norm = float(scores[i] / (max_score + 1e-9))
            hits.append(
                VectorHit(
                    text=self._docs[i],
                    source=self._sources[i],
                    score=norm,
                    chunk_id=self._ids[i],
                    relevance=norm,       # normalized keyword strength, 0..1
                    metadata=self._metas[i],
                )
            )
        return hits

    def exact_match(self, fragments: list[str], limit: int = 5) -> list[VectorHit]:
        """Verbatim substring search for symbol/formula fragments over the corpus.

        Runs over the same in-memory chunk list as the BM25 index (no extra DB
        round-trip). A chunk that literally contains an exactly-stated formula is
        returned with ``relevance=1.0`` so the fusion layer can rank it at the top
        — this is how an exactly-typed equation is retrieved verbatim regardless
        of embedding quality.
        """
        from graph_rag.text_features import normalize_for_match

        self._build_index()
        if not self._docs or not fragments:
            return []
        needles = [n for n in (normalize_for_match(f) for f in fragments) if len(n) >= 2]
        if not needles:
            return []

        hits: list[VectorHit] = []
        for i, doc in enumerate(self._docs):
            hay = normalize_for_match(doc)
            if any(n in hay for n in needles):
                hits.append(
                    VectorHit(
                        text=doc,
                        source=self._sources[i],
                        score=1.0,
                        chunk_id=self._ids[i],
                        relevance=1.0,
                        metadata=self._metas[i],
                    )
                )
                if len(hits) >= limit:
                    break
        return hits
