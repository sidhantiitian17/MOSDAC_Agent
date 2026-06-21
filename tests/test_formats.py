"""Tests for the format registry and registry-driven loader dispatch.

These exercise the anti-hardcoding refactor (alldoc.md §9): adding/disabling a
format is driven entirely by graph_rag/ingestion/formats.py, and source_type
flows consistently from the registry into chunk metadata. No Docling required —
the Docling parse step is mocked.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.documents import Document

from graph_rag.ingestion import formats
from graph_rag.preprocessing.preprocessor import enrich_chunks


# ── Registry table ──────────────────────────────────────────────────────────

def test_core_and_new_formats_registered():
    for suffix in (".pdf", ".html", ".txt", ".docx", ".xlsx", ".pptx", ".csv", ".png", ".gif"):
        assert formats.get_spec(suffix) is not None, suffix


def test_source_types_are_canonical_per_suffix():
    assert formats.get_spec(".pdf").source_type == "pdf"
    assert formats.get_spec(".html").source_type == "html"
    assert formats.get_spec(".docx").source_type == "docx"
    assert formats.get_spec(".xlsx").source_type == "xlsx"
    # every raster image variant collapses to one source_type
    assert formats.get_spec(".jpg").source_type == "image"
    assert formats.get_spec(".gif").source_type == "image"


def test_gif_normalizes_first():
    assert formats.get_spec(".gif").pre_normalize == "gif_to_png"
    assert formats.get_spec(".png").pre_normalize is None


def test_docling_format_names_are_distinct_and_enabled():
    names = formats.docling_input_format_names()
    assert "IMAGE" in names and names.count("IMAGE") == 1  # deduped across image suffixes
    assert {"PDF", "HTML", "DOCX", "XLSX", "PPTX", "CSV"} <= set(names)
    assert "ASCIIDOC" in names


def test_unknown_suffix_is_unsupported():
    assert formats.get_spec(".xyz") is None
    assert formats.is_supported(".xyz") is False


# ── Kill-switches (config-driven, no code change) ───────────────────────────

def test_images_killswitch_removes_image_suffixes(monkeypatch):
    from graph_rag.config import settings

    monkeypatch.setattr(settings, "ingest_enable_images", False)
    assert formats.is_supported(".png") is False
    assert ".png" not in formats.supported_suffixes()
    assert "IMAGE" not in formats.docling_input_format_names()
    # core formats unaffected
    assert formats.is_supported(".pdf") is True


def test_office_killswitch_removes_office_suffixes(monkeypatch):
    from graph_rag.config import settings

    monkeypatch.setattr(settings, "ingest_enable_office", False)
    assert formats.is_supported(".docx") is False
    assert "DOCX" not in formats.docling_input_format_names()
    assert formats.is_supported(".html") is True


# ── Size guard ──────────────────────────────────────────────────────────────

def test_image_size_guard(tmp_path: Path, monkeypatch):
    from graph_rag.config import settings

    monkeypatch.setattr(settings, "ingest_image_max_mb", 1)
    big = tmp_path / "huge.png"
    big.write_bytes(b"\x00" * (2 * 1024 * 1024))  # 2 MB
    ok, reason = formats.within_size_limit(formats.get_spec(".png"), big)
    assert ok is False and "cap" in reason
    # PDF has no size_limit_flag → always within limit here
    ok2, _ = formats.within_size_limit(formats.get_spec(".pdf"), big)
    assert ok2 is True


# ── enrich_chunks pulls per-type metadata from the registry ─────────────────

def _chunks():
    return [Document(page_content="Some content about INSAT.", metadata={})]


def test_enrich_office_sets_document_domain(tmp_path: Path):
    out = enrich_chunks(_chunks(), tmp_path / "a.docx", "docx")
    meta = out[0].metadata
    assert meta["source_type"] == "docx"
    assert meta["domain_type"] == "document"
    assert meta["pre_chunked"] is True
    assert "page_number" not in meta  # not a paginated type


def test_enrich_image_sets_image_domain(tmp_path: Path):
    out = enrich_chunks(_chunks(), tmp_path / "a.png", "image")
    assert out[0].metadata["domain_type"] == "image_ocr"


def test_enrich_pdf_preserves_page_number_no_domain(tmp_path: Path):
    out = enrich_chunks(_chunks(), tmp_path / "a.pdf", "pdf")
    meta = out[0].metadata
    assert meta["source_type"] == "pdf"
    assert "page_number" in meta
    assert "domain_type" not in meta  # pdf keeps its existing (domain-less) metadata


def test_enrich_html_preserves_web_scrape_domain(tmp_path: Path):
    out = enrich_chunks(_chunks(), tmp_path / "a.html", "html")
    assert out[0].metadata["domain_type"] == "web_scrape"


# ── load_file dispatch routes via the registry ──────────────────────────────

def test_load_file_routes_docx_through_docling(tmp_path: Path, monkeypatch):
    from graph_rag.ingestion import loader

    f = tmp_path / "report.docx"
    f.write_bytes(b"PK\x03\x04stub")  # not a real docx; preprocess_file is mocked

    captured = {}

    def fake_preprocess(path):
        captured["path"] = Path(path)
        return [Document(page_content="Heading\n\nbody", metadata={"source_type": "docx"})]

    monkeypatch.setattr(
        "graph_rag.preprocessing.preprocessor.preprocess_file", fake_preprocess
    )
    docs = loader.load_file(f)
    assert captured["path"] == f
    assert len(docs) == 1
    assert docs[0].metadata["file_type"] == "docx"
    assert docs[0].metadata["file_name"] == "report.docx"


def test_load_file_disabled_image_returns_empty(tmp_path: Path, monkeypatch):
    from graph_rag.config import settings
    from graph_rag.ingestion import loader

    monkeypatch.setattr(settings, "ingest_enable_images", False)
    f = tmp_path / "pic.png"
    f.write_bytes(b"\x89PNG\r\n")
    assert loader.load_file(f) == []


def test_load_file_unknown_suffix_returns_empty(tmp_path: Path):
    from graph_rag.ingestion import loader

    f = tmp_path / "data.xyz"
    f.write_text("whatever")
    assert loader.load_file(f) == []
