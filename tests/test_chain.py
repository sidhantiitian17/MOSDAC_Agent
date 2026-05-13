"""Tests for chain/graph_rag_chain.py — uses mock retriever + mock LLM."""
from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeListChatModel


def test_chain_invokes_retriever_and_llm():
    from graph_rag.chain.graph_rag_chain import build_graph_rag_chain

    mock_retriever = MagicMock()
    mock_retriever.retrieve.return_value = {
        "vector_context": "Apple acquired Beats in 2014.",
        "graph_context": "(Apple) -[ACQUIRED]-> (Beats)",
    }
    fake_llm = FakeListChatModel(responses=["Apple acquired Beats Electronics in 2014."])

    chain = build_graph_rag_chain(retriever=mock_retriever, llm=fake_llm)
    out = chain.invoke({"question": "What did Apple acquire?"})
    assert "Apple" in out
    assert "Beats" in out
    mock_retriever.retrieve.assert_called_once_with("What did Apple acquire?")


def test_chain_passes_question_through():
    from graph_rag.chain.graph_rag_chain import build_graph_rag_chain

    mock_retriever = MagicMock()
    mock_retriever.retrieve.return_value = {"vector_context": "ctx", "graph_context": "g"}
    fake_llm = FakeListChatModel(responses=["answer"])

    chain = build_graph_rag_chain(retriever=mock_retriever, llm=fake_llm)
    assert chain.invoke({"question": "X?"}) == "answer"
