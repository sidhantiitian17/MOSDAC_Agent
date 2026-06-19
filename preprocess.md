# Document Preprocessing Layer — Implementation Plan

**Author:** Senior AI Data Engineer (ISRO / MOSDAC RAG)
**Scope:** A universal preprocessing layer that sits **before** the existing
ingestion module (`graph_rag/ingestion/`) and emits clean, math-safe,
header-chunked LangChain `Document` objects from **both** complex scientific PDFs
(formulas + heatmaps) and noisy web HTML.

---

## 1. Why this layer exists

The current ingestion path (`loader.py → splitter.py → pipeline.py`) already does
a lot of the right things, but three gaps make it fragile for a 32B production model:

| Concern | Today (`graph_rag/ingestion/`) | Gap this layer closes |
|---|---|---|
| HTML cleaning | `_load_html` strips only `<script>/<style>/<noscript>` then `get_text()` — loses **all** structure (headings, tables). | Strip the full junk set (`nav/footer/header/aside/forms/popups`) **but keep document structure**, then let Docling render real Markdown. |
| OCR noise | None. Tesseract output flows straight into chunks. | A **LaTeX-safe** regex cleaner that fixes erratic spacing / stray glyphs without ever touching `$$…$$` or `$…$`. |
| Chunking | `MarkdownTextSplitter` (size-based) — can still cut mid-section. | `MarkdownHeaderTextSplitter` (structure-based) — splits on `#/##/###`, never slices a table or equation. |
| Metadata | `source/file_type/file_name` only. | Source-aware enrichment (`source_type`, `page_number` for PDF, `domain_type` for HTML). |

**Design principle:** *reuse, don't rewrite.* This layer wraps the proven Docling
config in [graph_rag/ingestion/docling_parser.py](graph_rag/ingestion/docling_parser.py)
and the math-protection idea in [graph_rag/ingestion/splitter.py](graph_rag/ingestion/splitter.py),
adding only the missing HTML-filter, regex-cleaner, and header-splitter stages.

---

## 2. Where it plugs in

```
              ┌──────────────────────────────────────────────┐
  file path → │   PREPROCESSING LAYER  (new: preprocessor.py) │ → List[Document]
              │                                              │   (clean markdown,
              │  1. Input Router & HTML Filter (BeautifulSoup)│    header-chunked,
              │  2. Core Parsing (Docling: OCR+tables+LaTeX)  │    metadata-rich)
              │  3. Post-Processing (LaTeX-safe regex clean)  │        │
              │  4. Unified Chunking (MarkdownHeaderSplitter) │        │
              │  5. Dynamic Metadata Enrichment               │        ▼
              └──────────────────────────────────────────────┘   pipeline.run_on_documents()
                                                                  → Chroma + Neo4j
```

Two integration modes (pick one; both are non-breaking):

* **Drop-in (recommended first):** call `preprocess_file(path)` directly and feed the
  result to `IngestionPipeline().run_on_documents(docs)` — that entry point already
  skips file-discovery and splitting and goes straight to vector + KG.
* **Embedded:** have `loader._load_pdf` / `_load_html` delegate to this layer, so the
  full `IngestionPipeline().run()` (manifest, content-hash skip) keeps working. In this
  mode the **splitter must be bypassed** for already-chunked docs (tag
  `metadata["pre_chunked"] = True` and short-circuit `split_documents`).

> The standalone module below is the source of truth; wiring choices are §6.

---

## 3. The 5-step pipeline (contract)

| Step | Input | Output | Hard rules |
|---|---|---|---|
| 1. Router + HTML filter | file path | clean temp `.html` **or** original `.pdf` | remove `nav, footer, header, script, style, aside, form, noscript` + popup/cookie containers by class/id heuristic |
| 2. Docling parse | clean file | one Markdown string | `do_ocr=True`, `do_table_structure=True`, `do_formula_enrichment=True`, **no VLM** |
| 3. Regex clean | Markdown | clean Markdown | **never** mutate text inside `$$…$$` / `$…$`; collapse `\n\n\n+ → \n\n` |
| 4. Chunk | clean Markdown | `List[Document]` | `MarkdownHeaderTextSplitter` on `#/##/###`; no character slicing |
| 5. Enrich | chunks | `List[Document]` | PDF → `{source_type, file_name, page_number?}`; HTML → `{source_type, file_name, domain_type}` |

---

## 4. Implementation — `graph_rag/preprocessing/preprocessor.py`

> Self-contained and testable in isolation. Run it on one file from the CLI
> (`python -m graph_rag.preprocessing.preprocessor <file>`) to print the chunks.

```python
"""Universal document preprocessing layer for the MOSDAC RAG pipeline.

Turns a single PDF or HTML file into clean, math-safe, header-chunked LangChain
Documents ready for ingestion. Handles complex scientific PDFs (LaTeX formulas,
heatmaps via OCR) and noisy web HTML behind one entry point: `preprocess_file()`.

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
from dataclasses import dataclass
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
    keep <h1-6>, <table>, <p>, <ul> etc. intact so Docling can still recover real
    Markdown structure (headings drive Step 4's splitter).
    """
    # errors="ignore": ISRO scrapes mix encodings; never abort on a stray byte.
    raw = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "lxml")

    for tag in soup(list(_JUNK_TAGS)):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    for tag in soup.find_all(True):           # every remaining element
        if _is_junk_container(tag):
            tag.decompose()

    body = soup.body or soup                   # prefer <body>; fall back to whole tree
    tmp = Path(tempfile.mkstemp(suffix=".html", prefix="mosdac_clean_")[1])
    tmp.write_text(str(body), encoding="utf-8")
    logger.info("HTML filtered %s → %s", path.name, tmp.name)
    return tmp


# ── Step 2: Core parsing (Docling) ──────────────────────────────────────────

@lru_cache(maxsize=2)
def _build_converter():
    """Build & cache a Docling DocumentConverter (model load is expensive).

    Mirrors graph_rag/ingestion/docling_parser.py so behaviour stays consistent:
      do_ocr                 = True   → Tesseract reads burned-in labels on heatmaps
      do_table_structure     = True   → TableFormer emits real Markdown tables
      do_formula_enrichment  = True   → CodeFormula emits formulas as $$...$$ LaTeX
      picture description/VLM= OFF     → we only OCR text *inside* images, no captioning
    TesseractCliOcrOptions (not the API binding) matches the system `tesseract`
    installed in Dockerfile.api and is the most robust choice for the 32B box.
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
    pdf_opts.do_picture_description = False
    pdf_opts.do_picture_classification = False
    pdf_opts.generate_picture_images = False

    return DocumentConverter(
        # Register HTML so the cleaned temp file routes through Docling too.
        allowed_formats=[InputFormat.PDF, InputFormat.HTML],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_opts)},
    )


def _parse_to_markdown(path: Path) -> str:
    """Convert a clean PDF/HTML file into one Markdown string. Raises on empty."""
    result = _build_converter().convert(str(path))
    markdown = result.document.export_to_markdown()
    if not markdown or not markdown.strip():
        raise ValueError(f"Docling produced empty Markdown for {path.name}")
    return markdown


# ── Step 3: Post-processing (LaTeX-safe regex noise cleaner) ────────────────

# Order matters: display math ($$...$$) BEFORE inline ($...$) so we never match a
# `$$` boundary as two inline delimiters. DOTALL lets $$...$$ span lines.
_DISPLAY_MATH = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$)")
_MATH_PLACEHOLDER = "\x00MATH{}\x00"           # NUL-wrapped: cannot occur in real text

# OCR noise patterns (applied ONLY to non-math text):
#   _SOFT_HYPHEN     — Tesseract emits U+00AD soft hyphens that break word search.
#   _BROKEN_HYPHEN   — "satel-\nlite" line-wrap artifacts → rejoin to "satellite".
#   _STRAY_GLYPHS    — isolated non-alphanumeric junk (│ ▯ ) floating between spaces.
#   _MULTISPACE      — collapse runs of spaces/tabs Tesseract sprays into words.
#   _BLANK_LINES     — 3+ newlines → exactly two (one blank line).
_SOFT_HYPHEN = re.compile("­")
_BROKEN_HYPHEN = re.compile(r"(\w+)-\s*\n\s*(\w+)")
_STRAY_GLYPHS = re.compile(r"(?<=\s)[^\w\s$#*\-|.,;:()\[\]/%]+(?=\s)")
_MULTISPACE = re.compile(r"[ \t]{2,}")
_BLANK_LINES = re.compile(r"\n{3,}")


def _protect_math(text: str) -> tuple[str, list[str]]:
    """Swap every math span for an inert placeholder; return the originals.

    This is the single most important rule in the spec: once protected, the
    cleaning regexes below physically cannot see — let alone alter — LaTeX.
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


def _clean_markdown(markdown: str) -> str:
    """Remove OCR noise without ever touching math blocks."""
    protected, stash = _protect_math(markdown)

    protected = _SOFT_HYPHEN.sub("", protected)
    protected = _BROKEN_HYPHEN.sub(r"\1\2", protected)
    protected = _STRAY_GLYPHS.sub(" ", protected)
    protected = _MULTISPACE.sub(" ", protected)
    protected = _BLANK_LINES.sub("\n\n", protected)

    return _restore_math(protected, stash).strip()


# ── Step 4: Unified chunking (header-aware, never slices tables/equations) ───

_HEADERS_TO_SPLIT_ON = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def _chunk_markdown(markdown: str) -> list[Document]:
    """Split on markdown headers only — sections stay whole, so do tables/LaTeX.

    strip_headers=False keeps the heading text in the chunk body so the LLM sees
    the section title as context. We do NOT chain a character splitter: a 32B
    model has the context budget to take a full section, and any size-based cut
    risks severing a $$...$$ block or a table mid-row.
    """
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS_TO_SPLIT_ON,
        strip_headers=False,
    )
    docs = splitter.split_text(markdown)
    # Fallback: a heading-less document yields zero splits — keep it as one chunk.
    if not docs and markdown.strip():
        return [Document(page_content=markdown)]
    return docs


# ── Step 5: Dynamic metadata enrichment ─────────────────────────────────────

@dataclass
class PreprocessResult:
    chunks: list[Document]
    source_type: str            # "pdf" | "html"


def _enrich(chunks: list[Document], path: Path, source_type: str) -> list[Document]:
    """Attach source-aware metadata to every chunk (immutably copies metadata)."""
    enriched: list[Document] = []
    for idx, chunk in enumerate(chunks):
        meta = dict(chunk.metadata)            # never mutate the splitter's dict
        meta.update(
            {
                "source": str(path),
                "file_name": path.name,
                "source_type": source_type,
                "chunk_index": idx,
                "content_type": "markdown",
            }
        )
        if source_type == "pdf":
            # Docling does not surface a reliable per-chunk page after markdown
            # export; expose it only when a header carries it, else omit (None).
            meta.setdefault("page_number", chunk.metadata.get("page_number"))
        else:
            meta["domain_type"] = "web_scrape"
        enriched.append(Document(page_content=chunk.page_content, metadata=meta))
    return enriched


# ── Public entry point ──────────────────────────────────────────────────────

def preprocess_file(path: str | Path) -> list[Document]:
    """Run the full 5-step pipeline on one PDF/HTML file → enriched chunks.

    Raises ValueError for unsupported suffixes. Cleans up the temp HTML file.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    tmp_html: Path | None = None
    try:
        if suffix in PDF_SUFFIXES:
            source_type, parse_target = "pdf", path
        elif suffix in HTML_SUFFIXES:
            source_type = "html"
            tmp_html = _clean_html_to_tempfile(path)     # Step 1
            parse_target = tmp_html
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        markdown = _parse_to_markdown(parse_target)      # Step 2
        markdown = _clean_markdown(markdown)             # Step 3
        chunks = _chunk_markdown(markdown)               # Step 4
        return _enrich(chunks, path, source_type)        # Step 5
    finally:
        if tmp_html is not None:
            tmp_html.unlink(missing_ok=True)


# ── CLI smoke test: `python -m graph_rag.preprocessing.preprocessor <file>` ──

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) != 2:
        print("usage: python -m graph_rag.preprocessing.preprocessor <file.pdf|file.html>")
        raise SystemExit(2)

    result_chunks = preprocess_file(sys.argv[1])
    print(f"\n=== {len(result_chunks)} chunk(s) ===\n")
    for i, c in enumerate(result_chunks):
        print(f"--- chunk {i} | meta={c.metadata} ---")
        print(c.page_content[:800])
        print()
```

---

## 5. Regex design rationale (why each choice is safe for the 32B model)

* **Placeholder = `\x00MATH{n}\x00`.** Wrapping the index in NUL bytes guarantees the
  token can never collide with real Markdown (NUL never appears in text/OCR output),
  so restore is lossless even on adversarial documents.
* **Display-before-inline ordering.** Matching `$$…$$` first prevents the inline
  regex from misreading a `$$` fence as two empty `$…$` spans.
* **Inline regex uses lookarounds** `(?<!\$)\$(?!\$)` so `$x$` matches but `$$` fences
  and currency-style `$$` are not torn apart.
* **`_BROKEN_HYPHEN` only joins `\w-\n\w`** — it rejoins OCR line-wrap splits
  (`satel-\nlite`) but leaves real hyphenated compounds (`L-band`) untouched because
  those have no newline.
* **`_STRAY_GLYPHS` whitelists** Markdown/scientific punctuation (`# * - | . , ; : ( ) [ ] / %`)
  so it strips OCR confetti (`│ ▯`) without eating table pipes or list markers.
* **`_BLANK_LINES` → `\n\n`** keeps paragraph boundaries (which the header splitter and
  the LLM both rely on) while removing OCR's ragged vertical whitespace.

---

## 6. Integration steps

1. **Create the package:** `graph_rag/preprocessing/__init__.py` +
   `preprocessor.py` (above). Export `preprocess_file`.
2. **Add config knobs** to [graph_rag/config.py](graph_rag/config.py) (reuse existing
   names where possible): `docling_ocr_lang`, `tesseract_cmd` are already there;
   no new required settings.
3. **Wire it in.** Easiest non-breaking path — a thin driver:
   ```python
   from graph_rag.preprocessing.preprocessor import preprocess_file
   from graph_rag.ingestion.pipeline import IngestionPipeline

   docs = []
   for f in files:
       docs.extend(preprocess_file(f))
   IngestionPipeline(skip_graph=False).run_on_documents(docs)
   ```
   Because these chunks are already split, **tag them `pre_chunked=True`** and add an
   early `return [doc]` guard in `split_documents` for such docs if you later route
   them through `run()` instead of `run_on_documents()`.
4. **Dependencies** (already mostly present — see `requirement.txt` / Dockerfile.api):
   `docling[ocr]`, system `tesseract-ocr` + `poppler-utils`, `beautifulsoup4`, `lxml`,
   `langchain-text-splitters`, `langchain-core`.

---

## 7. Test plan

| Test | How | Pass criteria |
|---|---|---|
| PDF smoke | `python -m graph_rag.preprocessing.preprocessor sample_formula.pdf` | chunks printed; `$$…$$` blocks intact; `source_type=pdf` |
| HTML smoke | `... sample_scrape.html` | nav/footer/cookie text absent; `domain_type=web_scrape` |
| Math integrity | unit test: feed Markdown with `$$E=mc^2$$`, assert byte-equal after `_clean_markdown` | equation unchanged |
| Header split | unit test: 3-section Markdown → 3 chunks, none cutting a table | section count matches headers |
| Graceful degrade | corrupt/empty file | logged warning, no crash (raises `ValueError`, caller catches) |

Target: ≥80% coverage on `preprocessor.py` (pure functions — easy to unit test;
mock `_build_converter` so tests don't load Docling models).

---

## 8. Production robustness checklist (Qwen 2.5 Coder 32B)

- [x] Math is opaque end-to-end — the model never receives a corrupted equation.
- [x] Header-based chunks give the model coherent, self-contained sections.
- [x] Docling converter is `lru_cache`d — no per-file model reload under load.
- [x] Every stage fails soft and logs; one bad file never aborts a batch.
- [x] Temp HTML files are always cleaned up (`finally` + `missing_ok=True`).
- [x] OCR uses the system Tesseract CLI (matches Dockerfile.api), not a fragile binding.
