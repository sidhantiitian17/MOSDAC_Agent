"""Tests for eval/harness.py — pure metric functions + question-set loading."""
from __future__ import annotations

from graph_rag.eval.harness import (
    EvalHarness,
    EvalQuestion,
    EvalResult,
    extract_numbers,
    faithfulness_score,
    retrieval_hit_rate,
)


def test_extract_numbers():
    assert extract_numbers("360 m and 1.5 km, channel 12") == {"360", "1.5", "12"}


def test_faithfulness_all_numbers_grounded():
    assert faithfulness_score("It is 360 m wide", "spec says 360 m here") == 1.0


def test_faithfulness_ungrounded_number():
    assert faithfulness_score("It is 999 m", "spec says 360 m") == 0.0


def test_faithfulness_no_numbers_is_one():
    assert faithfulness_score("no numbers here", "context") == 1.0


def test_retrieval_hit_rate():
    ctx = "Oceansat-2 carries OCM with resolution 360 m"
    assert retrieval_hit_rate(ctx, ["Oceansat-2", "OCM"], ["resolution"]) == 1.0
    assert retrieval_hit_rate(ctx, ["INSAT-3D"], []) == 0.0


def test_retrieval_hit_rate_no_targets_is_one():
    assert retrieval_hit_rate("anything", [], []) == 1.0


def test_load_question_set():
    qs = EvalHarness.load("tests/eval/multihop_questions.yaml")
    assert len(qs) >= 15
    assert all(isinstance(q, EvalQuestion) for q in qs)
    assert {q.type for q in qs} <= {"single", "multihop", "comparison", "followup"}
    # Follow-up cases must carry the prior turns needed to resolve their references.
    assert all(q.setup for q in qs if q.type == "followup")


def test_scorecard_renders_overall_row():
    results = [
        EvalResult("s1", "single", "q", "a", 1.0, 1.0, 0.8),
        EvalResult("m1", "multihop", "q2", "a2", 0.5, 1.0, 0.4),
    ]
    sc = EvalHarness(use_judge=False).scorecard(results)
    assert "OVERALL" in sc
    assert "single" in sc and "multihop" in sc
