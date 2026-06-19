"""Shared embedding-rerank helpers for retrieval.

Both the passage reranker (HybridRetriever) and the graph-path reranker
(GraphRetriever) rank candidates by cosine similarity of their embedding to the
query embedding, then keep the top-k. This module centralizes that math and the
embed-and-sort flow so it lives in one place (DRY).

Reranking degrades gracefully: if the embedder is unavailable or raises, the
original order is preserved (truncated to ``top_k``), so retrieval keeps working
when embeddings are down.
"""
from __future__ import annotations

import logging
from typing import Callable, Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two vectors (0 if either is degenerate)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb + 1e-12)


def rerank_by_embedding(
    query: str,
    items: list[T],
    to_text: Callable[[T], str],
    embedder,
    top_k: int,
) -> list[T]:
    """Rank ``items`` by cosine similarity of their text embedding to ``query``.

    ``to_text`` serializes each item to the string that gets embedded. Falls back
    to the original order (truncated to ``top_k``) when the embedder is missing
    or raises.
    """
    if len(items) <= 1:
        return items[:top_k]
    try:
        qv = embedder.embed_query(query)
        dvs = embedder.embed_documents([to_text(it) for it in items])
    except Exception as exc:  # noqa: BLE001 — degrade gracefully when embeddings are down
        logger.debug("Embedding rerank unavailable: %s", exc)
        return items[:top_k]
    ranked = sorted(zip(items, dvs), key=lambda pair: cosine(qv, pair[1]), reverse=True)
    return [it for it, _ in ranked][:top_k]
