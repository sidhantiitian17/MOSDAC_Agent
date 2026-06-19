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

_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _cosine_sim(a: list[float], b: list[float]) -> float:
    import numpy as np
    a_arr, b_arr = map(np.array, (a, b))
    denom = float(np.linalg.norm(a_arr) * np.linalg.norm(b_arr)) + 1e-9
    return float(np.dot(a_arr, b_arr) / denom)


def check_numeric_grounding(answer: str, context: str) -> Tuple[bool, List[str]]:
    """
    Every number in *answer* must appear verbatim in *context*.

    Returns (all_grounded, list_of_unsupported_numbers).
    """
    nums = set(_NUMBER_RE.findall(answer))
    if not nums:
        return True, []
    unsupported = [n for n in nums if n not in context]
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
