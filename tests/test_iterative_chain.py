"""Tests for chain/iterative_chain.py — mocked retriever + LLM, runs offline."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from graph_rag.chain.iterative_chain import IterativeReasoner


class _Resp:
    def __init__(self, content):
        self.content = content


def _llm(responses):
    m = MagicMock()
    m.invoke.side_effect = [_Resp(r) for r in responses]
    return m


@pytest.fixture(autouse=True)
def _no_faithfulness(monkeypatch):
    # Disable the extra self-check call by default; individual tests re-enable it.
    monkeypatch.setattr(
        "graph_rag.chain.iterative_chain.settings.enable_faithfulness_check", False, raising=False
    )


def test_answers_immediately_single_pass():
    retr = MagicMock()
    retr.retrieve.return_value = {"graph_context": "(A) -[CARRIES]-> (B)", "vector_context": "ctx"}
    r = IterativeReasoner(retriever=retr, llm=_llm(["Final answer."]), max_iterations=3)
    assert r.answer("q") == "Final answer."
    assert retr.retrieve.call_count == 1


def test_need_more_triggers_reretrieve():
    retr = MagicMock()
    retr.retrieve.side_effect = [
        {"graph_context": "g1", "vector_context": "v1"},
        {"graph_context": "g2", "vector_context": "v2"},
    ]
    r = IterativeReasoner(
        retriever=retr, llm=_llm(["NEED_MORE: Oceansat-2", "Done with more."]), max_iterations=3
    )
    assert r.answer("q") == "Done with more."
    assert retr.retrieve.call_count == 2


def test_loop_is_bounded_by_max_iterations():
    retr = MagicMock()
    retr.retrieve.return_value = {"graph_context": "g", "vector_context": "v"}
    r = IterativeReasoner(
        retriever=retr,
        llm=_llm(["NEED_MORE: a", "NEED_MORE: b", "NEED_MORE: c"]),
        max_iterations=2,
    )
    out = r.answer("q")
    assert "NEED_MORE" not in out  # final pass strips the unsatisfied request


def test_self_check_runs_when_answer_has_numbers(monkeypatch):
    monkeypatch.setattr(
        "graph_rag.chain.iterative_chain.settings.enable_faithfulness_check", True, raising=False
    )
    retr = MagicMock()
    retr.retrieve.return_value = {"graph_context": "resolution 360 m", "vector_context": "v"}
    r = IterativeReasoner(
        retriever=retr,
        llm=_llm(["Resolution is 360 m.", "Resolution is 360 m (verified)."]),
        max_iterations=1,
    )
    assert "verified" in r.answer("q")


def test_self_check_keeps_draft_when_correction_degrades(monkeypatch):
    monkeypatch.setattr(
        "graph_rag.chain.iterative_chain.settings.enable_faithfulness_check", True, raising=False
    )
    retr = MagicMock()
    retr.retrieve.return_value = {"graph_context": "360 m", "vector_context": "v"}
    # Draft is a full grounded sentence; the verifier returns a bare fragment —
    # the guard must keep the draft rather than the mangled correction.
    draft = "The OCM has a spatial resolution of 360 m and measures chlorophyll."
    r = IterativeReasoner(retriever=retr, llm=_llm([draft, "Oceansat-2"]), max_iterations=1)
    assert r.answer("q") == draft


def test_invoke_matches_chain_interface():
    retr = MagicMock()
    retr.retrieve.return_value = {"graph_context": "g", "vector_context": "v"}
    r = IterativeReasoner(retriever=retr, llm=_llm(["hi"]), max_iterations=1)
    assert r.invoke({"question": "q", "history": ""}) == "hi"
