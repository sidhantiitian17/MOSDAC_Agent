"""Relevance-floor grounding gate and per-turn Citation Registry (L2).

This is the CRITICAL structural control that guarantees the bot answers ONLY
when the knowledge base actually supports it.

Flow:
  1. After HybridRetriever.retrieve() returns raw hits, call check_groundable().
  2. If it fails → return the canonical refusal BEFORE calling the LLM.
  3. If it passes → call build_registry_from_hits() to get a CitationRegistry.
  4. Pass the registry to the output guard (L4) for citation verification.

CitationRegistry:
  Maps short IDs (S1, S2, …) to the exact retrieved passages so the L4 guard
  can verify every [Sx] the model emits against the actual retrieved chunks.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    citation_id: str
    source: str
    chunk_id: str
    text: str


class CitationRegistry:
    """Per-turn registry: short IDs → retrieved passages."""

    def __init__(self) -> None:
        self._citations: Dict[str, Citation] = {}
        self._counter: int = 0

    def register(self, source: str, chunk_id: str, text: str) -> str:
        """Register a passage and return its assigned ID (S1, S2, …)."""
        self._counter += 1
        cid = f"S{self._counter}"
        self._citations[cid] = Citation(
            citation_id=cid, source=source, chunk_id=chunk_id, text=text
        )
        return cid

    def get(self, cid: str) -> Optional[Citation]:
        return self._citations.get(cid)

    def all_ids(self) -> set:
        return set(self._citations.keys())

    def as_list(self) -> List[dict]:
        return [
            {
                "id": c.citation_id,
                "source": c.source,
                "chunk_id": c.chunk_id,
                "snippet": c.text[:200],
            }
            for c in self._citations.values()
        ]

    def texts(self) -> List[str]:
        """All passage texts — used by sentence grounding check."""
        return [c.text for c in self._citations.values()]

    def __len__(self) -> int:
        return len(self._citations)

    def __bool__(self) -> bool:
        return len(self._citations) > 0


def check_groundable(
    hits: list,
    min_score: float,
    min_passages: int,
) -> tuple[bool, float]:
    """
    Decide whether retrieved hits meet the relevance floor.

    Args:
        hits:          VectorHit list from HybridRetriever (must have .score attribute).
        min_score:     Minimum acceptable relevance score (e.g. 0.20).
        min_passages:  Minimum number of passages above the floor.

    Returns:
        (passes, top_score)
    """
    if not hits:
        logger.info("Grounding gate: no hits returned")
        return False, 0.0

    top_score = max(h.score for h in hits)
    above_floor = [h for h in hits if h.score >= min_score]

    passes = len(above_floor) >= min_passages and top_score >= min_score
    logger.debug(
        "Grounding gate: top_score=%.4f above_floor=%d/%d min_score=%.2f passes=%s",
        top_score, len(above_floor), len(hits), min_score, passes,
    )
    return passes, top_score


def build_registry_from_hits(
    hits: list,
    manifest_path: str = "",
    check_allowlist: bool = True,
) -> CitationRegistry:
    """Build a CitationRegistry from a VectorHit list.

    Optionally filters hits against the source allowlist (L2 data-poisoning defence).
    """
    from guardrails.retrieval.source_allowlist import is_allowed

    registry = CitationRegistry()
    for hit in hits:
        if check_allowlist and manifest_path:
            if not is_allowed(hit.source, manifest_path):
                logger.warning("Skipping non-allowlisted source: %s", hit.source)
                continue
        registry.register(source=hit.source, chunk_id=hit.chunk_id, text=hit.text)
    return registry
