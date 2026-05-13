"""Tests for ingestion/loader.py — file discovery and parsing."""
from __future__ import annotations

from pathlib import Path

from graph_rag.ingestion.loader import load_all_documents, load_file


def test_load_html_file(tmp_path: Path):
    html = tmp_path / "page.html"
    html.write_text(
        "<html><body><h1>Apple</h1><p>Apple acquired Beats Electronics.</p></body></html>",
        encoding="utf-8",
    )
    docs = load_file(html)
    assert len(docs) == 1
    assert "Apple" in docs[0].page_content
    assert docs[0].metadata["file_type"] == "html"
    assert docs[0].metadata["file_name"] == "page.html"


def test_load_text_file(tmp_path: Path):
    txt = tmp_path / "note.txt"
    txt.write_text("Just plain text content.", encoding="utf-8")
    docs = load_file(txt)
    assert len(docs) == 1
    assert docs[0].metadata["file_type"] == "text"


def test_unsupported_extension(tmp_path: Path):
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG\r\n")
    assert load_file(f) == []


def test_load_all_documents_walks_folders(tmp_path: Path):
    (tmp_path / "a.html").write_text("<p>One</p>", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("Two", encoding="utf-8")

    docs = load_all_documents([tmp_path])
    contents = " ".join(d.page_content for d in docs)
    assert "One" in contents
    assert "Two" in contents


def test_load_all_documents_handles_missing_folder(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    assert load_all_documents([missing]) == []
