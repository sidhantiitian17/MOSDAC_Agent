"""Tests for retrieval/* — uses mocks so it runs without live services."""
from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.documents import Document


def test_vector_retriever_formats_context():
    from graph_rag.retrieval.vector_retriever import VectorRetriever

    mock_store = MagicMock()
    mock_store.similarity_search_with_score.return_value = [
        (Document(page_content="Apple bought Beats.", metadata={"source": "a.pdf", "chunk_id": "c1"}), 0.1),
        (Document(page_content="Microsoft bought GitHub.", metadata={"source": "b.pdf", "chunk_id": "c2"}), 0.2),
    ]
    r = VectorRetriever.__new__(VectorRetriever)
    r._store = mock_store
    r._k = 5

    ctx = r.as_context("acquisitions")
    assert "Apple bought Beats." in ctx
    assert "Source: a.pdf" in ctx
    assert "Microsoft bought GitHub." in ctx


def test_vector_retriever_handles_empty_results():
    from graph_rag.retrieval.vector_retriever import VectorRetriever

    mock_store = MagicMock()
    mock_store.similarity_search_with_score.return_value = []
    r = VectorRetriever.__new__(VectorRetriever)
    r._store = mock_store
    r._k = 5

    assert "no relevant" in r.as_context("anything").lower()


def test_graph_retriever_formats_paths():
    from graph_rag.retrieval.graph_retriever import GraphRetriever

    mock_store = MagicMock()
    mock_store.fulltext_search.return_value = [{"name": "Apple", "type": "ORG", "score": 1.0}]
    mock_store.query_neighbors.return_value = [
        {
            "nodes": [{"name": "Apple"}, {"name": "Beats"}],
            "relationships": [{"type": "RELATION", "name": "ACQUIRED", "start": "Apple", "end": "Beats"}],
        }
    ]
    mock_extractor = MagicMock()
    mock_extractor.extract_entities.return_value = [("Apple", "ORG")]

    r = GraphRetriever.__new__(GraphRetriever)
    r._store = mock_store
    r._extractor = mock_extractor
    r._depth = 2
    r._k = 10

    ctx = r.as_context("what did Apple buy?")
    assert "Apple" in ctx and "Beats" in ctx
    assert "ACQUIRED" in ctx


def test_hybrid_retriever_returns_both_contexts():
    from graph_rag.retrieval.hybrid_retriever import HybridRetriever

    h = HybridRetriever()
    h._vector = MagicMock()
    h._vector.as_context.return_value = "VECTOR_CTX"
    h._graph = MagicMock()
    h._graph.as_context.return_value = "GRAPH_CTX"

    out = h.retrieve("query")
    assert out["vector_context"] == "VECTOR_CTX"
    assert out["graph_context"] == "GRAPH_CTX"


def test_hybrid_retriever_degrades_when_one_side_fails():
    from graph_rag.retrieval.hybrid_retriever import HybridRetriever

    h = HybridRetriever()
    h._vector = MagicMock()
    h._vector.as_context.return_value = "VECTOR_OK"
    h._graph = MagicMock()
    h._graph.as_context.side_effect = RuntimeError("neo4j down")

    out = h.retrieve("query")
    assert out["vector_context"] == "VECTOR_OK"
    assert "unavailable" in out["graph_context"].lower()
