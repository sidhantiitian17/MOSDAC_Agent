"""Shared pytest fixtures.

Tests that need live external services (Neo4j, Tabby ML, ChromaDB writes)
are skipped automatically when the service isn't reachable, so the suite still
runs cleanly in environments where only some pieces are wired up.
"""
from __future__ import annotations

import os
import tempfile

from dotenv import dotenv_values

# Importing ``chat_api`` composes a default app at module load
# (chat_api/main.py: ``app = create_app()``), which constructs the Tabby LLM
# client and requires TABBY_API_TOKEN. CI runs with no .env, so the import would
# crash. Supply a dummy token ONLY when neither the environment nor a local .env
# provides one — so the suite imports and unit-tests the app with NO live LLM.
# ``dotenv_values()`` reads .env into a dict WITHOUT mutating os.environ, so we
# never leak real .env values into the process (that would defeat tests that
# construct settings with ``_env_file=None`` to assert code-level defaults).
# Tests that need a live Tabby/Neo4j/embedder still skip when it's unreachable.
if not os.environ.get("TABBY_API_TOKEN") and not dotenv_values().get("TABBY_API_TOKEN"):
    os.environ["TABBY_API_TOKEN"] = "test-token"

import pytest


@pytest.fixture(scope="session")
def settings():
    from graph_rag.config import settings as s

    return s


@pytest.fixture
def tmp_chroma_dir(monkeypatch):
    """Force ChromaStore to use a throwaway directory for the duration of a test."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
    # Provider-agnostic: just try to embed. Works for Ollama/bge-large or Tabby.
    try:
        from graph_rag.embeddings import get_embedder

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
            "Embedder not reachable — ensure Ollama is running with bge-large loaded "
            "(OLLAMA_BASE_URL=http://localhost:11434, OLLAMA_EMBEDDING_MODEL=bge-large)."
        )
