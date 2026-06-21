"""Pluggable passage rerankers for the hybrid retriever.

Two interchangeable strategies behind one ``rerank(query, hits, top_k)`` call:

  * ``BiEncoderReranker`` — re-sorts by cosine similarity of the SAME bge-large
    embedding used for first-stage retrieval. Cheap, local, always available.
  * ``CrossEncoderReranker`` — scores each (query, passage) pair JOINTLY via a
    configurable HTTP reranker endpoint (e.g. a TEI / BGE-reranker server). This
    is a genuinely stronger relevance signal than the bi-encoder re-sort. It
    degrades to the bi-encoder automatically if the endpoint is unavailable.

Both write a normalized ``relevance`` in [0, 1] onto the returned hits, so the L2
grounding gate downstream sees a clean, comparable score. ``get_reranker``
selects the strategy from config — flip on the cross-encoder in production with
no code change, keeping the bi-encoder as a safety net.
"""
from __future__ import annotations

import logging

from graph_rag.config import settings
from graph_rag.retrieval._rank_utils import cosine
from graph_rag.retrieval.vector_retriever import VectorHit

logger = logging.getLogger(__name__)


class BaseReranker:
    def rerank(self, query: str, hits: list[VectorHit], top_k: int) -> list[VectorHit]:
        raise NotImplementedError


class BiEncoderReranker(BaseReranker):
    """Cosine re-rank using the shared embedder. Always-available default."""

    def __init__(self, embedder) -> None:
        self._embedder = embedder

    def rerank(self, query: str, hits: list[VectorHit], top_k: int) -> list[VectorHit]:
        if len(hits) <= 1:
            return hits[:top_k]
        try:
            qv = self._embedder.embed_query(query)
            dvs = self._embedder.embed_documents([h.text for h in hits])
        except Exception as exc:  # noqa: BLE001 — degrade gracefully
            logger.debug("Bi-encoder rerank unavailable: %s", exc)
            return hits[:top_k]
        scored = []
        for h, dv in zip(hits, dvs):
            h.relevance = max(0.0, min(1.0, cosine(qv, dv)))
            scored.append(h)
        scored.sort(key=lambda h: h.relevance, reverse=True)
        return scored[:top_k]


class CrossEncoderReranker(BaseReranker):
    """Joint (query, passage) scoring via an HTTP reranker, bi-encoder fallback."""

    def __init__(self, base_url: str, model: str, token: str, fallback: BaseReranker) -> None:
        self._url = base_url.rstrip("/") + "/rerank"
        self._model = model
        self._token = token
        self._fallback = fallback

    def rerank(self, query: str, hits: list[VectorHit], top_k: int) -> list[VectorHit]:
        if len(hits) <= 1:
            return hits[:top_k]
        try:
            return self._rerank_remote(query, hits, top_k)
        except Exception as exc:  # noqa: BLE001 — never break retrieval on rerank
            logger.warning("Cross-encoder rerank failed (%s); falling back.", exc)
            return self._fallback.rerank(query, hits, top_k)

    def _rerank_remote(self, query: str, hits: list[VectorHit], top_k: int) -> list[VectorHit]:
        import requests

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        payload = {
            "model": self._model,
            "query": query,
            "documents": [h.text for h in hits],
        }
        resp = requests.post(self._url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            raise ValueError("reranker returned no results")

        ranked: list[VectorHit] = []
        scores = [float(r.get("relevance_score", r.get("score", 0.0))) for r in results]
        smax = max(scores) if scores else 1.0
        for r, raw in zip(results, scores):
            idx = int(r["index"])
            if 0 <= idx < len(hits):
                hit = hits[idx]
                hit.relevance = max(0.0, min(1.0, raw / smax)) if smax > 0 else 0.0
                ranked.append(hit)
        return ranked[:top_k]


def get_reranker(embedder) -> BaseReranker:
    """Select a reranker from config. Bi-encoder is the always-available default."""
    bi = BiEncoderReranker(embedder)
    if settings.enable_cross_encoder_rerank and settings.reranker_base_url:
        return CrossEncoderReranker(
            base_url=settings.reranker_base_url,
            model=settings.reranker_model,
            token=settings.reranker_api_token,
            fallback=bi,
        )
    return bi
