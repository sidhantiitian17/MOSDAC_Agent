"""Tests for knowledge_graph/llm_extractor.py — JSON parsing + row validation.

These tests mock the model call, so they run fully offline with no Tabby ML.
"""
from __future__ import annotations

from graph_rag.knowledge_graph.llm_extractor import LLMExtractor, _extract_json


def test_extract_json_pulls_balanced_object():
    data = _extract_json('noise before {"triples": [{"a": 1}]} noise after')
    assert data["triples"][0]["a"] == 1


def test_extract_json_handles_code_fences():
    data = _extract_json('```json\n{"triples": []}\n```')
    assert data == {"triples": []}


def test_extract_json_returns_none_when_absent():
    assert _extract_json("no json here at all") is None


def test_validate_row_drops_trivial_relation():
    row = {
        "subject": "X", "subject_type": "Satellite",
        "relation": "is", "object": "Y", "object_type": "Concept",
    }
    assert LLMExtractor._validate_row(row, "c", "p") is None


def test_validate_row_drops_self_loop():
    row = {"subject": "X", "relation": "CARRIES", "object": "x"}
    assert LLMExtractor._validate_row(row, "c", "p") is None


def test_validate_row_builds_typed_triple():
    row = {
        "subject": "Oceansat-2", "subject_type": "satellite",
        "relation": "CARRIES", "object": "OCM", "object_type": "sensor",
        "confidence": 0.9,
    }
    t = LLMExtractor._validate_row(row, "c1", "p1")
    assert t is not None
    assert t.subject == "Oceansat-2"
    assert t.relation == "CARRIES"
    assert t.subject_type == "Satellite"   # normalized
    assert t.object_type == "Sensor"       # normalized
    assert t.confidence == 0.9


def test_extract_with_mocked_completion(monkeypatch):
    ex = LLMExtractor(api_token="dummy")
    monkeypatch.setattr(
        ex,
        "_complete",
        lambda messages, max_tokens=None: (
            '{"triples":[{"subject":"Oceansat-2","subject_type":"Satellite",'
            '"relation":"CARRIES","object":"OCM","object_type":"Sensor","confidence":0.95}]}'
        ),
    )
    triples = ex.extract("Oceansat-2 carries the OCM sensor.", source_chunk_id="c1")
    assert len(triples) == 1
    assert triples[0].object_ == "OCM"
    assert triples[0].relation == "CARRIES"


def test_extract_empty_text_returns_empty():
    assert LLMExtractor(api_token="dummy").extract("") == []
