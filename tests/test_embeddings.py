"""Tests for embeddings/nvidia_embedder.py."""
from __future__ import annotations

import pytest

from tests.conftest import skip_if_no_nvidia


def test_embedder_requires_api_key(monkeypatch):
    from graph_rag.config import Settings

    s = Settings(_env_file=None, nvidia_api_key="missing")
    monkeypatch.setattr("graph_rag.config.settings", s, raising=False)

    from graph_rag.embeddings import nvidia_embedder

    nvidia_embedder.get_embedder.cache_clear()
    with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
        nvidia_embedder.get_embedder()


def test_embedder_returns_nonempty_vector(nvidia_available):
    skip_if_no_nvidia(nvidia_available)
    from graph_rag.embeddings.nvidia_embedder import get_embedder

    get_embedder.cache_clear()
    emb = get_embedder()
    vec = emb.embed_query("The quick brown fox jumps over the lazy dog.")
    assert isinstance(vec, list)
    assert len(vec) > 0
    assert all(isinstance(x, float) for x in vec)


def test_similar_texts_have_higher_similarity(nvidia_available):
    skip_if_no_nvidia(nvidia_available)
    from graph_rag.embeddings.nvidia_embedder import get_embedder

    get_embedder.cache_clear()
    emb = get_embedder()
    a = emb.embed_query("Dogs are loyal pets.")
    b = emb.embed_query("Canines are faithful companions.")
    c = emb.embed_query("Quantum physics describes particle behaviour.")

    def cosine(u, v):
        import math

        dot = sum(x * y for x, y in zip(u, v))
        nu = math.sqrt(sum(x * x for x in u))
        nv = math.sqrt(sum(y * y for y in v))
        return dot / (nu * nv) if nu and nv else 0.0

    assert cosine(a, b) > cosine(a, c)
