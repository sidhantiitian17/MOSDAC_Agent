"""Tests for ChromaStore.check_embedding_compat() (Bug #3 fix)."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from graph_rag.vector_store.chroma_store import ChromaStore


def _make_store(stored_dim: int | None = None, count: int = 1) -> ChromaStore:
    """Return a ChromaStore with fully-mocked Chroma internals.

    Bypasses __init__ entirely (Chroma is lazily imported inside __init__ so
    it can't be patched at the module level).
    """
    store_instance = MagicMock()
    store_instance._collection.count.return_value = count
    if stored_dim is not None:
        store_instance._collection.get.return_value = {
            "embeddings": [[0.0] * stored_dim],
            "ids": ["chunk-0"],
        }
    else:
        store_instance._collection.get.return_value = {"embeddings": [], "ids": []}

    cs = ChromaStore.__new__(ChromaStore)
    cs._embedder = MagicMock()
    cs._collection_name = "test_collection"
    cs._persist_dir = "./test_chroma"
    cs._store = store_instance
    return cs


def test_check_embedding_compat_passes_when_dims_match():
    cs = _make_store(stored_dim=1024)
    cs._embedder.embed_query.return_value = [0.0] * 1024
    cs.check_embedding_compat()  # must not raise


def test_check_embedding_compat_raises_on_dim_mismatch():
    cs = _make_store(stored_dim=512)
    cs._embedder.embed_query.return_value = [0.0] * 1024

    with pytest.raises(RuntimeError, match="Embedding dimension mismatch"):
        cs.check_embedding_compat()


def test_check_embedding_compat_error_message_includes_dims():
    cs = _make_store(stored_dim=768)
    cs._embedder.embed_query.return_value = [0.0] * 1024

    with pytest.raises(RuntimeError) as exc_info:
        cs.check_embedding_compat()

    msg = str(exc_info.value)
    assert "768" in msg
    assert "1024" in msg
    assert "test_collection" in msg


def test_check_embedding_compat_skips_empty_collection():
    cs = _make_store(stored_dim=512, count=0)
    cs._embedder.embed_query.return_value = [0.0] * 1024

    cs.check_embedding_compat()  # must not raise
    cs._embedder.embed_query.assert_not_called()


def test_check_embedding_compat_skips_when_no_embedder():
    cs = _make_store(stored_dim=1024)
    cs._embedder = None

    cs.check_embedding_compat()  # must not raise


def test_check_embedding_compat_warns_on_offline_embedder(caplog):
    cs = _make_store(stored_dim=1024)
    cs._embedder.embed_query.side_effect = ConnectionError("Ollama not running")

    with caplog.at_level(logging.WARNING, logger="graph_rag.vector_store.chroma_store"):
        cs.check_embedding_compat()  # must not raise

    assert any("Could not verify" in r.message for r in caplog.records)
