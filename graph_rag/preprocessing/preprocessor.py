"""Universal document preprocessing layer for the MOSDAC RAG pipeline.

Turns a single PDF or HTML file into clean, math-safe, header-chunked LangChain
Documents ready for ingestion. Handles complex scientific PDFs (LaTeX formulas,
heatmaps via OCR) and noisy web HTML behind one entry point: `preprocess_file()`.

Also exposes `clean_markdown`, `chunk_markdown`, and `enrich_chunks` so that
`loader.py` can apply only the post-Docling steps to PDFs while keeping the full
pypdf → PyMuPDF → OCR fallback cascade intact.

Robustness notes for the 32B production model:
  * Every stage degrades gracefully — a Docling failure, an OCR miss, or a
    malformed header never aborts the run; we log and fall back.
  * LaTeX is treated as an opaque, immutable token from the moment it is parsed
    until the chunk is emitted, so the regex cleaner can never corrupt an equation
    the LLM will later reason over.
"""
from __future__ import annotations

import logging
import re
import tempfile
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup, Comment
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter

logger = logging.getLogger(__name__)

PDF_SUFFIXES = {".pdf"}
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}

# Tags that carry no document content — chrome, scripts, layout. Removed wholesale.
_JUNK_TAGS = (
    "nav", "footer", "header", "script", "style", "noscript",
    "aside", "form", "button", "iframe", "svg", "menu",
)

# Substring signatures for popup / cookie / share / banner containers. Matched
# case-insensitively against the `class` and `id` attributes. Kept as substrings
# (not exact) because CSS frameworks namespace them ("cookie-consent-banner").
_JUNK_ATTR_SIGNATURES = (
    "cookie", "consent", "popup", "modal", "banner", "newsletter",
    "subscribe", "social", "share", "advert", "sidebar", "breadcrumb",
)


# ── Step 1: Input router & HTML filter ──────────────────────────────────────

def _is_junk_container(tag) -> bool:
    """True if a tag's class/id looks like web chrome (cookie bar, popup, etc.)."""
    attrs = " ".join(tag.get("class", []) + [tag.get("id", "")]).lower()
    return any(sig in attrs for sig in _JUNK_ATTR_SIGNATURES)


def _clean_html_to_tempfile(path: Path) -> Path:
    """Strip web junk from an HTML file and write a clean temp HTML file.

    We DELETE rather than unwrap so the junk's text never reaches Docling, but we
    keep <h1-6>, <table>, <p>, <ul> etc. intact so Docling can recover real Markdown
    structure. Headings are what drive Step 4's MarkdownHeaderTextSplitter — if we
    used get_text() here (as the old _load_html did), we'd lose all section boundaries.
    """
    # errors="ignore": ISRO scrapes mix encodings; never abort on a stray byte.
    raw = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "lxml")

    # Pass 1: remove known junk tag types wholesale.
    for tag in soup(list(_JUNK_TAGS)):
        tag.decompose()

    # Pass 2: strip HTML comments — they can carry injected tracking pixels or
    # conditional IE blocks that confuse the Docling HTML parser.
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Pass 3: remove any remaining tag whose class/id signals web chrome
    # (cookie banners, modals, share widgets). Must come after Pass 1 so that
    # decomposed tags' children don't reappear in find_all(True).
    for tag in soup.find_all(True):
        if _is_junk_container(tag):
            tag.decompose()

    # Prefer the <body> subtree — it excludes <head> scripts/meta that survived
    # Pass 1 (e.g. inline <style> inside <head> with non-standard tag wrappers).
    body = soup.body or soup
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".html", prefix="mosdac_clean_")
    tmp = Path(tmp_path)
    try:
        import os
        os.close(tmp_fd)
        tmp.write_text(str(body), encoding="utf-8")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("HTML filter: %s → %s (%d bytes)", path.name, tmp.name, tmp.stat().st_size)
    return tmp


# ── Step 2: Core parsing (Docling) ──────────────────────────────────────────

@lru_cache(maxsize=2)
def _build_converter():
    """Build & cache a Docling DocumentConverter (model load is expensive).

    Configuration mirrors graph_rag/ingestion/docling_parser.py so both paths
    produce identical Markdown for the same input:
      do_ocr                = True  → Tesseract reads burned-in labels on heatmaps
      do_table_structure    = True  → TableFormer produces real Markdown tables
      do_formula_enrichment = True  → CodeFormula wraps equations as $$...$$ LaTeX
      VLM picture pipeline  = OFF   → OCR text inside images only, no captioning
    TesseractCliOcrOptions matches the system tesseract binary installed in
    Dockerfile.api — more robust than the Python pytesseract binding under load.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    ocr_options = TesseractCliOcrOptions(lang=["eng"])

    pdf_opts = PdfPipelineOptions()
    pdf_opts.do_ocr = True
    pdf_opts.ocr_options = ocr_options
    pdf_opts.do_table_structure = True
    pdf_opts.do_formula_enrichment = True
    pdf_opts.table_structure_options.do_cell_matching = True
    # Never enable VLM picture description — it would make model-load non-deterministic
    # and burn GPU memory needed for the 32B extraction model.
    pdf_opts.do_picture_description = False
    pdf_opts.do_picture_classification = False
    pdf_opts.generate_picture_images = False

    return DocumentConverter(
        # Registering HTML here lets the same converter handle cleaned temp files
        # without a separate converter instance.
        allowed_formats=[InputFormat.PDF, InputFormat.HTML],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)},
    )


def _parse_to_markdown(path: Path) -> str:
    """Convert a clean PDF or HTML file into one Markdown string via Docling.

    Raises ValueError on empty output so the caller can log and fall back — no
    silent failures that produce empty chunks downstream.
    """
    result = _build_converter().convert(str(path))
    markdown = result.document.export_to_markdown()
    if not markdown or not markdown.strip():
        raise ValueError(f"Docling produced empty Markdown for {path.name}")
    logger.info("Docling parsed %s → %d chars of Markdown", path.name, len(markdown))
    return markdown


# ── Step 3: Post-processing (LaTeX-safe regex noise cleaner) ────────────────

# Match display math first ($$...$$, possibly multiline) BEFORE inline so that
# a `$$` fence is never mistaken for two empty inline spans. DOTALL is required
# for display blocks that span multiple lines (e.g. matrices).
_DISPLAY_MATH = re.compile(r"\$\$.*?\$\$", re.DOTALL)

# Inline math: $...$  but NOT $$...$$.
# (?<!\$) lookbehind: don't match the second $ of a $$ open fence.
# (?!\$)  lookahead : don't match the first $ of a $$ close fence.
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)")

# NUL-byte wrapper guarantees the placeholder cannot collide with any real text
# or Markdown — NUL never appears in UTF-8 document output or Tesseract OCR.
_MATH_PLACEHOLDER = "\x00MATH{}\x00"

# OCR noise patterns applied ONLY to non-math segments:
#
# _SOFT_HYPHEN  — U+00AD (0xAD): Tesseract frequently emits soft hyphens inside
#                 words, breaking exact-match search (e.g. "satel­lite").
# _BROKEN_HYPHEN — Hard hyphen at end-of-line followed by newline: Tesseract
#                  inserts these when a word wraps across scan lines.
#                  Only rejoin when both sides are \w to preserve real compounds
#                  like "L-band" (no newline between "L" and "band").
# _STRAY_GLYPHS  — Non-alphanumeric characters floating between whitespace that
#                  are not valid Markdown or scientific punctuation. Catches box-
#                  drawing chars (│ ┼ ▯), Greek letters mis-OCR'd outside math
#                  blocks, and stray diacritics.  Whitelist keeps: # * - | . , ;
#                  : ( ) [ ] / % $ (dollar signs outside math are preserved; the
#                  math protection above already hid real LaTeX).
# _MULTISPACE    — Tesseract sprays extra spaces inside words on low-DPI scans.
# _BLANK_LINES   — 3+ consecutive newlines → exactly 2 (one blank line).
#                  Keeps paragraph structure the LLM and splitter both rely on.
_SOFT_HYPHEN = re.compile("­")
_BROKEN_HYPHEN = re.compile(r"(\w+)-\s*\n\s*(\w+)")
_STRAY_GLYPHS = re.compile(r"(?<=\s)[^\w\s$#*\-|.,;:()\[\]/%]+(?=\s)")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")


def _protect_math(text: str) -> tuple[str, list[str]]:
    """Replace every math span with a NUL-wrapped placeholder; stash originals.

    This is the invariant that makes the entire cleaner safe: from this point on,
    no cleaning regex ever sees the interior of a LaTeX block.
    """
    stash: list[str] = []

    def _swap(m: re.Match) -> str:
        stash.append(m.group(0))
        return _MATH_PLACEHOLDER.format(len(stash) - 1)

    text = _DISPLAY_MATH.sub(_swap, text)
    text = _INLINE_MATH.sub(_swap, text)
    return text, stash


def _restore_math(text: str, stash: list[str]) -> str:
    for i, formula in enumerate(stash):
        text = text.replace(_MATH_PLACEHOLDER.format(i), formula)
    return text


def clean_markdown(markdown: str) -> str:
    """Remove OCR noise from Docling Markdown without touching any math blocks.

    Public so loader.py can call it on PDF Markdown after the Docling parse step,
    keeping the existing pypdf → PyMuPDF → OCR fallback cascade intact for PDFs
    while still getting regex cleaning on the happy path.
    """
    protected, stash = _protect_math(markdown)

    protected = _SOFT_HYPHEN.sub("", protected)
    protected = _BROKEN_HYPHEN.sub(r"\1\2", protected)
    protected = _STRAY_GLYPHS.sub(" ", protected)
    protected = _MULTISPACE.sub(" ", protected)
    protected = _BLANK_LINES.sub("\n\n", protected)

    return _restore_math(protected, stash).strip()


# ── Step 4: Unified chunking (structure-aware, never slices tables/equations) ─

# Split on H1 / H2 / H3 headers only. H4+ are kept inside their parent section
# so deeply nested subsections don't become tiny orphan chunks.
_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def chunk_markdown(markdown: str) -> list[Document]:
    """Split cleaned Markdown on section headers; sections stay whole.

    strip_headers=False keeps the heading in the chunk body so the 32B model sees
    the section title as context when it reasons over the passage.

    We deliberately do NOT chain a character-level splitter. The 32B model can
    handle a full scientific section (~2-4 k tokens), and any size-based cut
    risks severing a $$...$$ block or a table mid-row. If a single section is
    pathologically large it is still better to pass it whole than to corrupt it.

    Public so loader.py can use it for PDF post-processing independently of the
    HTML preprocessing path.
    """
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS_TO_SPLIT_ON,
        strip_headers=False,
    )
    docs = splitter.split_text(markdown)
    # Header-less documents (e.g. raw data tables, plain text PDFs) yield zero
    # splits — keep as one chunk rather than dropping the document entirely.
    if not docs and markdown.strip():
        return [Document(page_content=markdown)]
    return [d for d in docs if d.page_content.strip()]


# ── Step 5: Dynamic metadata enrichment ─────────────────────────────────────

def enrich_chunks(
    chunks: list[Document],
    path: Path,
    source_type: str,
) -> list[Document]:
    """Attach source-aware metadata to every chunk (creates new dicts; no mutation).

    Tags the chunks with `pre_chunked=True` so `split_documents` in splitter.py
    knows to skip re-splitting — these chunks are already header-split.

    Public so loader.py can call it independently for the PDF post-processing path.
    """
    enriched: list[Document] = []
    for idx, chunk in enumerate(chunks):
        meta = dict(chunk.metadata)      # copy — never mutate the splitter's dict
        meta.update(
            {
                "source": str(path),
                "file_name": path.name,
                "source_type": source_type,
                "chunk_index": idx,
                "content_type": "markdown",
                # Signal to splitter.py: these are already correctly split.
                "pre_chunked": True,
            }
        )
        if source_type == "pdf":
            # Docling's markdown export does not surface a reliable per-chunk page
            # number after heading-based splitting. Preserve any value the splitter
            # already extracted; default to None so downstream code can branch on
            # its presence.
            meta.setdefault("page_number", chunk.metadata.get("page_number"))
        else:
            meta["domain_type"] = "web_scrape"
        enriched.append(Document(page_content=chunk.page_content, metadata=meta))
    return enriched


# ── Public entry point ──────────────────────────────────────────────────────

def preprocess_file(path: str | Path) -> list[Document]:
    """Run the full 5-step pipeline on one PDF or HTML file → enriched chunks.

    Steps:
      1. Route: HTML → BeautifulSoup junk filter → temp file; PDF → pass through.
      2. Parse: Docling (OCR + tables + LaTeX) → one Markdown string.
      3. Clean: LaTeX-safe regex noise cleaner.
      4. Chunk: MarkdownHeaderTextSplitter (#/##/###).
      5. Enrich: source-type-aware metadata on every chunk.

    Raises ValueError for unsupported file types. Always cleans up the temp HTML
    file even if a later step raises — the `finally` block is unconditional.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    tmp_html: Path | None = None

    try:
        if suffix in PDF_SUFFIXES:
            source_type: str = "pdf"
            parse_target: Path = path
        elif suffix in HTML_SUFFIXES:
            source_type = "html"
            tmp_html = _clean_html_to_tempfile(path)      # Step 1
            parse_target = tmp_html
        else:
            raise ValueError(
                f"Unsupported file type: {suffix!r} — expected .pdf or .html/.htm/.xhtml"
            )

        markdown = _parse_to_markdown(parse_target)       # Step 2
        markdown = clean_markdown(markdown)               # Step 3
        chunks = chunk_markdown(markdown)                 # Step 4
        return enrich_chunks(chunks, path, source_type)   # Step 5

    finally:
        if tmp_html is not None:
            tmp_html.unlink(missing_ok=True)


# ── CLI smoke test ───────────────────────────────────────────────────────────
# Run: python -m graph_rag.preprocessing.preprocessor <file.pdf|file.html>

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) != 2:
        print("usage: python -m graph_rag.preprocessing.preprocessor <file.pdf|file.html>")
        raise SystemExit(2)

    result_chunks = preprocess_file(sys.argv[1])
    print(f"\n=== {len(result_chunks)} chunk(s) produced ===\n")
    for i, c in enumerate(result_chunks):
        print(f"--- chunk {i} ---")
        print(f"    meta : {c.metadata}")
        print(f"    text : {c.page_content[:600]!r}")
        print()
