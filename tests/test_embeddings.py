"""Tests for graph_rag/embeddings/ollama_embedder.py."""
from __future__ import annotations

import math

import pytest

from tests.conftest import skip_if_no_nomic


def test_get_embedder_reads_url_from_settings(monkeypatch):
    """get_embedder() must build the Ollama URL from settings, never hardcoded."""
    from graph_rag.config import Settings
    from graph_rag.embeddings.ollama_embedder import OllamaEmbedder, get_embedder

    s = Settings(_env_file=None, ollama_base_url="http://sentinel-host:11434", ollama_embedding_model="bge-large")
    monkeypatch.setattr("graph_rag.config.settings", s, raising=False)
    get_embedder.cache_clear()
    try:
        emb = get_embedder()
        assert isinstance(emb, OllamaEmbedder)
        assert "sentinel-host" in emb._url
    finally:
        get_embedder.cache_clear()


def test_embedder_returns_nonempty_vector(nomic_available):
    skip_if_no_nomic(nomic_available)
    from graph_rag.embeddings.ollama_embedder import get_embedder

    get_embedder.cache_clear()
    emb = get_embedder()
    vec = emb.embed_query("The quick brown fox jumps over the lazy dog.")
    assert isinstance(vec, list)
    assert len(vec) > 0
    assert all(isinstance(x, float) for x in vec)


def test_embed_documents_returns_correct_count(nomic_available):
    skip_if_no_nomic(nomic_available)
    from graph_rag.embeddings.ollama_embedder import get_embedder

    get_embedder.cache_clear()
    emb = get_embedder()
    texts = ["Hello world.", "Satellite imagery.", "Monsoon rainfall data."]
    vecs = emb.embed_documents(texts)
    assert len(vecs) == len(texts)
    assert all(len(v) > 0 for v in vecs)


def test_similar_texts_have_higher_similarity(nomic_available):
    skip_if_no_nomic(nomic_available)
    from graph_rag.embeddings.ollama_embedder import get_embedder

    get_embedder.cache_clear()
    emb = get_embedder()
    a = emb.embed_query("Dogs are loyal pets.")
    b = emb.embed_query("Canines are faithful companions.")
    c = emb.embed_query("Quantum physics describes particle behaviour.")

    def cosine(u, v):
        dot = sum(x * y for x, y in zip(u, v))
        nu = math.sqrt(sum(x * x for x in u))
        nv = math.sqrt(sum(y * y for y in v))
        return dot / (nu * nv) if nu and nv else 0.0

    assert cosine(a, b) > cosine(a, c)
