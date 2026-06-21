"""Offline tests for the runner's pure aggregation/manifest/reporting logic.

The live pieces (judge LLM, ragas evaluate) are NOT exercised here — aggregation
takes injected synthetic RAGAS scores so the segregation, custom-metric, and gate
plumbing is verified without any model.
"""
from __future__ import annotations

from graph_rag.eval import scorecard as sc
from graph_rag.eval.dataset import GoldenItem
from graph_rag.eval.probe import CapturedTurn
from graph_rag.eval.ragas_runner import (
    aggregate_results,
    build_manifest,
    render_markdown,
)


def _item(id, stratum, *, answerable=True, reference="ref", formula="", entities=None):
    return GoldenItem(
        id=id, stratum=stratum, user_input=f"q-{id}", reference=reference,
        expected_formula=formula, expected_entities=entities or [], answerable=answerable,
    )


def _captured(id, stratum, *, answerable=True, refused=False, answer="", contexts=None, citations=None):
    return CapturedTurn(
        id=id, stratum=stratum, answerable=answerable, user_input=f"q-{id}",
        answer=answer, refused=refused, grounded=not refused,
        retrieved_contexts=contexts or [], citations=citations or [],
    )


def test_segregation_and_confusion_matrix():
    items = {
        "a1": _item("a1", "single"),
        "a2": _item("a2", "single"),
        "oos1": _item("oos1", "should_refuse_oos", answerable=False, reference="should refuse"),
        "oos2": _item("oos2", "should_refuse_oos", answerable=False, reference="should refuse"),
    }
    captured = [
        _captured("a1", "single", answer="grounded answer", contexts=["ctx"]),   # true answer
        _captured("a2", "single", refused=True),                                  # false refusal
        _captured("oos1", "should_refuse_oos", answerable=False, refused=True),    # true refusal
        _captured("oos2", "should_refuse_oos", answerable=False, answer="hallucinated"),  # hallucination
    ]
    ragas = {"a1": {sc.FAITHFULNESS: 0.9, sc.CONTEXT_RECALL: 0.8}}

    b = aggregate_results(captured, items, ragas, config_name="PROD")
    assert b.n_total == 4 and b.n_answered == 2 and b.n_refused == 2
    assert b.confusion.true_answer == 1 and b.confusion.false_refusal == 1
    assert b.confusion.true_refusal == 1 and b.confusion.hallucinated_on_absent == 1
    assert b.summary[sc.HALLUCINATION_RATE] == 0.5
    assert b.summary[sc.FALSE_REFUSAL_RATE] == 0.5
    # Faithfulness mean only over the answered-answerable item.
    assert b.summary[sc.FAITHFULNESS] == 0.9


def test_refused_items_excluded_from_ragas_mean():
    items = {"a1": _item("a1", "single"), "a2": _item("a2", "single")}
    captured = [
        _captured("a1", "single", answer="x", contexts=["c"]),
        _captured("a2", "single", refused=True),
    ]
    # Even if a stale score exists for the refused item, it must not enter the mean.
    ragas = {"a1": {sc.FAITHFULNESS: 1.0}, "a2": {sc.FAITHFULNESS: 0.0}}
    b = aggregate_results(captured, items, ragas)
    assert b.summary[sc.FAITHFULNESS] == 1.0


def test_ce1_unit_swap_flows_into_summary():
    items = {"n1": _item("n1", "numeric_edge")}
    captured = [_captured("n1", "numeric_edge", answer="resolution is 360 km", contexts=["resolution is 360 m"])]
    b = aggregate_results(captured, items, {"n1": {}})
    assert b.summary[sc.CE1_UNIT_SWAP_RATE] == 1.0
    assert b.summary[sc.CE1_GROUNDED_RATE] == 0.0


def test_ce2_formula_pass_rate():
    items = {"f1": _item("f1", "formula", formula=r"$$E = mc^2$$")}
    captured = [_captured("f1", "formula", answer=r"the law $$E=mc^2$$ holds", contexts=["E=mc^2"])]
    b = aggregate_results(captured, items, {"f1": {}})
    assert b.summary[sc.CE2_FORMULA_PASS_RATE] == 1.0


def test_ce3_fabricated_citation_rate():
    items = {"a1": _item("a1", "single")}
    # Cites [S9] but only S1 is a valid (returned) citation → fabricated reached the user.
    captured = [_captured("a1", "single", answer="The swath is 1400 km wide here [S9].",
                          contexts=["1400 km"], citations=[{"id": "S1"}])]
    b = aggregate_results(captured, items, {"a1": {}})
    assert b.summary[sc.CE3_FABRICATED_CITE_RATE] == 1.0


def test_security_pass_rate_from_unsafe_stratum():
    items = {
        "u1": _item("u1", "should_refuse_unsafe", answerable=False, reference="refuse"),
        "u2": _item("u2", "should_refuse_unsafe", answerable=False, reference="refuse"),
    }
    captured = [
        _captured("u1", "should_refuse_unsafe", answerable=False, refused=True),
        _captured("u2", "should_refuse_unsafe", answerable=False, answer="leaked"),  # not refused
    ]
    b = aggregate_results(captured, items, {})
    assert b.summary[sc.SECURITY_PASS_RATE] == 0.5


def test_missing_strata_yield_none_and_block_go():
    # A tiny answered-only set: many gate metrics have no data → None → SKIP → NO-GO.
    items = {"a1": _item("a1", "single")}
    captured = [_captured("a1", "single", answer="x", contexts=["c"])]
    b = aggregate_results(captured, items, {"a1": {sc.FAITHFULNESS: 0.95}})
    assert b.summary[sc.CE2_FORMULA_PASS_RATE] is None  # no formula items
    assert b.go_scorecard().go is False


def test_manifest_records_frozen_knobs():
    m = build_manifest("PROD", gold_checksum="abc123", gold_n=35, judge_model="strong-judge")
    assert m["config"] == "PROD" and m["gold_checksum"] == "abc123" and m["gold_n"] == 35
    assert m["judge_model"] == "strong-judge"
    assert "embedding_model" in m["pipeline"]
    assert "grounding_action" in m["guardrails"]
    assert "timestamp" in m


def test_render_markdown_smoke():
    items = {"a1": _item("a1", "single")}
    captured = [_captured("a1", "single", answer="x", contexts=["c"])]
    b = aggregate_results(captured, items, {"a1": {sc.FAITHFULNESS: 0.95}})
    m = build_manifest("PROD", "abc", 1, "judge")
    text = render_markdown(b, m)
    assert "RAGAS Eval — PROD" in text and "Refusal confusion" in text
