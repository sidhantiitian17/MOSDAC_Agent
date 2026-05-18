"""BM25 keyword retriever — exact-term matching over ChromaDB chunks.

Builds a BM25Okapi index lazily on first query and caches it for the lifetime
of the instance.  Used alongside VectorRetriever in HybridRetriever to catch
exact technical terms (sensor IDs, acronyms, numeric values) that semantic
embeddings may under-rank.
"""
from __future__ import annotations

import logging
import re

from graph_rag.config import settings
from graph_rag.retrieval.vector_retriever import VectorHit
from graph_rag.vector_store.chroma_store import ChromaStore

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+")


class BM25Retriever:
    """Keyword retriever using BM25Okapi over the full ChromaDB chunk corpus."""

    def __init__(self, store: ChromaStore | None = None, k: int | None = None) -> None:
        self._store = store
        self._k = k or settings.top_k_bm25
        # Lazy-initialised on first retrieve() call
        self._bm25 = None
        self._docs: list[str] = []
        self._ids: list[str] = []
        self._sources: list[str] = []

    @property
    def store(self) -> ChromaStore:
        if self._store is None:
            from graph_rag.embeddings.nomic_embedder import get_embedder
            self._store = ChromaStore(embedder=get_embedder())
        return self._store

    def _build_index(self) -> None:
        if self._bm25 is not None:
            return
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as exc:
            raise ImportError(
                "rank-bm25 not installed. Run: pip install rank-bm25"
            ) from exc

        try:
            raw = self.store.store._collection.get(include=["documents", "metadatas"])
        except Exception as exc:
            logger.warning("BM25: could not fetch chunks from ChromaDB: %s", exc)
            self._bm25 = None
            return

        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        ids = raw.get("ids") or []

        if not docs:
            logger.info("BM25: ChromaDB collection is empty — index skipped.")
            return

        self._docs = [d or "" for d in docs]
        self._ids = list(ids)
        self._sources = [
            (m.get("source", "unknown") if isinstance(m, dict) else "unknown")
            for m in metas
        ]

        tokenized = [_TOKEN_RE.findall(d.lower()) for d in self._docs]
        self._bm25 = BM25Okapi(tokenized)
        logger.info("BM25: index built over %d chunks.", len(self._docs))

    def retrieve(self, query: str, k: int | None = None) -> list[VectorHit]:
        self._build_index()
        if self._bm25 is None:
            return []

        top_k = k or self._k
        query_tokens = _TOKEN_RE.findall(query.lower())
        scores = self._bm25.get_scores(query_tokens)

        # Argsort descending, keep only positive-scoring results
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        max_score = scores[ranked[0]] if ranked else 0.0

        hits: list[VectorHit] = []
        for i in ranked[:top_k]:
            if scores[i] <= 0:
                break
            hits.append(
                VectorHit(
                    text=self._docs[i],
                    source=self._sources[i],
                    score=float(scores[i] / (max_score + 1e-9)),
                    chunk_id=self._ids[i],
                )
            )
        return hits
