"""Semantic search via ChromaDB; returns formatted passages with source attribution."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from graph_rag.config import settings
from graph_rag.vector_store.chroma_store import ChromaStore

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    text: str
    source: str
    score: float            # channel-native ordering score (kept for back-compat)
    chunk_id: str
    # Normalized semantic relevance in [0, 1], higher = better. Comparable across
    # channels and used by the L2 grounding gate. Defaults to 0.0 so callers that
    # only set ``score`` (older tests, graph hits) keep working — the grounding
    # gate falls back to ``score`` when ``relevance`` is unset.
    relevance: float = 0.0
    # Carried chunk metadata (source_type, has_formula, page_number, …). Used for
    # feature-aware boosting and richer citations; never required.
    metadata: dict = field(default_factory=dict)


class VectorRetriever:
    def __init__(self, store: ChromaStore | None = None, k: int | None = None):
        from graph_rag.embeddings import get_embedder

        self._store = store or ChromaStore(embedder=get_embedder())
        self._k = k or settings.top_k_vector
        self._store.check_embedding_compat()

    def retrieve(self, query: str, k: int | None = None) -> list[VectorHit]:
        # similarity_search_with_relevance gives a normalized [0,1] relevance
        # (higher = better) regardless of the collection's distance space, so the
        # grounding gate compares against a meaningful, stable scale — not a raw
        # Chroma distance (where lower = better and the orientation was inverted).
        try:
            results = self._store.similarity_search_with_relevance(query, k=k or self._k)
        except Exception as exc:
            logger.warning("Vector retrieval failed: %s", exc)
            return []
        hits: list[VectorHit] = []
        for doc, relevance in results:
            rel = float(relevance)
            hits.append(
                VectorHit(
                    text=doc.page_content,
                    source=doc.metadata.get("source", "unknown"),
                    score=rel,             # ordering score == relevance for this channel
                    chunk_id=doc.metadata.get("chunk_id", ""),
                    relevance=rel,
                    metadata=dict(doc.metadata),
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
