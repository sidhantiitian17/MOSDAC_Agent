"""Offline tests for the custom evaluators CE1–CE4 (graph_rag/eval/custom_metrics.py)."""
from __future__ import annotations

from graph_rag.eval.custom_metrics import (
    FALSE_REFUSAL,
    HALLUCINATED_ON_ABSENT,
    TRUE_ANSWER,
    TRUE_REFUSAL,
    classify_outcome,
    refusal_confusion,
    score_citation_integrity,
    score_formula_fidelity,
    score_numeric_fidelity,
)


# ── CE1 numeric & unit fidelity ───────────────────────────────────────────────
def test_ce1_grounded_with_separator_and_decimal():
    # 1,400 ≡ 1400 and 4.50 ≡ 4.5 must not be flagged.
    r = score_numeric_fidelity("The swath is 1400 km and bias 4.5 m.", "swath 1,400 km, bias 4.50 m")
    assert r.total == 2 and r.grounded == 2
    assert r.grounded_rate == 1.0 and r.unit_swap_rate == 0.0


def test_ce1_unit_swap_detected():
    # Right number, wrong unit → unit swap (counted ungrounded + unit_swap).
    r = score_numeric_fidelity("resolution is 360 km", "resolution is 360 m")
    assert r.total == 1 and r.grounded == 0 and r.unit_swaps == 1
    assert r.unit_swap_rate == 1.0
    assert any("360" in s for s in r.swapped)


def test_ce1_fabricated_number_ungrounded():
    r = score_numeric_fidelity("the swath is 5000 km", "swath of 1400 km")
    assert r.grounded == 0 and r.unit_swaps == 0
    assert "5000" in r.ungrounded_values


def test_ce1_bare_number_no_unit_is_grounded():
    r = score_numeric_fidelity("about 2 days revisit", "revisit period of 2 days")
    assert r.grounded_rate == 1.0


def test_ce1_ignores_identifier_digits():
    # "INSAT-3D" must not be parsed as the quantity "3" — mirrors production _NUMBER_RE.
    r = score_numeric_fidelity("INSAT-3D imager", "the INSAT-3D satellite")
    assert r.total == 0 and r.grounded_rate == 1.0


def test_ce1_empty_answer_is_vacuously_grounded():
    assert score_numeric_fidelity("no numbers here", "ctx").grounded_rate == 1.0


# ── CE2 formula fidelity ──────────────────────────────────────────────────────
def test_ce2_whitespace_insensitive_match():
    expected = r"$$\sigma_0 = \frac{P_r (4\pi)^3 R^4}{P_t G^2 \lambda^2 A}$$"
    answer = r"The NRCS is $$\sigma_0=\frac{P_r (4\pi)^3 R^4}{P_t G^2 \lambda^2 A}$$ where ..."
    assert score_formula_fidelity(answer, expected) is True


def test_ce2_corrupted_symbol_fails():
    expected = r"$$\sigma_0 = \alpha + \beta$$"
    answer = r"$$\sigma_1 = \alpha + \beta$$"  # sigma_0 → sigma_1
    assert score_formula_fidelity(answer, expected) is False


def test_ce2_no_expected_formula_is_vacuously_true():
    assert score_formula_fidelity("anything", "") is True


def test_ce2_matches_formula_without_delimiters():
    expected = r"$$E = mc^2$$"
    assert score_formula_fidelity("the relation E=mc^2 holds", expected) is True


# ── CE3 citation integrity ────────────────────────────────────────────────────
def test_ce3_clean_citations():
    answer = ("The OCM sensor measures ocean colour and chlorophyll [S1]. "
              "It flies on the Oceansat-2 satellite in orbit [S2].")
    r = score_citation_integrity(answer, {"S1", "S2"})
    assert not r.has_fabricated
    assert r.factual_sentences == 2 and r.uncited_claim_rate == 0.0


def test_ce3_fabricated_citation_detected():
    r = score_citation_integrity("The swath width is about 1400 km wide [S9].", {"S1"})
    assert r.has_fabricated and "S9" in r.fabricated_ids


def test_ce3_uncited_factual_claim_counted():
    # A long factual sentence with no [Sx] is an uncited claim.
    r = score_citation_integrity("The scatterometer swath spans roughly 1400 km across the ocean.", set())
    assert r.factual_sentences == 1 and r.uncited_sentences == 1
    assert r.uncited_claim_rate == 1.0


# ── CE4 refusal correctness ───────────────────────────────────────────────────
def test_ce4_classify_outcomes():
    assert classify_outcome(True, False) == TRUE_ANSWER
    assert classify_outcome(True, True) == FALSE_REFUSAL
    assert classify_outcome(False, True) == TRUE_REFUSAL
    assert classify_outcome(False, False) == HALLUCINATED_ON_ABSENT


def test_ce4_confusion_rates():
    # 3 answerable (2 answered, 1 wrongly refused) + 3 unanswerable (2 refused, 1 answered).
    records = [
        (True, False), (True, False), (True, True),
        (False, True), (False, True), (False, False),
    ]
    cm = refusal_confusion(records)
    assert cm.true_answer == 2 and cm.false_refusal == 1
    assert cm.true_refusal == 2 and cm.hallucinated_on_absent == 1
    assert abs(cm.false_refusal_rate - 1 / 3) < 1e-9
    assert abs(cm.hallucination_rate - 1 / 3) < 1e-9
    assert abs(cm.refusal_precision - 2 / 3) < 1e-9   # 2 true / (2 true + 1 false)
    assert abs(cm.refusal_recall - 2 / 3) < 1e-9      # 2 of 3 unanswerable refused
