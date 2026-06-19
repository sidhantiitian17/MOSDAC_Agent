"""Citation verification: model-cited [Sx] IDs must exist in the CitationRegistry (L4).

Every fabricated citation is stripped.  Surviving IDs are resolved to their
{source, chunk_id, snippet} metadata and returned in the response envelope so
the frontend can render clickable provenance links.
"""
from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Inline citation  e.g. [S1] or [S12]
_INLINE_RE = re.compile(r"\[S(\d+)\]")
# SOURCES footer line  e.g. SOURCES: [S1, S3]
_SOURCES_LINE_RE = re.compile(r"SOURCES:\s*\[([^\]]*)\]", re.IGNORECASE)


def extract_cited_ids(text: str) -> set[str]:
    """Return all citation IDs referenced anywhere in the text."""
    ids: set[str] = set()
    for m in _INLINE_RE.finditer(text):
        ids.add(f"S{m.group(1)}")
    m = _SOURCES_LINE_RE.search(text)
    if m:
        for part in m.group(1).split(","):
            part = part.strip()
            if re.match(r"^S\d+$", part):
                ids.add(part)
    return ids


def verify(
    answer: str,
    registry,
) -> Tuple[str, List[dict]]:
    """
    Strip fabricated citations and return (cleaned_answer, verified_citations).

    Args:
        answer:   Raw LLM output.
        registry: CitationRegistry built during the retrieval phase.

    Returns:
        (answer with fabricated citations removed,
         list of {id, source, chunk_id, snippet} for valid citations)
    """
    cited = extract_cited_ids(answer)
    if not cited:
        return answer, []

    valid = registry.all_ids()
    fabricated = cited - valid

    if fabricated:
        logger.warning("Stripping fabricated citation(s): %s", sorted(fabricated))
        for fid in fabricated:
            answer = answer.replace(f"[{fid}]", "")

    def _rebuild_sources(match: re.Match) -> str:
        parts = [p.strip() for p in match.group(1).split(",")]
        good = [p for p in parts if p in valid]
        return f"SOURCES: [{', '.join(good)}]" if good else ""

    answer = _SOURCES_LINE_RE.sub(_rebuild_sources, answer).strip()

    surviving = cited & valid
    citations: List[dict] = []
    for cid in sorted(surviving, key=lambda x: int(x[1:])):
        c = registry.get(cid)
        if c:
            citations.append(
                {"id": c.citation_id, "source": c.source, "chunk_id": c.chunk_id, "snippet": c.text[:200]}
            )

    return answer, citations
