"""Unit tests for the garbage-data quality gate (graph_rag/preprocessing/quality.py)."""
from __future__ import annotations

from graph_rag.preprocessing.quality import assess_quality

LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. The satellite "
    "carries an imaging payload and measures atmospheric temperature profiles "
    "across multiple spectral bands every fifteen minutes."
)


def test_empty_string_rejected():
    passed, reason = assess_quality("")
    assert passed is False
    assert "short" in reason


def test_whitespace_only_rejected():
    passed, _ = assess_quality("   \n\t  \n  ")
    assert passed is False


def test_real_prose_passes():
    passed, reason = assess_quality(LOREM)
    assert passed is True
    assert reason == "ok"


def test_ocr_gibberish_low_alnum_rejected():
    # Long enough to clear the length floor, but mostly box-drawing/control soup.
    gibberish = "▯│ ┼ ▯ ╬ ║ ▯ │ ┼ ╬ ║ ▯ │ ┼ ╬ ║ ▯ │ ┼ ╬ ║ ▯ │ ┼ ╬ ║ ▯ │ ┼ ╬ ║ ▯"
    passed, reason = assess_quality(gibberish)
    assert passed is False
    assert "alphanumeric" in reason or "unique" in reason


def test_all_identical_tokens_rejected():
    # Degenerate repetition: one repeated word → fails unique-token / repetition.
    passed, reason = assess_quality("spam " * 40)
    assert passed is False
    assert "unique" in reason or "repetition" in reason


def test_repetition_loop_rejected():
    text = "the the the the the the the the the the the the the the the the the the"
    passed, reason = assess_quality(text)
    assert passed is False


def test_math_heavy_snippet_passes_via_exemption():
    # Little prose, lots of LaTeX. Without the math exemption the alnum ratio and
    # token counts would look degenerate; the exemption must let it through.
    text = (
        "Energy mass relation. $$E = mc^2$$ Gravity field. "
        "$$F = G \\frac{m_1 m_2}{r^2}$$ Wave equation. "
        "$$\\nabla^2 \\phi = \\frac{1}{c^2} \\frac{\\partial^2 \\phi}{\\partial t^2}$$"
    )
    passed, reason = assess_quality(text)
    assert passed is True, reason


def test_replacement_char_soup_rejected():
    text = "valid text start " + ("�" * 80)
    passed, reason = assess_quality(text)
    assert passed is False
    assert "encoding" in reason or "alphanumeric" in reason


def test_thresholds_are_config_driven(monkeypatch):
    from graph_rag.config import settings

    short = "tiny bit of text here ok"  # < default 40 chars? it's 24 → rejected
    assert assess_quality(short)[0] is False
    monkeypatch.setattr(settings, "ingest_min_chars", 5)
    # Still needs >= min_unique_tokens distinct words; lower that too.
    monkeypatch.setattr(settings, "ingest_min_unique_tokens", 3)
    assert assess_quality(short)[0] is True
