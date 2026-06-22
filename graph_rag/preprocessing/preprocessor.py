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

# File-format routing lives in the single-source-of-truth registry
# (graph_rag/ingestion/formats.py) — no suffix sets are duplicated here.

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
    if not tag.attrs:
        return False
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

@lru_cache(maxsize=1)
def _build_converter():
    """Build & cache the one unified Docling DocumentConverter (model load is expensive).

    A single converter serves every Docling-parsed format — PDF, HTML, DOCX,
    XLSX, PPTX, CSV, AsciiDoc and images — so they all go through identical
    options. The set of allowed formats is derived from the format registry
    (graph_rag/ingestion/formats.py): adding a new format there flows into this
    converter automatically, with no edit here.

    PDF/IMAGE options (OCR + tables + formula, VLM OFF) mirror
    graph_rag/ingestion/docling_parser.py so both paths produce identical
    Markdown. Office/CSV/AsciiDoc need no per-format options — Docling defaults
    already emit structured Markdown.
      do_ocr                = True  → Tesseract reads burned-in labels on heatmaps
      do_table_structure    = True  → TableFormer produces real Markdown tables
      do_formula_enrichment = True  → CodeFormula wraps equations as $$...$$ LaTeX
      VLM picture pipeline  = OFF   → OCR text inside images only, no captioning
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import (
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
    )

    from graph_rag.config import settings
    from graph_rag.ingestion import formats

    ocr_options = TesseractCliOcrOptions(lang=[settings.docling_ocr_lang])

    pdf_opts = PdfPipelineOptions()
    # OFFLINE: load models from the pre-downloaded artifacts dir (no Hub calls).
    if settings.docling_artifacts_path:
        pdf_opts.artifacts_path = settings.docling_artifacts_path
    pdf_opts.do_ocr = True
    pdf_opts.ocr_options = ocr_options
    pdf_opts.do_table_structure = settings.docling_do_table_structure
    pdf_opts.do_formula_enrichment = settings.docling_do_formula_enrichment
    pdf_opts.table_structure_options.do_cell_matching = True
    # Never enable VLM picture description — it would make model-load non-deterministic
    # and burn GPU memory needed for the 32B extraction model.
    pdf_opts.do_picture_description = False
    pdf_opts.do_picture_classification = False
    pdf_opts.generate_picture_images = False

    # Resolve enabled InputFormat names from the registry → enum members. An
    # unknown name (older Docling without that format) is skipped gracefully.
    allowed = []
    for name in formats.docling_input_format_names():
        member = getattr(InputFormat, name, None)
        if member is not None:
            allowed.append(member)

    format_options = {}
    if InputFormat.PDF in allowed:
        format_options[InputFormat.PDF] = PdfFormatOption(pipeline_options=pdf_opts)
    # Image pipeline reuses the OCR options so scanned figures/screenshots yield text.
    if InputFormat.IMAGE in allowed:
        format_options[InputFormat.IMAGE] = ImageFormatOption(pipeline_options=pdf_opts)

    return DocumentConverter(allowed_formats=allowed, format_options=format_options)


def _normalize_gif_to_png(path: Path) -> Path:
    """Flatten the first frame of a GIF to a temp PNG for the Docling IMAGE pipeline.

    Docling's image pipeline targets still raster formats — an (especially
    animated) GIF is not a first-class InputFormat. We OCR the first frame only;
    animation is dropped by design (documented in alldoc.md §3).
    """
    import os

    from PIL import Image

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="mosdac_gif_")
    tmp = Path(tmp_path)
    os.close(tmp_fd)
    try:
        with Image.open(path) as im:
            im.seek(0)  # first frame
            im.convert("RGB").save(tmp, "PNG")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("GIF normalize: %s → %s (first frame)", path.name, tmp.name)
    return tmp


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

# A contiguous run of Markdown table rows (lines that start/end with `|`). Treated
# as one atomic block so a size-based sub-split never severs a table mid-row.
_TABLE_BLOCK = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.MULTILINE)
_TABLE_PLACEHOLDER = "\x00TABLE{}\x00"
# Leading Markdown heading lines of a section — re-prepended to every sub-chunk
# so each piece keeps its section title as context after a size split.
_HEADING_LINES = re.compile(r"\A(?:\s*#{1,6}[^\n]*\n)+")


def _protect_blocks(text: str) -> tuple[str, list[str], list[str]]:
    """Hide math AND table blocks behind NUL placeholders before a size split."""
    text, math_stash = _protect_math(text)
    table_stash: list[str] = []

    def _swap(m: re.Match) -> str:
        table_stash.append(m.group(0))
        return _TABLE_PLACEHOLDER.format(len(table_stash) - 1)

    text = _TABLE_BLOCK.sub(_swap, text)
    return text, math_stash, table_stash


def _restore_blocks(text: str, math_stash: list[str], table_stash: list[str]) -> str:
    for i, tbl in enumerate(table_stash):
        text = text.replace(_TABLE_PLACEHOLDER.format(i), tbl)
    return _restore_math(text, math_stash)


def _subsplit_section(doc: Document, max_chars: int, overlap: int) -> list[Document]:
    """Sub-split an over-long section on paragraph/sentence boundaries, math/table-safe.

    Sections within ``max_chars`` are returned untouched (the common case). Larger
    sections are split so each piece fits the embedder's context window — without
    this, a 2–4 k-token section is embedded from only its first ~512 tokens and
    everything below is invisible to vector search. Every piece keeps the section
    heading prefix, and no ``$$…$$`` block or table row is ever cut.
    """
    content = doc.page_content
    if len(content) <= max_chars:
        return [doc]

    from langchain_text_splitters import RecursiveCharacterTextSplitter

    m = _HEADING_LINES.match(content)
    heading = m.group(0) if m else ""

    protected, math_stash, table_stash = _protect_blocks(content)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chars,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    pieces = [
        _restore_blocks(p, math_stash, table_stash) for p in splitter.split_text(protected)
    ]
    pieces = [p.strip() for p in pieces if p.strip()]
    if len(pieces) <= 1:
        return [doc]

    # Stable shared parent id so optional parent-section expansion can re-group
    # the children at retrieval time (graph_rag/retrieval/hybrid_retriever.py).
    import hashlib

    parent_id = hashlib.sha1(content[:512].encode("utf-8")).hexdigest()[:16]
    out: list[Document] = []
    for i, piece in enumerate(pieces):
        body = piece if (not heading or piece.startswith(heading.strip())) else f"{heading}{piece}"
        meta = dict(doc.metadata)
        meta["parent_id"] = parent_id
        meta["section_part"] = i
        out.append(Document(page_content=body, metadata=meta))
    return out


def chunk_markdown(markdown: str) -> list[Document]:
    """Split cleaned Markdown on section headers, then cap over-long sections.

    strip_headers=False keeps the heading in the chunk body so the model sees the
    section title as context when it reasons over the passage.

    Sections are first split on ``#/##/###`` headers (never mid-structure). A
    section that still exceeds ``settings.chunk_max_section_chars`` is then
    sub-split on paragraph/sentence boundaries by ``_subsplit_section`` — math and
    table blocks stay atomic — so long sections remain fully searchable instead of
    being silently truncated by the embedder. Set ``enable_section_subsplit=False``
    to restore the previous whole-section behaviour.

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
        docs = [Document(page_content=markdown)]
    docs = [d for d in docs if d.page_content.strip()]

    from graph_rag.config import settings as _s

    if not _s.enable_section_subsplit:
        return docs

    out: list[Document] = []
    for d in docs:
        out.extend(_subsplit_section(d, _s.chunk_max_section_chars, _s.chunk_overlap))
    return out


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

    Per-type metadata defaults (e.g. ``domain_type``) and the page-number policy
    come from the format registry keyed on ``source_type`` — no hard-coded
    ``if source_type == "pdf"`` branch, so a new format gets correct metadata by
    adding a registry row only.
    """
    from graph_rag.ingestion import formats
    from graph_rag.text_features import has_formula, has_table, numeric_density

    defaults = formats.metadata_defaults_for(source_type)
    keep_page_number = formats.preserves_page_number(source_type)

    enriched: list[Document] = []
    for idx, chunk in enumerate(chunks):
        meta = dict(chunk.metadata)      # copy — never mutate the splitter's dict
        body = chunk.page_content
        meta.update(
            {
                "source": str(path),
                "file_name": path.name,
                "source_type": source_type,
                "chunk_index": idx,
                "content_type": "markdown",
                # Signal to splitter.py: these are already correctly split.
                "pre_chunked": True,
                # Structure features — let retrieval bias toward formula/table/
                # quantitative chunks for the matching query type (text_features.py).
                "has_formula": has_formula(body),
                "has_table": has_table(body),
                "numeric_density": numeric_density(body),
            }
        )
        # Apply per-type defaults without clobbering anything the splitter set.
        for key, value in defaults.items():
            meta.setdefault(key, value)
        if keep_page_number:
            # Docling's markdown export does not surface a reliable per-chunk page
            # number after heading-based splitting. Preserve any value the splitter
            # already extracted; default to None so downstream code can branch on
            # its presence.
            meta.setdefault("page_number", chunk.metadata.get("page_number"))
        enriched.append(Document(page_content=chunk.page_content, metadata=meta))
    return enriched


# ── Public entry point ──────────────────────────────────────────────────────

def preprocess_file(path: str | Path) -> list[Document]:
    """Run the full multi-format pipeline on one Docling-parsable file → enriched chunks.

    Handles HTML, PDF, Office (DOCX/XLSX/PPTX/CSV/AsciiDoc) and images
    (incl. GIF) through one router driven by the format registry — there is no
    hard-coded ``if suffix == ".pdf"`` branch here.

    Steps:
      1. Route (registry): HTML → BeautifulSoup junk filter → temp file;
         GIF → first-frame PNG; everything else → pass through to Docling.
      2. Parse: Docling (OCR + tables + LaTeX) → one Markdown string.
      3. Clean: LaTeX-safe regex noise cleaner.
      4. Quality gate (formats with apply_quality_gate): reject garbage docs and
         drop individual junk chunks (alldoc.md §5). On a rejected document we
         return ``[]`` so it is NOT recorded in the manifest and can be retried
         after a threshold change.
      5. Chunk: MarkdownHeaderTextSplitter (#/##/###).
      6. Enrich: source-type-aware metadata on every chunk.

    Raises ValueError for file types the registry does not route through Docling
    (e.g. plain .txt/.md, or an unknown suffix). Temp files are always cleaned up.
    """
    from graph_rag.ingestion import formats
    from graph_rag.preprocessing.quality import assess_quality

    path = Path(path)
    spec = formats.get_spec(path.suffix.lower())
    if spec is None or spec.category == formats.CATEGORY_TEXT:
        raise ValueError(
            f"Unsupported file type for Docling preprocessing: {path.suffix!r}"
        )

    tmp_files: list[Path] = []
    try:
        if spec.category == formats.CATEGORY_HTML:
            parse_target = _clean_html_to_tempfile(path)  # Step 1: BS4 junk filter
            tmp_files.append(parse_target)
        elif spec.pre_normalize == "gif_to_png":
            parse_target = _normalize_gif_to_png(path)     # Step 1: GIF → PNG
            tmp_files.append(parse_target)
        else:
            parse_target = path

        markdown = _parse_to_markdown(parse_target)        # Step 2
        markdown = clean_markdown(markdown)                # Step 3

        if spec.apply_quality_gate:                        # Step 4
            passed, reason = assess_quality(markdown)
            if not passed:
                logger.warning("quality gate rejected %s: %s", path.name, reason)
                return []

        chunks = chunk_markdown(markdown)                  # Step 5

        if spec.apply_quality_gate:
            kept = [c for c in chunks if assess_quality(c.page_content)[0]]
            if not kept:
                logger.warning(
                    "quality gate rejected all %d chunk(s) of %s", len(chunks), path.name
                )
                return []
            chunks = kept

        return enrich_chunks(chunks, path, spec.source_type)  # Step 6

    finally:
        for tmp in tmp_files:
            tmp.unlink(missing_ok=True)


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
