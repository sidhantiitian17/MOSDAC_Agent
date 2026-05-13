"""Tests for ingestion/splitter.py — chunking behaviour and metadata."""
from __future__ import annotations

from langchain_core.documents import Document

from graph_rag.ingestion.splitter import split_documents


def test_split_short_doc_returns_single_chunk():
    doc = Document(page_content="Hello world.", metadata={"source": "test.txt"})
    chunks = split_documents([doc], chunk_size=1000, chunk_overlap=0)
    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_id"]
    assert chunks[0].metadata["chunk_index"] == 0


def test_split_long_doc_produces_multiple_chunks():
    long_text = ". ".join(f"Sentence number {i}" for i in range(200))
    doc = Document(page_content=long_text, metadata={"source": "long.txt"})
    chunks = split_documents([doc], chunk_size=200, chunk_overlap=20)
    assert len(chunks) >= 3
    chunk_ids = {c.metadata["chunk_id"] for c in chunks}
    assert len(chunk_ids) == len(chunks), "chunk ids should be unique"


def test_chunk_ids_are_deterministic():
    doc = Document(page_content="ABC " * 50, metadata={"source": "x.txt"})
    a = split_documents([doc], chunk_size=80, chunk_overlap=10)
    b = split_documents([doc], chunk_size=80, chunk_overlap=10)
    assert [c.metadata["chunk_id"] for c in a] == [c.metadata["chunk_id"] for c in b]
