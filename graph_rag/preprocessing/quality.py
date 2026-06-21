"""Deterministic garbage-data quality gate for multi-format ingestion.

Cheap, LLM-free signals that reject low-information extractions — blank scans,
empty spreadsheets, decorative GIFs, OCR hallucinations on logos, control-
character soup from corrupt files — BEFORE they ever reach the embedder or KG
extractor. See alldoc.md §5.

The gate is called inside ``preprocess_file`` *after* Docling parse +
``clean_markdown`` but *before* chunking, so it judges cleaned prose. Every
threshold is config-driven (``ingest_*`` in config.py) so a noisy corpus can be
tuned without code edits.

Math-aware: ``$$…$$`` spans count as valid content so an equation-heavy page
with little prose is not wrongly dropped by the alphanumeric-ratio check.
"""
from __future__ import annotations

import re

from graph_rag.config import settings

# Display-math spans are treated as valid content (math exemption).
_MATH_SPAN = re.compile(r"\$\$.*?\$\$", re.DOTALL)
# Word tokens for the unique-token / repetition signals.
_TOKEN = re.compile(r"\w+", re.UNICODE)
# C0 control chars (except tab/newline/carriage-return) and the Unicode
# replacement character — both signal binary noise or a decoding failure.
_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f�]")


def assess_quality(text: str, *, math_exempt: bool = True) -> tuple[bool, str]:
    """Cheap deterministic gate. Returns ``(passed, reason)``.

    ``passed=False`` means the caller should skip the document/chunk (and, for a
    whole document, NOT record it in the manifest — so re-tuning thresholds
    re-tries it). ``reason`` is a short human-readable explanation for logging.
    """
    stripped = text.strip()

    # 1. Length floor — kills blank scans / empty sheets.
    if len(stripped) < settings.ingest_min_chars:
        return False, f"too short ({len(stripped)} chars)"

    # 2. Encoding sanity — a high ratio of replacement/control chars ⇒ binary noise.
    total = len(stripped)
    bad = len(_BAD_CHARS.findall(stripped))
    if total and bad / total > settings.ingest_max_replacement_ratio:
        return False, f"encoding noise ({bad}/{total} bad chars)"

    # 3. Alphanumeric ratio — below the floor ⇒ box-drawing soup / OCR garbage.
    #    Math spans are pulled out and counted as valid so equation-heavy pages
    #    are not penalised for low prose alnum.
    if math_exempt:
        math_chars = sum(len(m) for m in _MATH_SPAN.findall(stripped))
        prose = _MATH_SPAN.sub(" ", stripped)
    else:
        math_chars = 0
        prose = stripped
    non_space = [c for c in prose if not c.isspace()]
    alnum = sum(1 for c in non_space if c.isalnum())
    denom = len(non_space) + math_chars
    if denom:
        ratio = (alnum + math_chars) / denom
        if ratio < settings.ingest_min_alnum_ratio:
            return False, f"low alphanumeric ratio ({ratio:.2f})"

    # 4. Unique-token floor — too few distinct words ⇒ degenerate content
    #    (all-identical cells, repeated watermark text).
    tokens = _TOKEN.findall(stripped.lower())
    unique = set(tokens)
    if len(unique) < settings.ingest_min_unique_tokens:
        return False, f"too few unique tokens ({len(unique)})"

    # 5. Repetition ratio — high duplication ⇒ OCR stutter / "the the the…" loop.
    if tokens:
        repeat = 1 - len(unique) / len(tokens)
        if repeat > settings.ingest_max_repeat_ratio:
            return False, f"high repetition ({repeat:.2f})"

    return True, "ok"
