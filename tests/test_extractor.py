"""Tests for knowledge_graph/extractor.py."""
from __future__ import annotations

from graph_rag.knowledge_graph.extractor import EntityRelationExtractor, Triple


def test_triple_serializes_to_dict():
    t = Triple(
        subject="Apple",
        subject_type="ORG",
        relation="ACQUIRED",
        object_="Beats",
        object_type="ORG",
        source_chunk_id="c1",
        confidence=0.8,
    )
    d = t.as_dict()
    assert d["subject"] == "Apple"
    assert d["relation"] == "ACQUIRED"
    assert d["object"] == "Beats"
    assert d["confidence"] == 0.8


def test_extract_empty_text_returns_empty_list():
    ex = EntityRelationExtractor()
    assert ex.extract("") == []
    assert ex.extract("   ") == []


def test_fallback_extractor_finds_known_patterns():
    """Even without spaCy, the regex fallback should catch obvious patterns."""
    triples = EntityRelationExtractor._fallback(
        "Apple acquired Beats Electronics in 2014.",
        source_chunk_id="c1",
        source_path="p",
    )
    assert any("Apple" in t.subject and "Beats" in t.object_ for t in triples)


def test_extract_produces_triples_from_clear_text():
    ex = EntityRelationExtractor()
    text = "Apple acquired Beats Electronics. Microsoft acquired GitHub."
    triples = ex.extract(text, source_chunk_id="c1", source_path="p")
    assert isinstance(triples, list)
    for t in triples:
        assert isinstance(t, Triple)
        assert t.subject and t.object_ and t.relation
