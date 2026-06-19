"""Tests for retrieval/query_planner.py — mocked LLM, runs offline."""
from __future__ import annotations

from unittest.mock import MagicMock

from graph_rag.retrieval.query_planner import QueryPlanner, _loads_obj


class _Resp:
    def __init__(self, content):
        self.content = content


def test_loads_obj_balanced():
    data = _loads_obj('noise {"subquestions":["a"],"anchors":["X"],"multihop":true} tail')
    assert data["anchors"] == ["X"]
    assert data["multihop"] is True


def test_loads_obj_none_when_absent():
    assert _loads_obj("no json here") is None


def test_decompose_parses_subquestions_and_anchors():
    llm = MagicMock()
    llm.invoke.return_value = _Resp(
        '{"subquestions":["q1","q2"],"anchors":["Oceansat-2"],"multihop":true}'
    )
    plan = QueryPlanner(llm=llm).decompose("a complex multi-part question")
    assert plan.sub_questions == ["q1", "q2"]
    assert plan.anchors == ["Oceansat-2"]
    assert plan.multihop is True


def test_decompose_falls_back_on_bad_json():
    llm = MagicMock()
    llm.invoke.return_value = _Resp("not json at all")
    plan = QueryPlanner(llm=llm).decompose("simple question")
    assert plan.sub_questions == ["simple question"]
    assert plan.multihop is False


def test_decompose_empty_question_is_safe():
    plan = QueryPlanner(llm=MagicMock()).decompose("")
    assert plan.sub_questions == []
