"""Tests for retrieval/* — uses mocks so it runs without live services."""
from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.documents import Document


def test_vector_retriever_formats_context():
    from graph_rag.retrieval.vector_retriever import VectorRetriever

    mock_store = MagicMock()
    mock_store.similarity_search_with_relevance.return_value = [
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
    mock_store.similarity_search_with_relevance.return_value = []
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
    from graph_rag.retrieval.vector_retriever import VectorHit

    h = HybridRetriever()
    # Vector + BM25 hits are RRF-fused into vector_context; graph is independent.
    h._vector = MagicMock()
    h._vector.retrieve.return_value = [VectorHit("Apple bought Beats.", "a.pdf", 0.1, "c1")]
    h._bm25 = MagicMock()
    h._bm25.retrieve.return_value = []
    h._graph = MagicMock()
    h._graph.as_context.return_value = "GRAPH_CTX"

    out = h.retrieve("query")
    assert "Apple bought Beats." in out["vector_context"]
    assert out["graph_context"] == "GRAPH_CTX"


def test_hybrid_retriever_degrades_when_one_side_fails():
    from graph_rag.retrieval.hybrid_retriever import HybridRetriever
    from graph_rag.retrieval.vector_retriever import VectorHit

    h = HybridRetriever()
    h._vector = MagicMock()
    h._vector.retrieve.return_value = [VectorHit("VEC OK", "v.pdf", 0.1, "c1")]
    h._bm25 = MagicMock()
    h._bm25.retrieve.return_value = []
    h._graph = MagicMock()
    h._graph.as_context.side_effect = RuntimeError("neo4j down")

    out = h.retrieve("query")
    assert "VEC OK" in out["vector_context"]
    assert "unavailable" in out["graph_context"].lower()


def test_hybrid_retriever_reranks_fused_passages():
    from graph_rag.retrieval.hybrid_retriever import HybridRetriever
    from graph_rag.retrieval.vector_retriever import VectorHit

    h = HybridRetriever()
    h._vector = MagicMock()
    h._vector.retrieve.return_value = [
        VectorHit("ALPHA", "a.pdf", 0.3, "c1"),
        VectorHit("BRAVO", "b.pdf", 0.2, "c2"),
        VectorHit("CHARLIE", "c.pdf", 0.1, "c3"),
    ]
    h._bm25 = MagicMock()
    h._bm25.retrieve.return_value = []
    h._graph = MagicMock()
    h._graph.as_context.return_value = "GRAPH_CTX"

    # Embedder that makes BRAVO the most similar to the query, reordering it first.
    embedder = MagicMock()
    embedder.embed_query.return_value = [1.0, 0.0]
    embedder.embed_documents.return_value = [[0.0, 1.0], [1.0, 0.0], [0.0, 1.0]]
    h._embedder = embedder

    ctx = h.retrieve("query")["vector_context"]
    assert ctx.index("BRAVO") < ctx.index("ALPHA")  # rerank promoted BRAVO


def test_hybrid_retriever_rerank_falls_back_when_embedder_unavailable():
    from graph_rag.retrieval.hybrid_retriever import HybridRetriever
    from graph_rag.retrieval.vector_retriever import VectorHit

    h = HybridRetriever()
    h._vector = MagicMock()
    h._vector.retrieve.return_value = [
        VectorHit("ALPHA", "a.pdf", 0.3, "c1"),
        VectorHit("BRAVO", "b.pdf", 0.2, "c2"),
    ]
    h._bm25 = MagicMock()
    h._bm25.retrieve.return_value = []
    h._graph = MagicMock()
    h._graph.as_context.return_value = "G"

    embedder = MagicMock()
    embedder.embed_query.side_effect = RuntimeError("embeddings down")
    h._embedder = embedder

    # Degrades to RRF order — ALPHA (higher vector rank) stays first.
    ctx = h.retrieve("query")["vector_context"]
    assert ctx.index("ALPHA") < ctx.index("BRAVO")
