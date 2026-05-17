"""Semantic search via ChromaDB; returns formatted passages with source attribution."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from graph_rag.config import settings
from graph_rag.vector_store.chroma_store import ChromaStore

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    text: str
    source: str
    score: float
    chunk_id: str


class VectorRetriever:
    def __init__(self, store: ChromaStore | None = None, k: int | None = None):
        from graph_rag.embeddings.bge_embedder import get_embedder

        self._store = store or ChromaStore(embedder=get_embedder())
        self._k = k or settings.top_k_vector

    def retrieve(self, query: str, k: int | None = None) -> list[VectorHit]:
        try:
            results = self._store.similarity_search_with_score(query, k=k or self._k)
        except Exception as exc:
            logger.warning("Vector retrieval failed: %s", exc)
            return []
        hits: list[VectorHit] = []
        for doc, score in results:
            hits.append(
                VectorHit(
                    text=doc.page_content,
                    source=doc.metadata.get("source", "unknown"),
                    score=float(score),
                    chunk_id=doc.metadata.get("chunk_id", ""),
                )
            )
        return hits

    def as_context(self, query: str, k: int | None = None) -> str:
        hits = self.retrieve(query, k=k)
        if not hits:
            return "(no relevant passages found)"
        return "\n\n".join(
            f"[Source: {h.source} | score={h.score:.3f}]\n{h.text}" for h in hits
        )

    def __call__(self, query: str) -> str:
        return self.as_context(query)
