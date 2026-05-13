"""Chunk Document objects into overlapping passages with stable chunk_ids."""
from __future__ import annotations

import hashlib
from typing import Iterable

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from graph_rag.config import settings


def _chunk_id(text: str, source: str, idx: int) -> str:
    digest = hashlib.sha1(f"{source}|{idx}|{text[:64]}".encode("utf-8")).hexdigest()
    return digest[:16]


def split_documents(
    documents: Iterable[Document],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size or settings.chunk_size,
        chunk_overlap=chunk_overlap or settings.chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    out: list[Document] = []
    for doc in documents:
        pieces = splitter.split_documents([doc])
        source = doc.metadata.get("source", "unknown")
        for i, piece in enumerate(pieces):
            piece.metadata["chunk_id"] = _chunk_id(piece.page_content, source, i)
            piece.metadata["chunk_index"] = i
            out.append(piece)
    return out
