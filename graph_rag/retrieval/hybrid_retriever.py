"""Combine vector + BM25 keyword + graph retrieval into a single context block.

Vector and BM25 results are fused via Reciprocal Rank Fusion (RRF) before being
formatted as the vector_context.  Graph context is assembled independently.

Each retriever can fail independently — the system degrades gracefully to
whatever sources are available.
"""
from __future__ import annotations

import logging

from graph_rag.config import settings
from graph_rag.retrieval.bm25_retriever import BM25Retriever
from graph_rag.retrieval.graph_retriever import GraphRetriever
from graph_rag.retrieval.vector_retriever import VectorHit, VectorRetriever

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Merges semantic (vector), keyword (BM25), and graph retrieval for the RAG prompt."""

    def __init__(
        self,
        vector: VectorRetriever | None = None,
        graph: GraphRetriever | None = None,
        bm25: BM25Retriever | None = None,
    ):
        self._vector = vector
        self._graph = graph
        self._bm25 = bm25
        self._embedder = None  # lazy — only built when passage rerank is on

    def _get_embedder(self):
        if self._embedder is None:
            from graph_rag.embeddings import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    @property
    def vector(self) -> VectorRetriever:
        if self._vector is None:
            self._vector = VectorRetriever()
        return self._vector

    @property
    def graph(self) -> GraphRetriever:
        if self._graph is None:
            self._graph = GraphRetriever()
        return self._graph

    @property
    def bm25(self) -> BM25Retriever:
        if self._bm25 is None:
            # Share the same ChromaStore instance as the vector retriever to avoid
            # loading the BGE model twice.
            self._bm25 = BM25Retriever(store=self.vector._store)
        return self._bm25

    @staticmethod
    def _rrf_fuse(
        vec_hits: list[VectorHit],
        bm25_hits: list[VectorHit],
        rrf_k: int = 60,
    ) -> list[VectorHit]:
        """Reciprocal Rank Fusion: score(d) = Σ 1/(rrf_k + rank + 1) across lists."""
        rrf: dict[str, float] = {}
        first_hit: dict[str, VectorHit] = {}

        for rank, hit in enumerate(vec_hits):
            rrf[hit.chunk_id] = rrf.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank + 1)
            first_hit.setdefault(hit.chunk_id, hit)

        for rank, hit in enumerate(bm25_hits):
            rrf[hit.chunk_id] = rrf.get(hit.chunk_id, 0.0) + 1.0 / (rrf_k + rank + 1)
            first_hit.setdefault(hit.chunk_id, hit)

        sorted_ids = sorted(rrf, key=lambda cid: rrf[cid], reverse=True)
        return [
            VectorHit(
                text=first_hit[cid].text,
                source=first_hit[cid].source,
                score=rrf[cid],
                chunk_id=cid,
            )
            for cid in sorted_ids
        ]

    @staticmethod
    def _format_hits(hits: list[VectorHit]) -> str:
        if not hits:
            return "(no relevant passages found)"
        return "\n\n".join(
            f"[Source: {h.source} | score={h.score:.4f}]\n{h.text}" for h in hits
        )

    def _rerank_passages(self, query: str, hits: list[VectorHit]) -> list[VectorHit]:
        """Embedding-rerank the fused candidate pool; keep the most relevant few."""
        top = settings.top_k_passages
        candidates = hits[: settings.rerank_candidate_pool]
        if len(candidates) <= 1:
            return candidates[:top]
        try:
            embedder = self._get_embedder()
        except Exception as exc:
            logger.debug("Passage rerank embedder unavailable: %s", exc)
            return candidates[:top]

        from graph_rag.retrieval._rank_utils import rerank_by_embedding

        return rerank_by_embedding(query, candidates, lambda h: h.text, embedder, top)

    def retrieve(self, query: str) -> dict[str, str]:
        # When reranking, pull a wider candidate pool from each source so the
        # reranker has more to choose from; otherwise keep the default top-k.
        rerank = settings.enable_passage_rerank
        pool = settings.rerank_candidate_pool if rerank else None

        # Vector (semantic) hits
        try:
            vec_hits = self.vector.retrieve(query, k=pool)
        except Exception as exc:
            logger.warning("Vector retrieval unavailable: %s", exc)
            vec_hits = []

        # BM25 (keyword) hits
        try:
            bm25_hits = self.bm25.retrieve(query, k=pool)
        except Exception as exc:
            logger.warning("BM25 retrieval unavailable: %s", exc)
            bm25_hits = []

        # Fuse with RRF, optionally rerank by relevance, then format
        fused = self._rrf_fuse(vec_hits, bm25_hits, rrf_k=settings.hybrid_rrf_k)
        if rerank:
            fused = self._rerank_passages(query, fused)
        vector_context = self._format_hits(fused)

        # Graph context (unchanged path)
        try:
            graph_context = self.graph.as_context(query)
        except Exception as exc:
            logger.warning("Graph retrieval unavailable: %s", exc)
            graph_context = "(knowledge graph unavailable)"

        return {
            "vector_context": vector_context,
            "graph_context": graph_context,
            "_hits": fused,  # raw VectorHit list used by grounding gate / citation registry
        }
