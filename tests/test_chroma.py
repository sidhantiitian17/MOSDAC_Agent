"""Tests for vector_store/chroma_store.py.

Uses a small fake embedder so this test runs without a live Gemini API key.
"""
from __future__ import annotations

import tempfile

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings


class FakeEmbedder(Embeddings):
    """Deterministic 8-dim embedding based on character frequencies."""

    DIM = 8

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for i, ch in enumerate(text.lower()):
            vec[i % self.DIM] += (ord(ch) % 32) / 32.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


@pytest.fixture
def store():
    from graph_rag.vector_store.chroma_store import ChromaStore

    tmp_dir = tempfile.mkdtemp()
    s = ChromaStore(embedder=FakeEmbedder(), collection_name="test_col", persist_dir=tmp_dir)
    yield s
    # Release ChromaDB file handles before cleanup (Windows holds locks)
    try:
        s.reset()
        del s
    except Exception:
        pass
    import shutil, gc
    gc.collect()
    shutil.rmtree(tmp_dir, ignore_errors=True)


def test_add_and_count(store):
    docs = [
        Document(page_content="Apple acquired Beats Electronics.", metadata={"chunk_id": "c1"}),
        Document(page_content="Microsoft acquired GitHub.", metadata={"chunk_id": "c2"}),
        Document(page_content="The sky is blue.", metadata={"chunk_id": "c3"}),
    ]
    new_ids = store.add_documents(docs)
    assert len(new_ids) == 3
    assert store.count() == 3


def test_add_is_idempotent(store):
    docs = [Document(page_content="foo", metadata={"chunk_id": "c1"})]
    store.add_documents(docs)
    new_ids = store.add_documents(docs)
    assert new_ids == [], "second insert of same chunk_id should be a no-op"
    assert store.count() == 1


def test_similarity_search_returns_matching_doc(store):
    docs = [
        Document(page_content="Apple acquired Beats Electronics.", metadata={"chunk_id": "c1"}),
        Document(page_content="Microsoft acquired GitHub.", metadata={"chunk_id": "c2"}),
    ]
    store.add_documents(docs)
    results = store.similarity_search("Beats", k=2)
    assert len(results) >= 1
    assert any("Beats" in r.page_content for r in results)
