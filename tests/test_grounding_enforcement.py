"""Output-side grounding enforcement (L4): normalized numeric check, sentence
stripping, and the strip/refuse policy in the pipeline."""
import pytest

from guardrails.output import grounding_check as gc
from guardrails.retrieval.grounding_gate import CitationRegistry
from guardrails.pipeline import GuardrailPipeline


# ── F6: numeric grounding normalization ─────────────────────────────────────

def test_numeric_normalization_thousands_separator():
    ok, bad = gc.check_numeric_grounding("Swath is 1400 km.", "context says 1,400 km")
    assert ok and not bad


def test_numeric_normalization_trailing_decimal_zero():
    ok, bad = gc.check_numeric_grounding("Height 4.50 m.", "the height is 4.5 m")
    assert ok and not bad


def test_numeric_citation_id_not_treated_as_number():
    ok, bad = gc.check_numeric_grounding("INSAT-3D carries VHRR [S1].", "INSAT-3D carries VHRR.")
    assert ok and not bad


def test_numeric_fabricated_still_flagged():
    ok, bad = gc.check_numeric_grounding("Swath is 9999 km.", "Swath is 1400 km.")
    assert not ok and bad == ["9999"]


# ── F5: sentence stripping helper ───────────────────────────────────────────

def test_strip_ungrounded_keeps_sources_footer():
    ans = "Swath is 1400 km [S1]. Totally invented nonsense sentence here. Done [S1].\nSOURCES: [S1]"
    out = gc.strip_ungrounded(ans, ["Totally invented nonsense sentence here."])
    assert "nonsense" not in out
    assert "Swath is 1400 km" in out and "SOURCES: [S1]" in out


def test_strip_ungrounded_all_removed_returns_empty():
    assert gc.strip_ungrounded("Only an invented claim here.", ["Only an invented claim here."]) == ""


# ── F5: pipeline policy (strip / refuse / flag) ─────────────────────────────

@pytest.fixture
def cfg(monkeypatch):
    from guardrails.config import guardrail_settings
    monkeypatch.setattr(guardrail_settings, "enable", True)
    monkeypatch.setattr(guardrail_settings, "citation_verify", False)
    monkeypatch.setattr(guardrail_settings, "leakage_check", False)
    monkeypatch.setattr(guardrail_settings, "pii_output", False)
    monkeypatch.setattr(guardrail_settings, "toxicity", False)
    # Disable the embedding-based sentence check by default; tests opt in via mock.
    monkeypatch.setattr(guardrail_settings, "grounding_min_sim", 0.0)
    return guardrail_settings


def test_refuse_mode_blocks_ungrounded_number(cfg, monkeypatch):
    from guardrails.templates import REFUSAL_NO_CONTEXT
    monkeypatch.setattr(cfg, "grounding_action", "refuse")
    pipe = GuardrailPipeline()
    answer, citations, reasons = pipe.check_output(
        "The swath is 9999 km.", CitationRegistry(), passages=[], context="swath is 1400 km"
    )
    assert answer == REFUSAL_NO_CONTEXT and "grounding_refused" in reasons


def test_strip_mode_removes_ungrounded_sentence(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "grounding_action", "strip")
    monkeypatch.setattr(cfg, "grounding_min_sim", 0.4)
    bad = "The orbit is an entirely fabricated unsupported statement here."
    good = "The swath width is reported in the source document text."
    monkeypatch.setattr(gc, "check_sentence_grounding", lambda a, p, s: (False, [bad]))
    pipe = GuardrailPipeline()
    answer, _, reasons = pipe.check_output(
        f"{good} {bad}", CitationRegistry(), passages=["some passage"], context="context"
    )
    assert "fabricated" not in answer and "swath width" in answer
    assert "grounding_stripped" in reasons


def test_flag_mode_is_non_destructive(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "grounding_action", "flag")
    original = "The swath is 9999 km."
    answer, _, reasons = GuardrailPipeline().check_output(
        original, CitationRegistry(), passages=[], context="swath is 1400 km"
    )
    assert answer == original and "ungrounded_numbers:1" in reasons
