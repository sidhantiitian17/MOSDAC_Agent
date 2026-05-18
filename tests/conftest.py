"""Shared pytest fixtures.

Tests that need live external services (Neo4j, Tabby ML, ChromaDB writes)
are skipped automatically when the service isn't reachable, so the suite still
runs cleanly in environments where only some pieces are wired up.
"""
from __future__ import annotations

import tempfile

import pytest


@pytest.fixture(scope="session")
def settings():
    from graph_rag.config import settings as s

    return s


@pytest.fixture
def tmp_chroma_dir(monkeypatch):
    """Force ChromaStore to use a throwaway directory for the duration of a test."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("CHROMA_PERSIST_DIR", tmp)
        from graph_rag.config import Settings

        new_settings = Settings(_env_file=None, chroma_persist_dir=tmp)
        monkeypatch.setattr("graph_rag.config.settings", new_settings, raising=False)
        yield tmp


@pytest.fixture(scope="session")
def neo4j_available() -> bool:
    try:
        from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

        with Neo4jStore() as store:
            return store.ping()
    except Exception:
        return False


@pytest.fixture(scope="session")
def nomic_available() -> bool:
    try:
        from graph_rag.config import settings as s

        if not s.nomic_api_token:
            return False
        from graph_rag.embeddings.nomic_embedder import get_embedder

        get_embedder.cache_clear()
        get_embedder().embed_query("ping")
        return True
    except Exception:
        return False


def skip_if_no_neo4j(neo4j_available: bool):
    if not neo4j_available:
        pytest.skip("Neo4j is not reachable — start a local instance to run this test.")


def skip_if_no_nomic(nomic_available: bool):
    if not nomic_available:
        pytest.skip(
            "Tabby ML is not reachable or NOMIC_API_TOKEN / TABBY_API_TOKEN is not set."
        )
