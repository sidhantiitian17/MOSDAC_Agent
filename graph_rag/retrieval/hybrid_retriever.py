"""Combine vector + BM25 keyword + graph retrieval into a single context block.

Vector and BM25 results are fused via Reciprocal Rank Fusion (RRF) before being
formatted as the vector_context.  Graph context is assembled independently.

Precision/grounding boosters layered on top of the base fusion:
  * Exact-formula fast path — when the query carries math notation, chunks that
    contain the verbatim symbol run are injected at the top (retrieves an
    exactly-stated formula regardless of embedding quality).
  * Feature boost — for numeric/formula queries, chunks tagged ``has_formula`` /
    high ``numeric_density`` get a small ranking boost so the right chunk reaches
    the rerank window.
  * Pluggable rerank — a cross-encoder (or bi-encoder fallback) re-scores the
    fused pool and writes a normalized ``relevance`` the L2 grounding gate reuses.

Each retriever can fail independently — the system degrades gracefully to
whatever sources are available.
"""
from __future__ import annotations

import logging
import re

from graph_rag.config import settings
from graph_rag.retrieval.bm25_retriever import BM25Retriever
from graph_rag.retrieval.graph_retriever import GraphRetriever
from graph_rag.retrieval.vector_retriever import VectorHit, VectorRetriever
from graph_rag.text_features import (
    extract_formula_fragments,
    looks_like_formula_query,
)

logger = logging.getLogger(__name__)

# A digit anywhere → the query is "quantitative" and the feature boost applies.
_HAS_NUMBER = re.compile(r"\d")


def _sanitize_context(text: str) -> str:
    """Neutralize injection directives in retrieved context (P1-3); no-op if disabled.

    Lazy import keeps the retriever importable without the guardrails layer and
    never lets a sanitizer error break retrieval.
    """
    try:
        from guardrails.input.injection import sanitize_context

        return sanitize_context(text)
    except Exception:
        return text


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

    def warm(self) -> None:
        """Eagerly build the BM25 index at startup so no user request pays the
        cold-start cost (P1-4). Safe to call repeatedly; degrades quietly."""
        try:
            self.bm25.warm()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 warm-up skipped: %s", exc)

    def reload(self) -> None:
        """Pick up a re-ingest without a restart (P1-4): rebuild the keyword index
        and drop cached query embeddings so fresh corpus content is searchable."""
        try:
            if self._bm25 is not None:
                self._bm25.reset()
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 reset failed: %s", exc)
        try:
            emb = self._get_embedder()
            if hasattr(emb, "clear_query_cache"):
                emb.clear_query_cache()
        except Exception:
            pass

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
        """Reciprocal Rank Fusion: score(d) = Σ 1/(rrf_k + rank + 1) across lists.

        The fused hit keeps an RRF ``score`` for ordering but ALSO carries the max
        per-channel ``relevance`` (a real [0,1] cosine/keyword strength), so the
        grounding gate downstream judges on a meaningful scale rather than the
        tiny RRF magnitudes.
        """
        rrf: dict[str, float] = {}
        best_rel: dict[str, float] = {}
        first_hit: dict[str, VectorHit] = {}

        for hits in (vec_hits, bm25_hits):
            for rank, hit in enumerate(hits):
                cid = hit.chunk_id
                rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (rrf_k + rank + 1)
                best_rel[cid] = max(best_rel.get(cid, 0.0), hit.relevance or 0.0)
                first_hit.setdefault(cid, hit)

        sorted_ids = sorted(rrf, key=lambda cid: rrf[cid], reverse=True)
        return [
            VectorHit(
                text=first_hit[cid].text,
                source=first_hit[cid].source,
                score=rrf[cid],
                chunk_id=cid,
                relevance=best_rel[cid],
                metadata=first_hit[cid].metadata,
            )
            for cid in sorted_ids
        ]

    @staticmethod
    def _format_hits(hits: list[VectorHit]) -> str:
        if not hits:
            return "(no relevant passages found)"
        return "\n\n".join(
            f"[Source: {h.source} | score={(h.relevance or h.score):.4f}]\n{h.text}"
            for h in hits
        )

    def _expand_parent(self, hit: VectorHit) -> str:
        """Reconstruct a hit's full parent section from its sibling child chunks.

        Children of an over-long section share a ``parent_id`` (set by the
        preprocessor). Returns the ordered concatenation so the LLM sees full
        context, while the precise child stays in ``_hits`` for grounding. Falls
        back to the child text if siblings can't be fetched.
        """
        pid = (hit.metadata or {}).get("parent_id")
        if not pid:
            return hit.text
        try:
            raw = self.vector._store.get_by_metadata({"parent_id": pid})
        except Exception:
            return hit.text
        docs = raw.get("documents") or []
        metas = raw.get("metadatas") or []
        if not docs:
            return hit.text
        ordered = sorted(
            zip(metas, docs), key=lambda md: (md[0] or {}).get("section_part", 0)
        )
        return "\n".join(d for _, d in ordered if d)

    def _format_context(self, hits: list[VectorHit]) -> str:
        """Format passages for the LLM, optionally expanding to parent sections."""
        if not settings.enable_parent_expansion:
            return self._format_hits(hits)
        blocks: list[str] = []
        seen_parents: set[str] = set()
        for h in hits:
            pid = (h.metadata or {}).get("parent_id")
            if pid and pid in seen_parents:
                continue
            if pid:
                seen_parents.add(pid)
            body = self._expand_parent(h) if pid else h.text
            rel = h.relevance or h.score
            blocks.append(f"[Source: {h.source} | score={rel:.4f}]\n{body}")
        return "\n\n".join(blocks) if blocks else "(no relevant passages found)"

    @staticmethod
    def _merge_exact_first(exact: list[VectorHit], fused: list[VectorHit]) -> list[VectorHit]:
        """Prepend verbatim-match hits, de-duplicating by chunk_id (exact wins)."""
        if not exact:
            return fused
        seen = {h.chunk_id for h in exact}
        return exact + [h for h in fused if h.chunk_id not in seen]

    @staticmethod
    def _apply_feature_boost(query: str, hits: list[VectorHit]) -> list[VectorHit]:
        """For numeric/formula queries, nudge formula/quantitative chunks upward.

        Boosts the RRF ordering ``score`` (not ``relevance``) so the right chunks
        reach the rerank window without inflating the grounding score. No-op when
        the query is not quantitative or the feature boost is disabled.
        """
        if not settings.enable_feature_boost or not hits:
            return hits
        wants_numbers = bool(_HAS_NUMBER.search(query)) or looks_like_formula_query(query)
        if not wants_numbers:
            return hits
        w = settings.feature_boost_weight
        for h in hits:
            meta = h.metadata or {}
            if meta.get("has_formula"):
                h.score *= (1.0 + w)
            h.score *= (1.0 + w * float(meta.get("numeric_density", 0.0) or 0.0))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def _rerank_passages(self, query: str, hits: list[VectorHit]) -> list[VectorHit]:
        """Rerank the fused candidate pool; keep the most relevant few."""
        top = settings.top_k_passages
        candidates = hits[: settings.rerank_candidate_pool]
        if len(candidates) <= 1:
            return candidates[:top]
        try:
            embedder = self._get_embedder()
        except Exception as exc:
            logger.debug("Passage rerank embedder unavailable: %s", exc)
            return candidates[:top]

        from graph_rag.retrieval.rerankers import get_reranker

        return get_reranker(embedder).rerank(query, candidates, top)

    def _exact_formula_hits(self, query: str) -> list[VectorHit]:
        """Verbatim symbol-run matches for a math query (empty otherwise)."""
        if not settings.enable_exact_formula_match or not looks_like_formula_query(query):
            return []
        fragments = extract_formula_fragments(query)
        if not fragments:
            return []
        try:
            return self.bm25.exact_match(fragments, limit=settings.top_k_passages)
        except Exception as exc:
            logger.debug("Exact-formula match unavailable: %s", exc)
            return []

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

        # Fuse with RRF, boost quantitative/formula chunks, then rerank.
        fused = self._rrf_fuse(vec_hits, bm25_hits, rrf_k=settings.hybrid_rrf_k)
        fused = self._apply_feature_boost(query, fused)
        if rerank:
            fused = self._rerank_passages(query, fused)

        # Inject verbatim formula matches AFTER rerank so an exactly-stated formula
        # keeps its relevance=1.0 and can't be demoted/dropped by the reranker.
        fused = self._merge_exact_first(self._exact_formula_hits(query), fused)
        vector_context = self._format_context(fused)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Hybrid retrieve: vec=%d bm25=%d fused=%d top_rel=%.3f",
                len(vec_hits), len(bm25_hits), len(fused),
                max((h.relevance or h.score for h in fused), default=0.0),
            )

        # Graph context (unchanged path)
        try:
            graph_context = self.graph.as_context(query)
        except Exception as exc:
            logger.warning("Graph retrieval unavailable: %s", exc)
            graph_context = "(knowledge graph unavailable)"

        # Indirect-injection defence (P1-3): neutralize any injection directives
        # smuggled inside retrieved passages BEFORE they reach the prompt. Only the
        # LLM-facing context strings are sanitized; ``_hits`` stays raw so grounding
        # and citation verification still run against the true passage text.
        vector_context = _sanitize_context(vector_context)
        graph_context = _sanitize_context(graph_context)

        return {
            "vector_context": vector_context,
            "graph_context": graph_context,
            "_hits": fused,  # raw VectorHit list used by grounding gate / citation registry
        }
