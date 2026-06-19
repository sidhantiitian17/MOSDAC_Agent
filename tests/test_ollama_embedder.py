"""Live tests for the Ollama bge-large embedder. Skip when Ollama is unreachable.

Verifies the endpoint comes from settings (.env only — never hardcoded) and
that get_embedder() returns an OllamaEmbedder configured with bge-large.
"""
from __future__ import annotations

import pytest


def _ollama_up() -> bool:
    try:
        import requests

        from graph_rag.config import settings

        r = requests.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


_SKIP = pytest.mark.skipif(not _ollama_up(), reason="Ollama not reachable on OLLAMA_BASE_URL")


def test_embedder_url_built_from_settings_not_hardcoded():
    # The endpoint must come from configuration, with /api/embeddings appended.
    from graph_rag.embeddings.ollama_embedder import OllamaEmbedder

    emb = OllamaEmbedder(model="bge-large", base_url="http://example.test:11434")
    assert emb._url == "http://example.test:11434/api/embeddings"


def test_missing_base_url_raises():
    from graph_rag.embeddings.ollama_embedder import OllamaEmbedder

    with pytest.raises(ValueError):
        OllamaEmbedder(model="bge-large", base_url="")


@_SKIP
def test_embed_query_returns_1024_dim_vector():
    from graph_rag.config import settings
    from graph_rag.embeddings.ollama_embedder import OllamaEmbedder

    emb = OllamaEmbedder(
        model=settings.ollama_embedding_model, base_url=settings.ollama_base_url
    )
    v = emb.embed_query("sea surface temperature")
    assert isinstance(v, list)
    assert len(v) == 1024


@_SKIP
def test_embed_documents_batches():
    from graph_rag.config import settings
    from graph_rag.embeddings.ollama_embedder import OllamaEmbedder

    emb = OllamaEmbedder(
        model=settings.ollama_embedding_model, base_url=settings.ollama_base_url
    )
    docs = emb.embed_documents(["chlorophyll", "scatterometer"])
    assert len(docs) == 2
    assert all(len(d) == 1024 for d in docs)


@_SKIP
def test_get_embedder_returns_ollama_instance():
    from graph_rag.embeddings.ollama_embedder import OllamaEmbedder, get_embedder

    get_embedder.cache_clear()
    try:
        assert isinstance(get_embedder(), OllamaEmbedder)
    finally:
        get_embedder.cache_clear()
