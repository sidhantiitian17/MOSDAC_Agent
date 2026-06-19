"""Tests for knowledge_graph/resolver.py — entity canonicalization."""
from __future__ import annotations

from graph_rag.knowledge_graph.resolver import canonical_key, canonical_name, resolve


def test_variants_collapse_to_one_key():
    k = canonical_key("INSAT-3D")
    assert k == canonical_key("INSAT 3D")
    assert k == canonical_key("the INSAT-3D satellite")
    assert k == "insat3d"


def test_seed_lexicon_canonical_display_name():
    assert canonical_name("insat 3d") == "INSAT-3D"
    assert canonical_name("the Oceansat-2 satellite") == "Oceansat-2"


def test_preserves_unknown_name_and_key():
    assert canonical_name("TestEntityA") == "TestEntityA"
    assert canonical_key("TestEntityA") == "testentitya"


def test_resolve_returns_name_key_pair():
    r = resolve("the Oceansat-2 satellite")
    assert r.key == "oceansat2"
    assert r.name == "Oceansat-2"
    assert r.surface == "the Oceansat-2 satellite"


def test_empty_input_is_safe():
    assert canonical_key("") == ""
    assert canonical_name("") == ""
