"""Discover & load PDF and HTML files from configured source folders."""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.documents import Document

from graph_rag.config import settings

logger = logging.getLogger(__name__)

# pypdf emits WARNING-level logs for corrupt-but-loadable files (duplicate dict entries,
# missing EOF markers). These are benign — real errors raise exceptions. Silence the noise.
logging.getLogger("pypdf.generic._data_structures").setLevel(logging.ERROR)
logging.getLogger("pypdf._reader").setLevel(logging.ERROR)

PDF_SUFFIXES = {".pdf"}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}
TEXT_SUFFIXES = {".txt", ".md"}


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


def _load_pdf(path: Path) -> list[Document]:
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
    try:
        from bs4 import BeautifulSoup

        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            soup = BeautifulSoup(fh.read(), "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return [Document(page_content=text, metadata={})] if text else []
    except Exception as exc:
        logger.warning("Failed to load HTML %s: %s", path, exc)
        return []


def _load_text(path: Path) -> list[Document]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return [Document(page_content=text, metadata={})] if text.strip() else []
    except Exception as exc:
        logger.warning("Failed to load text file %s: %s", path, exc)
        return []


def load_file(path: Path) -> list[Document]:
    """Dispatch to the right loader based on suffix; tag metadata for traceability."""
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        file_type = "pdf"
        docs = _load_pdf(path)
    elif suffix in HTML_SUFFIXES:
        file_type = "html"
        docs = _load_html(path)
    elif suffix in TEXT_SUFFIXES:
        file_type = "text"
        docs = _load_text(path)
    else:
        return []

    for d in docs:
        d.metadata.setdefault("source", str(path))
        d.metadata["file_type"] = file_type
        d.metadata["file_name"] = path.name
    return docs


def load_all_documents(folders: list[Path] | None = None) -> list[Document]:
    """Walk every configured folder; load every PDF/HTML/text file."""
    folders = folders or settings.source_folders()
    all_docs: list[Document] = []
    for folder in folders:
        if not folder.exists():
            logger.warning("Source folder does not exist: %s", folder)
            continue
        for path in folder.rglob("*"):
            if path.is_file():
                all_docs.extend(load_file(path))
    logger.info("Loaded %d documents from %s", len(all_docs), folders)
    return all_docs
