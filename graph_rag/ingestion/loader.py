"""Discover & load PDF and HTML files from configured source folders."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator

from langchain_core.documents import Document

from graph_rag.config import settings
from graph_rag.ingestion import formats
from graph_rag.ingestion.manifest import IngestionManifest, compute_file_hash

logger = logging.getLogger(__name__)

# pypdf emits WARNING-level logs for corrupt-but-loadable files (duplicate dict entries,
# missing EOF markers). These are benign — real errors raise exceptions. Silence the noise.
logging.getLogger("pypdf.generic._data_structures").setLevel(logging.ERROR)
logging.getLogger("pypdf._reader").setLevel(logging.ERROR)

# All file-format routing (which suffixes are supported, how each is loaded, what
# source_type it is tagged with) is declared once in graph_rag/ingestion/formats.py.
# Adding a format is a registry row there — no edits to the dispatch below.


def _mute_fitz_stderr() -> None:
    """Silence MuPDF C-level messages that go directly to stderr, bypassing Python logging."""
    try:
        import fitz
        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:
        pass


def _has_fitz_format_errors(path: Path) -> bool:
    """Open with fitz to detect format errors before attempting pypdf (~1ms check).

    A corrupt PDF that fitz handles in ~2s can hang pypdf for 6+ minutes before it
    gives up with EOF. Detecting corruption here saves that wasted wall-clock time.
    """
    try:
        import fitz
        _mute_fitz_stderr()
        try:
            fitz.TOOLS.mupdf_warnings(reset=True)  # clear accumulated warnings
        except AttributeError:
            return False  # older fitz without warning API — skip pre-check
        with fitz.open(str(path)):
            pass
        try:
            return bool(fitz.TOOLS.mupdf_warnings(reset=True))
        except AttributeError:
            return False
    except Exception:
        return True  # fitz couldn't even open it — definitely has errors


def _docling_eligible(path: Path) -> bool:
    """Skip Docling for oversized files — route to the streaming OCR fallback instead."""
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return False
    if size_mb > settings.docling_max_file_mb:
        logger.info(
            "Skipping Docling for %s (%.0f MB > %d MB cap)",
            path.name, size_mb, settings.docling_max_file_mb,
        )
        return False
    return True


def _load_pdf(path: Path, source_type: str = "pdf") -> list[Document]:
    # ── Primary: Docling — structured Markdown with math + tables + OCR ────────
    if settings.use_docling and _docling_eligible(path):
        try:
            from graph_rag.ingestion.docling_parser import parse_pdf_to_markdown
            from graph_rag.preprocessing.preprocessor import (
                chunk_markdown,
                clean_markdown,
                enrich_chunks,
            )
            markdown = parse_pdf_to_markdown(path)
            # Apply the preprocessing layer: LaTeX-safe regex clean → header split
            # → metadata enrichment. Returns pre-chunked docs tagged pre_chunked=True
            # so split_documents() passes them through without re-splitting.
            markdown = clean_markdown(markdown)
            chunks = chunk_markdown(markdown)
            return enrich_chunks(chunks, path, source_type)
        except Exception as exc:
            logger.warning(
                "Docling failed for %s (%s) — falling back to cascade", path.name, exc
            )

    # ── Fallback: pypdf → PyMuPDF → OCR cascade (unchanged) ─────────────────
    # Fast pre-check: if fitz reports format errors, pypdf will hang on the same
    # file for minutes. Skip it and go straight to the PyMuPDF recovery path.
    if _has_fitz_format_errors(path):
        logger.debug("Skipping pypdf for %s — fitz detected format errors", path.name)
        return _load_pdf_pymupdf(path)
    try:
        from langchain_community.document_loaders import PyPDFLoader
        return PyPDFLoader(str(path)).load()
    except Exception as exc:
        logger.warning("pypdf failed for %s (%s) — trying PyMuPDF", path.name, exc)
        return _load_pdf_pymupdf(path)


def _load_pdf_pymupdf(path: Path) -> list[Document]:
    try:
        import fitz  # pymupdf
        _mute_fitz_stderr()  # suppress C-level format error messages to stderr
        docs: list[Document] = []
        with fitz.open(str(path)) as pdf:
            for page_num in range(len(pdf)):
                text = pdf[page_num].get_text()
                if text.strip():
                    docs.append(Document(page_content=text, metadata={"page": page_num}))
        if docs:
            logger.info("PyMuPDF recovered %d pages from %s", len(docs), path.name)
            return docs
        logger.warning("PyMuPDF found no text in %s — trying OCR", path.name)
        return _load_pdf_ocr(path)
    except Exception as exc:
        logger.warning("PyMuPDF failed for %s (%s) — trying OCR", path.name, exc)
        return _load_pdf_ocr(path)


def _load_pdf_ocr(path: Path) -> list[Document]:
    """Last-resort OCR: render pages via PyMuPDF pixmap → Tesseract, then pdf2image fallback."""
    docs = _ocr_via_pymupdf(path)
    if docs is not None:
        return docs
    return _ocr_via_pdf2image(path)


def _ocr_via_pymupdf(path: Path) -> list[Document] | None:
    """Render PDF pages with PyMuPDF and OCR each pixmap. Returns None if PDF won't open."""
    try:
        import fitz
        _mute_fitz_stderr()
        import pytesseract
        from PIL import Image
        from graph_rag.config import settings as _settings

        if _settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = _settings.tesseract_cmd

        docs: list[Document] = []
        with fitz.open(str(path)) as pdf:
            for page_num in range(len(pdf)):
                try:
                    pix = pdf[page_num].get_pixmap(dpi=200)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    text = pytesseract.image_to_string(img, lang="eng")
                    if text.strip():
                        docs.append(Document(page_content=text, metadata={"page": page_num}))
                except Exception:
                    continue  # skip unrenderable pages, keep going

        if docs:
            logger.info("OCR (PyMuPDF renderer) recovered %d pages from %s", len(docs), path.name)
        else:
            logger.warning("OCR found no text in %s (blank or purely graphical)", path.name)
        return docs
    except Exception as exc:
        logger.debug("PyMuPDF render/OCR failed for %s: %s — trying pdf2image", path.name, exc)
        return None


def _ocr_via_pdf2image(path: Path) -> list[Document]:
    """Fallback OCR via poppler pdf2image renderer."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
        from graph_rag.config import settings as _settings

        if _settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = _settings.tesseract_cmd

        poppler_path = _settings.poppler_path or None
        images = convert_from_path(str(path), dpi=200, poppler_path=poppler_path)

        docs: list[Document] = []
        for page_num, img in enumerate(images):
            text = pytesseract.image_to_string(img, lang="eng")
            if text.strip():
                docs.append(Document(page_content=text, metadata={"page": page_num}))

        if docs:
            logger.info("OCR (pdf2image) recovered %d pages from %s", len(docs), path.name)
        else:
            logger.warning("OCR found no text in %s (blank or purely graphical)", path.name)
        return docs
    except Exception as exc:
        logger.warning("Failed to load PDF %s: %s", path, exc)
        return []


def _load_html(path: Path) -> list[Document]:
    # Delegate to the full preprocessing pipeline: BS4 junk filter → Docling
    # → LaTeX-safe regex clean → MarkdownHeaderTextSplitter → metadata enrichment.
    # This preserves heading/table structure that the old get_text() path lost.
    try:
        from graph_rag.preprocessing.preprocessor import preprocess_file
        return preprocess_file(path)
    except Exception as exc:
        logger.warning("Preprocessing failed for HTML %s (%s) — skipping", path.name, exc)
        return []


def _load_text(path: Path) -> list[Document]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return [Document(page_content=text, metadata={})] if text.strip() else []
    except Exception as exc:
        logger.warning("Failed to load text file %s: %s", path, exc)
        return []


def _load_via_docling(path: Path, spec: formats.FormatSpec) -> list[Document]:
    """Generic loader for every new Docling format (Office/CSV/AsciiDoc/images/GIF).

    Routes through the full preprocessing pipeline (parse → clean → quality gate
    → chunk → enrich). Honours the per-format size cap and never raises: a parse
    failure is logged and yields ``[]`` so one bad file can't abort the run.
    """
    ok, reason = formats.within_size_limit(spec, path)
    if not ok:
        logger.info("Skipping %s (%s)", path.name, reason)
        return []
    try:
        from graph_rag.preprocessing.preprocessor import preprocess_file
        return preprocess_file(path)
    except Exception as exc:
        logger.warning("Docling ingestion failed for %s (%s) — skipping", path.name, exc)
        return []


# Dispatch table: routing category → handler. Keeps load_file free of any
# hard-coded suffix or source_type literal.
_CATEGORY_LOADERS = {
    formats.CATEGORY_PDF: lambda path, spec: _load_pdf(path, spec.source_type),
    formats.CATEGORY_HTML: lambda path, spec: _load_html(path),
    formats.CATEGORY_TEXT: lambda path, spec: _load_text(path),
    formats.CATEGORY_DOCLING: _load_via_docling,
}


def load_file(path: Path) -> list[Document]:
    """Dispatch to the right loader via the format registry; tag metadata for traceability."""
    spec = formats.get_spec(path.suffix.lower())
    if spec is None or not formats.is_enabled(spec):
        return []  # unknown suffix or a disabled category (e.g. images off)

    handler = _CATEGORY_LOADERS.get(spec.category)
    if handler is None:
        return []
    docs = handler(path, spec)

    for d in docs:
        d.metadata.setdefault("source", str(path))
        # file_type carries the canonical source_type from the registry, so
        # loader and preprocessor metadata always agree (pdf/html/docx/image/…).
        d.metadata["file_type"] = spec.source_type
        d.metadata["file_name"] = path.name
    return docs


def iter_source_files(folders: list[Path] | None = None) -> Iterator[Path]:
    """Yield every currently-enabled file under the configured folders.

    The supported-suffix set is resolved from the registry on each call, so the
    office/image kill-switches take effect without restarting or code changes.
    """
    folders = folders or settings.source_folders()
    supported = formats.supported_suffixes()
    for folder in folders:
        if not folder.exists():
            logger.warning("Source folder does not exist: %s", folder)
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in supported:
                yield path


def load_all_documents(
    folders: list[Path] | None = None,
    *,
    manifest: IngestionManifest | None = None,
    force: bool = False,
) -> list[Document]:
    """Walk every configured folder; load every PDF/HTML/text file.

    When `manifest` is provided (and `force` is False), a file whose SHA-256 content
    hash is already recorded is skipped entirely — never opened, parsed, or OCR'd.
    Every loaded Document is tagged with `metadata["file_hash"]` so the pipeline can
    record the file as ingested once the run completes. With `manifest=None` the
    behaviour is identical to a plain recursive load (no hashing, no `file_hash` tag).
    """
    all_docs: list[Document] = []
    new_files = 0
    skipped = 0
    for path in iter_source_files(folders):
        file_hash: str | None = None
        if manifest is not None:
            file_hash = compute_file_hash(path)
            if not force and manifest.is_ingested(file_hash):
                skipped += 1
                logger.debug("Skipping already-ingested file: %s (%s…)", path.name, file_hash[:12])
                continue

        loaded = load_file(path)
        if not loaded:
            continue
        if file_hash:
            for d in loaded:
                d.metadata["file_hash"] = file_hash
        all_docs.extend(loaded)
        new_files += 1

    if manifest is not None:
        logger.info(
            "Loaded %d documents from %d new file(s); skipped %d already-ingested file(s).",
            len(all_docs),
            new_files,
            skipped,
        )
    else:
        logger.info("Loaded %d documents from %d file(s).", len(all_docs), new_files)
    return all_docs
