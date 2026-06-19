"""Chunk Documents into overlapping passages with stable chunk_ids.

Math-safe: a $$...$$ LaTeX block is never split across two chunks. Documents
tagged content_type='markdown' (Docling output) are split with a Markdown-aware
splitter that respects heading boundaries; legacy plain-text Documents keep the
recursive character splitter — zero change in behaviour for existing content.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownTextSplitter, RecursiveCharacterTextSplitter

from graph_rag.config import settings

# Matches display-math blocks $$...$$ spanning multiple lines.
_MATH_BLOCK = re.compile(r"\$\$.*?\$\$", re.DOTALL)
# Inert placeholder token — square brackets are legal Markdown but this exact
# pattern will never appear in real source text.
_PLACEHOLDER = "[[MATH_{}]]"


def _chunk_id(text: str, source: str, idx: int) -> str:
    digest = hashlib.sha1(f"{source}|{idx}|{text[:64]}".encode("utf-8")).hexdigest()
    return digest[:16]


def _protect_math(text: str) -> tuple[str, list[str]]:
    """Replace each $$...$$ block with a placeholder; return the originals."""
    formulas: list[str] = []

    def _stash(m: re.Match) -> str:
        formulas.append(m.group(0))
        return _PLACEHOLDER.format(len(formulas) - 1)

    return _MATH_BLOCK.sub(_stash, text), formulas


def _restore_math(text: str, formulas: list[str]) -> str:
    for i, formula in enumerate(formulas):
        text = text.replace(_PLACEHOLDER.format(i), formula)
    return text


def _split_one(doc: Document, chunk_size: int, chunk_overlap: int) -> list[Document]:
    # Pre-chunked docs come from the preprocessing layer (MarkdownHeaderTextSplitter).
    # They are already correctly split — re-splitting them would corrupt math blocks
    # and table rows that the header splitter intentionally kept whole.
    if doc.metadata.get("pre_chunked"):
        return [doc]

    is_markdown = doc.metadata.get("content_type") == "markdown"

    if is_markdown:
        # Protect formulas first so the splitter treats each $$...$$ as an
        # atomic token — a formula can never be severed across two chunks.
        protected, formulas = _protect_math(doc.page_content)
        splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        texts = [_restore_math(p, formulas) for p in splitter.split_text(protected)]
    else:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        texts = [p.page_content for p in splitter.split_documents([doc])]

    return [
        Document(page_content=t, metadata=dict(doc.metadata)) for t in texts if t.strip()
    ]


def split_documents(
    documents: Iterable[Document],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Document]:
    cs = chunk_size or settings.chunk_size
    co = chunk_overlap or settings.chunk_overlap

    out: list[Document] = []
    for doc in documents:
        source = doc.metadata.get("source", "unknown")
        for i, piece in enumerate(_split_one(doc, cs, co)):
            piece.metadata["chunk_id"] = _chunk_id(piece.page_content, source, i)
            piece.metadata["chunk_index"] = i
            out.append(piece)
    return out
