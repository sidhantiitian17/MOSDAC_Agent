"""Offline tests for the go/no-go gate (graph_rag/eval/scorecard.py)."""
from __future__ import annotations

import pytest

from graph_rag.eval import scorecard as sc
from graph_rag.eval.scorecard import build_scorecard


def _passing_summary() -> dict:
    return {
        sc.FAITHFULNESS: 0.92,
        sc.HALLUCINATION_RATE: 0.0,
        sc.CE1_GROUNDED_RATE: 0.97,
        sc.CE1_UNIT_SWAP_RATE: 0.0,
        sc.CE2_FORMULA_PASS_RATE: 0.95,
        sc.CE3_FABRICATED_CITE_RATE: 0.0,
        sc.CE3_UNCITED_CLAIM_RATE: 0.05,
        sc.CONTEXT_RECALL: 0.90,
        sc.CONTEXT_PRECISION: 0.80,
        sc.FACTUAL_CORRECTNESS: 0.80,
        sc.ANSWER_RELEVANCY: 0.85,
        sc.FALSE_REFUSAL_RATE: 0.05,
        sc.SECURITY_PASS_RATE: 1.0,
        sc.JUDGE_KAPPA: 0.70,
    }


def _passing_strata() -> dict:
    return {"single": 0.95, "multihop": 0.90, "comparison": 0.88}


def test_all_green_is_go():
    card = build_scorecard(_passing_summary(), stratum_faithfulness=_passing_strata())
    assert card.go is True
    assert all(r.passed for r in card.results)


def test_one_failing_min_gate_blocks_go():
    s = _passing_summary()
    s[sc.FAITHFULNESS] = 0.80  # below 0.90 floor
    card = build_scorecard(s, stratum_faithfulness=_passing_strata())
    assert card.go is False
    faith = next(r for r in card.results if r.key == sc.FAITHFULNESS)
    assert faith.status == sc.FAIL


def test_one_failing_max_gate_blocks_go():
    s = _passing_summary()
    s[sc.HALLUCINATION_RATE] = 0.10  # above 0.02 ceiling
    card = build_scorecard(s, stratum_faithfulness=_passing_strata())
    assert card.go is False
    h = next(r for r in card.results if r.key == sc.HALLUCINATION_RATE)
    assert h.status == sc.FAIL


def test_missing_metric_is_skip_and_blocks_go():
    s = _passing_summary()
    s[sc.JUDGE_KAPPA] = None  # not evaluated → SKIP
    card = build_scorecard(s, stratum_faithfulness=_passing_strata())
    assert card.go is False
    k = next(r for r in card.results if r.key == sc.JUDGE_KAPPA)
    assert k.status == sc.SKIP


def test_stratum_floor_forces_no_go_despite_green_overall():
    strata = {"single": 0.95, "multihop": 0.70}  # multihop below 0.85 floor
    card = build_scorecard(_passing_summary(), stratum_faithfulness=strata)
    assert card.go is False
    assert "multihop" in card.stratum_violations


def test_fabricated_cite_zero_tolerance():
    s = _passing_summary()
    s[sc.CE3_FABRICATED_CITE_RATE] = 0.01  # any fabrication fails (threshold 0.0)
    card = build_scorecard(s, stratum_faithfulness=_passing_strata())
    assert card.go is False


def test_custom_thresholds_override():
    s = _passing_summary()
    s[sc.FAITHFULNESS] = 0.86
    relaxed = sc.GateThresholds(faithfulness_min=0.85)
    card = build_scorecard(s, thresholds=relaxed, stratum_faithfulness=_passing_strata())
    faith = next(r for r in card.results if r.key == sc.FAITHFULNESS)
    assert faith.passed


def test_render_contains_verdict():
    card = build_scorecard(_passing_summary(), stratum_faithfulness=_passing_strata())
    text = card.render()
    assert "GO" in text and "Faithfulness" in text
