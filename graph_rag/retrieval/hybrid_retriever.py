"""Combine vector + graph retrieval into a single context block for the LLM."""
from __future__ import annotations

import logging

from graph_rag.retrieval.graph_retriever import GraphRetriever
from graph_rag.retrieval.vector_retriever import VectorRetriever

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Merges semantic passages and graph paths for the RAG prompt.

    Each retriever can fail independently — if Neo4j is down we degrade gracefully
    to vector-only, and vice versa.
    """

    def __init__(
        self,
        vector: VectorRetriever | None = None,
        graph: GraphRetriever | None = None,
    ):
        self._vector = vector
        self._graph = graph

    @property
    def vector(self) -> VectorRetriever:
        if self._vector is None:
            self._vector = VectorRetriever()
        return self._vector

    @property
    def graph(self) -> GraphRetriever:
        if self._graph is None:
            self._graph = GraphRetriever()
        return self._graph

    def retrieve(self, query: str) -> dict[str, str]:
        try:
            vector_context = self.vector.as_context(query)
        except Exception as exc:
            logger.warning("Vector retrieval unavailable: %s", exc)
            vector_context = "(vector store unavailable)"

        try:
            graph_context = self.graph.as_context(query)
        except Exception as exc:
            logger.warning("Graph retrieval unavailable: %s", exc)
            graph_context = "(knowledge graph unavailable)"

        return {"vector_context": vector_context, "graph_context": graph_context}
