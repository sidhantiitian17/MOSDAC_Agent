"""Tests for the gated follow-up query contextualizer — fully mocked LLM."""
from __future__ import annotations

from unittest.mock import MagicMock

from graph_rag.config import settings
from graph_rag.retrieval.query_contextualizer import QueryContextualizer


def _llm_returning(content: str) -> MagicMock:
    llm = MagicMock()
    resp = MagicMock()
    resp.content = content
    llm.invoke.return_value = resp
    return llm


def test_self_contained_question_passes_through_without_llm():
    """A question that names its own subject is not a follow-up — no LLM call."""
    llm = MagicMock()
    c = QueryContextualizer(llm=llm)
    history = "User: hi\nAssistant: hello"

    out = c.contextualize("What sensors does Oceansat-2 carry?", history)

    assert out.search_query == "What sensors does Oceansat-2 carry?"
    assert out.rewritten is False
    llm.invoke.assert_not_called()


def test_no_history_means_no_rewrite():
    """With nothing to resolve against, even a pronoun question passes through."""
    llm = MagicMock()
    c = QueryContextualizer(llm=llm)

    out = c.contextualize("what about its resolution?", "")

    assert out.search_query == "what about its resolution?"
    llm.invoke.assert_not_called()


def test_followup_with_history_is_rewritten():
    """A pronoun follow-up with history triggers exactly one LLM rewrite."""
    llm = _llm_returning(
        '{"standalone":"What is the spatial resolution of Oceansat-2 OCM?",'
        '"entities":["Oceansat-2","OCM"]}'
    )
    c = QueryContextualizer(llm=llm)
    history = "User: What sensors does Oceansat-2 carry?\nAssistant: It carries OCM and OSCAT."

    out = c.contextualize("what's its resolution?", history)

    assert out.search_query == "What is the spatial resolution of Oceansat-2 OCM?"
    assert out.rewritten is True
    assert "Oceansat-2" in out.carryover_entities
    llm.invoke.assert_called_once()


def test_llm_failure_falls_back_to_original_question():
    """A rewrite failure must never block answering — return the original query."""
    llm = MagicMock()
    llm.invoke.side_effect = RuntimeError("tabby down")
    c = QueryContextualizer(llm=llm)
    history = "User: Tell me about INSAT-3D.\nAssistant: It is a meteorological satellite."

    out = c.contextualize("what about it?", history)

    assert out.search_query == "what about it?"
    assert out.rewritten is False


def test_disabled_flag_short_circuits(monkeypatch):
    monkeypatch.setattr(settings, "enable_query_contextualization", False)
    llm = MagicMock()
    c = QueryContextualizer(llm=llm)
    history = "User: What sensors does Oceansat-2 carry?\nAssistant: OCM and OSCAT."

    out = c.contextualize("what's its resolution?", history)

    assert out.search_query == "what's its resolution?"
    llm.invoke.assert_not_called()


def test_gate_detects_short_elliptical_question():
    assert QueryContextualizer._looks_like_followup("and the swath?", "User: q\nAssistant: a")


def test_gate_ignores_question_with_named_entity():
    assert not QueryContextualizer._looks_like_followup(
        "What is the swath width of Scatterometer?", "User: q\nAssistant: a"
    )
