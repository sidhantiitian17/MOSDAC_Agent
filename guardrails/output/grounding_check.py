"""Sentence-level and numeric grounding checks against retrieved passages (L4).

Uses the already-loaded bge-large (Ollama) embedder — no new model.

numeric_grounding:  every number in the answer must appear verbatim in the context.
                    (Generalises IterativeReasoner._self_check beyond single numbers.)
sentence_grounding: each factual sentence must have cosine-similarity >= threshold
                    with at least one retrieved passage.  Fails open on embedder error.
"""
from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# A number, allowing thousands separators and a decimal part (1,400 / 4.5 / 360).
# Word-boundary anchored so digits inside identifiers/citations (INSAT-3D, [S1])
# are NOT treated as numeric claims.
_NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Trailing "SOURCES: [S1, S3]" footer — preserved across enforcement edits.
_SOURCES_FOOTER = re.compile(r"\s*SOURCES:\s*\[[^\]]*\]\s*$", re.IGNORECASE)
_MIN_FACTUAL_LEN = 25  # sentences shorter than this are treated as non-factual


def _cosine_sim(a: list[float], b: list[float]) -> float:
    import numpy as np
    a_arr, b_arr = map(np.array, (a, b))
    denom = float(np.linalg.norm(a_arr) * np.linalg.norm(b_arr)) + 1e-9
    return float(np.dot(a_arr, b_arr) / denom)


def _normalize_number(tok: str) -> str:
    """Canonical form so 1,400≡1400 and 4.50≡4.5 compare equal."""
    t = tok.replace(",", "")
    if "." in t:
        t = t.rstrip("0").rstrip(".")
    return t


def check_numeric_grounding(answer: str, context: str) -> Tuple[bool, List[str]]:
    """
    Every number in *answer* must be supported by *context*, compared in a
    normalized form (thousands separators stripped, trailing decimal zeros
    dropped) so formatting differences are not flagged as hallucinations while
    genuine fabricated values still are.

    Returns (all_grounded, list_of_unsupported_numbers).
    """
    nums = {_normalize_number(n) for n in _NUMBER_RE.findall(answer)}
    nums.discard("")
    if not nums:
        return True, []
    ctx_nums = {_normalize_number(n) for n in _NUMBER_RE.findall(context)}
    unsupported = sorted(n for n in nums if n not in ctx_nums)
    if unsupported:
        logger.warning("Ungrounded numbers in output: %s", unsupported)
    return not unsupported, unsupported


def check_sentence_grounding(
    answer: str,
    passages: List[str],
    min_sim: float,
) -> Tuple[bool, List[str]]:
    """
    Each factual sentence in *answer* must have max cosine-similarity >= *min_sim*
    with at least one retrieved passage.

    Returns (all_grounded, list_of_ungrounded_sentences).
    Fails open — returns (True, []) if the embedder is unavailable.
    """
    if not passages:
        return True, []

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(answer) if len(s.strip()) > 25]
    if not sentences:
        return True, []

    try:
        from graph_rag.embeddings import get_embedder

        embedder = get_embedder()
        passage_vecs = embedder.embed_documents(passages[:12])  # cap for speed
        sentence_vecs = embedder.embed_documents(sentences)

        ungrounded: List[str] = []
        for sent, svec in zip(sentences, sentence_vecs):
            max_sim = max(_cosine_sim(svec, pvec) for pvec in passage_vecs)
            if max_sim < min_sim:
                logger.debug("Ungrounded sentence (sim=%.3f): %.80s", max_sim, sent)
                ungrounded.append(sent)

        return not ungrounded, ungrounded

    except Exception as exc:
        logger.debug("Sentence grounding check skipped (fail-open): %s", exc)
        return True, []


def _factual_sentences(text: str) -> List[str]:
    """Sentences long enough to carry a factual claim (mirrors the grounding split)."""
    body = _SOURCES_FOOTER.sub("", text)
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if len(s.strip()) > _MIN_FACTUAL_LEN]


def strip_ungrounded(answer: str, ungrounded_sentences: List[str]) -> str:
    """Remove ungrounded sentences, preserving any trailing ``SOURCES:`` footer.

    Returns the empty string if nothing factual survives, so the caller can refuse
    rather than emit a hollow answer.
    """
    bad = {s.strip() for s in ungrounded_sentences if s.strip()}
    if not bad:
        return answer
    footer_m = _SOURCES_FOOTER.search(answer)
    footer = footer_m.group(0).strip() if footer_m else ""
    body = answer[: footer_m.start()] if footer_m else answer

    kept = [s for s in _SENTENCE_SPLIT_RE.split(body) if s.strip() and s.strip() not in bad]
    new_body = " ".join(p.strip() for p in kept).strip()
    if not new_body:
        return ""
    return f"{new_body}\n{footer}".strip() if footer else new_body

