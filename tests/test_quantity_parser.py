"""Tests for knowledge_graph/quantity_parser.py — spec extraction + normalization."""
from __future__ import annotations

from graph_rag.knowledge_graph.quantity_parser import parse_quantities


def test_extracts_resolution_and_swath():
    qs = parse_quantities("Spatial resolution is 1 km and the swath width is 740 km.")
    props = {q.property_key for q in qs}
    assert "spatial_resolution" in props
    assert "swath_width" in props


def test_normalizes_to_base_unit_and_keeps_raw():
    qs = parse_quantities("The spatial resolution is 1 km.")
    q = next(q for q in qs if q.property_key == "spatial_resolution")
    assert q.base_value == 1000.0
    assert q.base_unit == "m"
    assert q.raw == "1 km"  # verbatim — never paraphrased


def test_values_are_comparable_across_units():
    coarse = parse_quantities("spatial resolution 1 km")[0]
    fine = parse_quantities("spatial resolution 360 m")[0]
    assert coarse.base_value > fine.base_value  # 1 km is coarser than 360 m


def test_temporal_resolution_in_days():
    qs = parse_quantities("The revisit time is 2 days.")
    q = next(q for q in qs if q.property_key == "temporal_resolution")
    assert q.base_unit == "s"
    assert q.base_value == 172800.0


def test_no_property_keyword_returns_empty():
    assert parse_quantities("The cat sat on the mat in 2014 with 5 friends.") == []
