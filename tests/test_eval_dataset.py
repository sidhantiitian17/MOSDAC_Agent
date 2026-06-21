"""Offline tests for the golden dataset loader (graph_rag/eval/dataset.py)."""
from __future__ import annotations

import pytest

from graph_rag.eval.dataset import (
    STRATA,
    GoldenDatasetError,
    GoldenItem,
    Quantity,
    golden_checksum,
    load_golden,
    stratum_counts,
)

GOLDEN_DIR = "tests/eval/golden/v1"


def _write(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_valid_item_has_no_problems():
    it = GoldenItem(
        id="x1", stratum="single", user_input="q?", reference="a.",
    )
    assert it.validate() == []


def test_refusal_stratum_must_be_unanswerable():
    bad = GoldenItem(id="r1", stratum="should_refuse_oos", user_input="q?", answerable=True)
    problems = bad.validate()
    assert any("answerable=false" in p for p in problems)


def test_answerable_item_requires_reference():
    bad = GoldenItem(id="a1", stratum="single", user_input="q?", reference="")
    assert any("no reference" in p for p in bad.validate())


def test_formula_item_requires_formula_and_followup_requires_setup():
    f = GoldenItem(id="f1", stratum="formula", user_input="q?", reference="a", expected_formula="")
    assert any("expected_formula" in p for p in f.validate())
    fu = GoldenItem(id="fu1", stratum="followup", user_input="q?", reference="a", setup=[])
    assert any("setup" in p for p in fu.validate())


def test_unknown_stratum_flagged():
    assert any("unknown stratum" in p for p in
               GoldenItem(id="u1", stratum="nope", user_input="q?", reference="a").validate())


def test_load_jsonl_skips_comments_and_blank(tmp_path):
    _write(tmp_path, "a.jsonl", [
        "// a comment",
        "",
        '{"id": "s1", "stratum": "single", "user_input": "q?", "reference": "a."}',
    ])
    items = load_golden(tmp_path)
    assert len(items) == 1 and items[0].id == "s1"


def test_duplicate_id_raises(tmp_path):
    _write(tmp_path, "a.jsonl", [
        '{"id": "d1", "stratum": "single", "user_input": "q?", "reference": "a."}',
        '{"id": "d1", "stratum": "single", "user_input": "q2?", "reference": "b."}',
    ])
    with pytest.raises(GoldenDatasetError, match="duplicate id"):
        load_golden(tmp_path)


def test_invalid_json_raises(tmp_path):
    _write(tmp_path, "a.jsonl", ['{"id": "x", oops}'])
    with pytest.raises(GoldenDatasetError, match="invalid JSON"):
        load_golden(tmp_path)


def test_strict_validation_raises_but_nonstrict_loads(tmp_path):
    _write(tmp_path, "a.jsonl", [
        '{"id": "r1", "stratum": "should_refuse_oos", "user_input": "q?", "answerable": true}',
    ])
    with pytest.raises(GoldenDatasetError):
        load_golden(tmp_path, strict=True)
    items = load_golden(tmp_path, strict=False)
    assert len(items) == 1


def test_quantity_parsing():
    it = GoldenItem.from_dict({
        "id": "n1", "stratum": "numeric_edge", "user_input": "q?", "reference": "a",
        "expected_quantities": [{"value": 360, "unit": "m"}, {"value": "1400", "unit": "km"}],
    })
    assert it.expected_quantities == [Quantity(360.0, "m"), Quantity(1400.0, "km")]


def test_checksum_is_order_and_format_independent():
    a = [
        GoldenItem(id="b", stratum="single", user_input="q2", reference="r2"),
        GoldenItem(id="a", stratum="single", user_input="q1", reference="r1"),
    ]
    b = list(reversed(a))
    assert golden_checksum(a) == golden_checksum(b)
    # A content change moves the checksum.
    c = [GoldenItem(id="a", stratum="single", user_input="CHANGED", reference="r1"), a[0]]
    assert golden_checksum(c) != golden_checksum(a)


# ── the shipped seed dataset must always load, validate, and cover all strata ──
def test_seed_dataset_loads_and_validates():
    items = load_golden(GOLDEN_DIR)
    assert len(items) >= 30
    counts = stratum_counts(items)
    for s in STRATA:
        assert s in counts, f"stratum {s} missing from counts"
    # every declared stratum is represented at least once in the seed
    assert all(counts[s] >= 1 for s in STRATA), counts


def test_seed_refusal_items_are_unanswerable():
    items = load_golden(GOLDEN_DIR)
    for it in items:
        if it.stratum.startswith("should_refuse"):
            assert it.answerable is False, it.id
