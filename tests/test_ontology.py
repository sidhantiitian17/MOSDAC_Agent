"""Tests for knowledge_graph/ontology.py — relation/type normalization."""
from __future__ import annotations

from graph_rag.knowledge_graph.ontology import (
    RELATION_TYPES,
    canonical_relation,
    is_trivial_relation,
    normalize_node_type,
)


def test_canonical_relation_maps_known_verbs():
    assert canonical_relation("carries") == "CARRIES"
    assert canonical_relation("measures") == "MEASURES"
    assert canonical_relation("launched by") == "LAUNCHED_BY"
    assert canonical_relation("part of") == "PART_OF"


def test_trivial_relations_dropped():
    assert canonical_relation("is") is None
    assert canonical_relation("has") is None
    assert canonical_relation("was") is None
    assert is_trivial_relation("IS")
    assert not is_trivial_relation("CARRIES")


def test_unknown_verb_kept_sanitized():
    # Non-trivial but uncurated verbs are retained, not lost.
    assert canonical_relation("acquired") == "ACQUIRED"


def test_canonical_relation_passthrough_for_canonical_names():
    for rel in ("CARRIES", "MEASURES", "PRODUCES"):
        assert canonical_relation(rel) == rel
        assert rel in RELATION_TYPES


def test_normalize_node_type():
    assert normalize_node_type("ORG") == "Organization"
    assert normalize_node_type("GPE") == "Location"
    assert normalize_node_type("satellite") == "Satellite"
    assert normalize_node_type("unknown-thing") == "Concept"
    assert normalize_node_type(None) == "Concept"
