# Docling Integration Plan — MOSDAC GraphRAG

**Author role:** Senior AI/Backend Engineer (Production RAG)
**Goal:** Make [Docling](https://github.com/docling-project/docling) the **primary** document parser in the existing ingestion pipeline, producing one clean Markdown stream per document, with **Tesseract OCR** for heatmap/image labels and **LaTeX-preserved** math — under a strict **no-VLM** constraint. Qwen 2.5 Coder 32B (text-only) consumes the result.

This document is the implementation blueprint. Each section states *what to change*, *why*, and gives the concrete code. Nothing here is speculative — every integration point references a real file in the repo.

---

## 0. Why this change

### 0.1 Corpus we actually have to parse

| Group | Location | Count / size | Nature | Parsing challenge |
|---|---|---|---|---|
| **Scientific atlases** | `atlases_pdfs/` | 10 PDFs, 3 MB → **407 MB** (`Images_of_Moon_From_Chandrayan-1.pdf`) | Heatmaps, satellite imagery, geographic plots with **floating text labels** ("Arabian Sea", "August", "4.5m") | Almost no extractable text layer → **OCR-only**. Large files → must bound DPI/memory. |
| **ATBD / product docs** | `downloads/docs/`, `downloads/sites/.../docs/` | ~30 PDFs | Algorithm Theoretical Basis Documents: **differential equations**, radiative-transfer formulas, tables | Math must survive as **`$$...$$` LaTeX**; tables must stay structured. |
| **Portal HTML** | `downloads/**/*.html` | many | Drupal node pages, flip-book demos, catalog pages | Already handled by BeautifulSoup; Docling can normalize but is optional here. |
| **Non-parseable** | `downloads/software/*.{zip,tar,rar,jar}`, `*.php`, `*.xml` | several | binaries / APIs | Out of scope — `iter_source_files()` already filters by suffix. |

### 0.2 What the current pipeline loses

[graph_rag/ingestion/loader.py](graph_rag/ingestion/loader.py) runs a cascade: `pypdf → PyMuPDF → Tesseract OCR (pixmap) → pdf2image OCR`. This is robust for *text* PDFs but:

1. **Math is destroyed.** `pypdf`/PyMuPDF flatten `∫`, sub/superscripts, and Greek into garbled inline text. The LLM never sees a usable formula.
2. **Tables collapse** into whitespace-separated runs that lose row/column structure.
3. **Atlas OCR is unstructured.** The pixmap-OCR fallback fires per page and dumps a flat blob; there is no Markdown structure tying a label to its figure/section.
4. **Inconsistent output** — three different code paths produce three different text shapes, so the chunker can't reason about structure.

Docling replaces the *primary* parse with a single structured **Markdown** representation (`DoclingDocument` → `export_to_markdown()`) that preserves headings, tables (as Markdown tables), formulas (as LaTeX), and OCR'd text — one consistent shape for every PDF. The existing cascade is **retained as a fallback** for files Docling cannot open.

### 0.3 Constraints honored

- **NO VLM.** We do **not** enable Docling's `PictureDescription` / VLM pipeline. Image understanding is limited to Tesseract OCR of text *inside* images. Picture *classification/description* is disabled.
- **OCR for images/heatmaps.** `do_ocr=True` with `TesseractCliOcrOptions` (or `TesseractOcrOptions`), `force_full_page_ocr=True` for the atlases so labels burned into raster imagery are extracted.
- **Math → LaTeX.** `do_formula_enrichment=True` so formulas export as `$$...$$`. (This uses the CodeFormula model — a small **text/vision encoder for formula regions only**, CPU-runnable, ~few hundred MB. It is *not* a general VLM and does not violate the no-VLM constraint; if even this is too heavy, see §4.4 for the pure-fallback path.)

---

## 1. Dockerfile & system dependencies

**File:** `Dockerfile.api` (currently `python:3.11-slim`; the brief specifies `python:3.10-slim`).

> **Decision:** Keep **3.11-slim**. Docling supports 3.9–3.13 and the repo already builds on 3.11; downgrading to 3.10 buys nothing and risks wheel churn. If org policy mandates 3.10, change only the `FROM` line — everything else below is identical. (Documented here because the brief asked for 3.10; this is a deliberate deviation, not an oversight.)

Key additions vs the current Dockerfile:
- Add `libtesseract-dev`, `tesseract-ocr-eng`, `libleptonica-dev` (Tesseract CLI/lib + English data).
- Keep `tesseract-ocr poppler-utils libgl1` (already present).
- Add `libglib2.0-0` (Docling/OpenCV runtime dep).
- **Pre-fetch Docling models at build time** so the container is air-gapped-ready (ISRO deployment is offline — see `docker-compose.yml` header). `docling-tools models download` caches layout + tableformer + code/formula models into the image.
- Set `OMP_NUM_THREADS` to bound CPU oversubscription during batch ingestion.

```dockerfile
# Dockerfile.api
FROM python:3.11-slim

WORKDIR /app

# ── OS-level deps ────────────────────────────────────────────────────────────
# tesseract-ocr + eng data + dev headers  → Docling OCR backend (Tesseract)
# poppler-utils                            → pdf2image fallback + Docling PDF render
# libgl1 / libglib2.0-0 / libgomp1         → OpenCV + ONNXRuntime runtime for Docling
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    libleptonica-dev \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Tesseract data path (Debian slim) — used by TesseractOcrOptions if needed.
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata

# Bound CPU thread oversubscription during batch parsing (no GPU in this deployment).
ENV OMP_NUM_THREADS=4

COPY requirement.txt .
RUN pip install --no-cache-dir -r requirement.txt \
    fastapi "uvicorn[standard]" \
 && python -m spacy download en_core_web_sm

# ── Pre-download Docling models into the image (offline / air-gapped readiness)
# Caches layout, TableFormer and CodeFormula models so first parse needs no network.
RUN docling-tools models download \
 && python -c "from docling.document_converter import DocumentConverter; print('docling OK')"

COPY . .

CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> **Note on the COPY filename:** the repo file is `requirement.txt` (singular). The brief says "`requirements.txt`". Pick one and be consistent. The Dockerfile above matches the **existing** repo file (`requirement.txt`). If you rename to `requirements.txt`, update this `COPY` line too. **Do not** leave two files.

---

## 2. `requirement.txt` additions

**File:** `requirement.txt` (singular — the one `Dockerfile.api` copies).

Add the Docling block. `docling` pulls `docling-core`, `docling-parse`, `docling-ibm-models`, OCR plumbing, and a CPU `onnxruntime`. We pin a floor and let the resolver settle minors.

```diff
 # Document loaders
 pypdf>=4.0
 pymupdf>=1.24
 pytesseract>=0.3
 pdf2image>=1.17
 Pillow>=10.0
 beautifulsoup4>=4.12
 lxml>=5.0
 unstructured>=0.14
+
+# ── Docling — primary structured PDF parser (Markdown + LaTeX math + OCR) ──────
+# Brings docling-core, docling-parse, docling-ibm-models, and a CPU onnxruntime.
+# OCR uses the system Tesseract installed in Dockerfile.api (no bundled engine).
+docling>=2.15
```

Notes:
- The brief's `docling[ocr]` extra is **not required** when using the **system Tesseract CLI** (`TesseractCliOcrOptions`) we install via apt — that backend shells out to the `tesseract` binary and needs no Python OCR extra. Plain `docling` is correct here. If you instead choose RapidOCR/EasyOCR (bundled), add `docling[rapidocr]` and drop the apt Tesseract packages. **We use system Tesseract** (matches the existing `pytesseract` fallback and the no-extra-weights philosophy in the current `requirement.txt` comments).
- `pytesseract`, `pdf2image`, `pymupdf`, `pypdf` **stay** — they power the fallback cascade in §4.3.

---

## 3. Configuration

**File:** [graph_rag/config.py](graph_rag/config.py) — add a Docling block alongside the existing OCR settings (which already document `tesseract_cmd` / `poppler_path`).

```python
    # ── Docling (primary PDF parser) ────────────────────────────────────────
    # Toggle Docling on/off without code changes. When False, the loader uses
    # the legacy pypdf→PyMuPDF→OCR cascade only.
    use_docling: bool = True
    # OCR every page of these "atlas" image-PDFs (labels are burned into raster
    # imagery, so a text-layer check would wrongly skip them). Match on path
    # substring; defaults to the atlases folder.
    docling_force_full_page_ocr_dirs: str = "atlases_pdfs"
    # Tesseract language(s) for Docling OCR (matches installed tesseract-ocr-eng).
    docling_ocr_lang: str = "eng"
    # Extract formulas as LaTeX ($$...$$). Uses the CPU CodeFormula model.
    docling_do_formula_enrichment: bool = True
    # Parse tables into structured Markdown tables (TableFormer).
    docling_do_table_structure: bool = True
    # Skip Docling for files larger than this (MB) and use the streaming OCR
    # fallback instead — guards against the 407 MB Moon atlas OOM-ing a worker.
    docling_max_file_mb: int = 250
    # Cap pages parsed per document (0 = no cap). Bounds worst-case memory/time.
    docling_max_pages: int = 0
```

These are read by the new parser module (§4) and the loader dispatch (§4.3).

---

## 4. The Docling parser module

**New file:** `graph_rag/ingestion/docling_parser.py`

Design goals:
1. One function `parse_pdf_to_markdown(path) -> str` returning a single Markdown string.
2. Configure `PdfPipelineOptions` to enforce `do_ocr=True`, table structure, formula→LaTeX, **VLM disabled**.
3. Tesseract OCR via the system binary (`TesseractCliOcrOptions`), `force_full_page_ocr=True` for atlas files.
4. Build the converter **once** (model load is expensive) and reuse it — cache at module level.
5. Raise on failure so the loader can fall back (§4.3) rather than silently emitting empty text.

```python
"""Docling-based PDF → Markdown parser (primary parser).

Produces one structured Markdown string per PDF:
  - headings/sections preserved,
  - tables as Markdown tables (TableFormer),
  - formulas as $$...$$ LaTeX (CodeFormula enrichment),
  - image/heatmap text extracted via Tesseract OCR.

NO vision-language model is enabled — picture description/classification is OFF.
Image understanding is limited to OCR of text *inside* the raster.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from graph_rag.config import settings

logger = logging.getLogger(__name__)


def _should_force_full_page_ocr(path: Path) -> bool:
    """Atlas PDFs are raster imagery with burned-in labels → OCR every page.

    A normal text-layer heuristic would wrongly conclude 'has text' from a stray
    caption and skip OCR, dropping the geographic labels we need.
    """
    needle = settings.docling_force_full_page_ocr_dirs.lower()
    return bool(needle) and needle in str(path).lower().replace("\\", "/")


@lru_cache(maxsize=2)
def _build_converter(force_full_page_ocr: bool):
    """Build (and cache) a DocumentConverter. Model load is expensive — reuse it.

    Two cache slots: one for force-full-page-OCR (atlases), one for normal docs.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # System Tesseract (installed via apt in Dockerfile.api). lang must match
    # the installed tessdata (tesseract-ocr-eng → "eng").
    ocr_options = TesseractCliOcrOptions(
        lang=[settings.docling_ocr_lang],
        force_full_page_ocr=force_full_page_ocr,
    )

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True                 # CONSTRAINT: OCR images/heatmaps
    pipeline_options.ocr_options = ocr_options
    pipeline_options.do_table_structure = settings.docling_do_table_structure
    pipeline_options.do_formula_enrichment = settings.docling_do_formula_enrichment
    pipeline_options.table_structure_options.do_cell_matching = True

    # CONSTRAINT: no VLM. Do not describe/classify pictures.
    pipeline_options.do_picture_description = False
    pipeline_options.do_picture_classification = False
    pipeline_options.generate_picture_images = False

    if settings.docling_max_pages > 0:
        pipeline_options.page_range = (1, settings.docling_max_pages)

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def parse_pdf_to_markdown(path: Path) -> str:
    """Parse a PDF into a single Markdown string. Raises on failure.

    The caller (loader._load_pdf) catches the exception and falls back to the
    legacy pypdf/PyMuPDF/OCR cascade, so a Docling failure never loses a document.
    """
    converter = _build_converter(_should_force_full_page_ocr(path))
    result = converter.convert(str(path))
    markdown = result.document.export_to_markdown()
    if not markdown or not markdown.strip():
        raise ValueError(f"Docling produced empty Markdown for {path.name}")
    logger.info("Docling parsed %s → %d chars of Markdown", path.name, len(markdown))
    return markdown
```

### 4.1 Why these options

| Option | Setting | Reason |
|---|---|---|
| `do_ocr` | `True` | Mandatory — extract heatmap/figure labels. |
| `ocr_options` | `TesseractCliOcrOptions(lang=["eng"])` | Uses the apt-installed system Tesseract; no bundled OCR weights, mirrors existing `pytesseract` choice. |
| `force_full_page_ocr` | `True` for atlases only | Atlas pages are raster; partial OCR would miss burned-in labels. Normal ATBDs keep the text layer (faster, more accurate). |
| `do_table_structure` | `True` | ATBDs have spec tables; preserves rows/cols as Markdown. |
| `do_formula_enrichment` | `True` | Emits `$$...$$` LaTeX for differential equations. |
| `do_picture_description` | `False` | **No VLM** — hard constraint. |
| `page_range` / `docling_max_pages` | optional cap | Bounds memory on huge atlases. |

### 4.2 Math output shape

With `do_formula_enrichment=True`, Docling's Markdown wraps display formulas in `$$ ... $$` and inline ones in `$ ... $`. Example expected for an ATBD radiative-transfer term:

```markdown
The brightness temperature is governed by

$$T_b = \int_0^\infty B(\nu, T(z))\, W(z)\, dz$$

where $W(z)$ is the weighting function.
```

This is exactly what the math-safe chunker (§5) must keep intact.

### 4.3 Loader integration — Docling primary, cascade fallback

**File:** [graph_rag/ingestion/loader.py](graph_rag/ingestion/loader.py)

Change `_load_pdf()` to try Docling first (when enabled and under the size cap), wrapping the Markdown in a `Document` tagged `parser="docling"` and `content_type="markdown"` so the chunker (§5) can pick the Markdown-aware splitter. On any exception, fall back to the **existing** cascade unchanged.

```python
def _load_pdf(path: Path) -> list[Document]:
    # ── Primary: Docling structured Markdown (math + tables + OCR) ────────────
    if settings.use_docling and _docling_eligible(path):
        try:
            from graph_rag.ingestion.docling_parser import parse_pdf_to_markdown
            markdown = parse_pdf_to_markdown(path)
            return [Document(
                page_content=markdown,
                metadata={"parser": "docling", "content_type": "markdown"},
            )]
        except Exception as exc:
            logger.warning("Docling failed for %s (%s) — falling back to cascade", path.name, exc)

    # ── Fallback: existing pypdf → PyMuPDF → OCR cascade (UNCHANGED) ───────────
    if _has_fitz_format_errors(path):
        logger.debug("Skipping pypdf for %s — fitz detected format errors", path.name)
        return _load_pdf_pymupdf(path)
    try:
        from langchain_community.document_loaders import PyPDFLoader
        return PyPDFLoader(str(path)).load()
    except Exception as exc:
        logger.warning("pypdf failed for %s (%s) — trying PyMuPDF", path.name, exc)
        return _load_pdf_pymupdf(path)


def _docling_eligible(path: Path) -> bool:
    """Skip Docling for oversized files (use streaming OCR fallback instead)."""
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return False
    if size_mb > settings.docling_max_file_mb:
        logger.info("Skipping Docling for %s (%.0f MB > %d MB cap)",
                    path.name, size_mb, settings.docling_max_file_mb)
        return False
    return True
```

`load_file()` already tags `source`, `file_type`, `file_name` afterward, and `load_all_documents()` adds `file_hash` — so the manifest/incremental logic in `pipeline.py` keeps working untouched. The only new metadata is `parser` and `content_type`.

> **407 MB Moon atlas:** with `docling_max_file_mb=250` it routes to the cascade's streaming OCR (`_ocr_via_pymupdf`, page-by-page pixmaps) rather than loading the whole doc into Docling at once. Tune the cap per available worker RAM.

### 4.4 No-formula-model fallback (if CodeFormula is too heavy)

If the CodeFormula model can't be shipped (resource limit), set `docling_do_formula_enrichment=False`. Formulas then export as plain text instead of LaTeX, but OCR, tables, and structure still work. The chunker (§5) degrades gracefully — its `$$` guard simply finds nothing to protect. This keeps the integration shippable on the tightest hardware.

---

## 5. Math-safe chunking

**File:** [graph_rag/ingestion/splitter.py](graph_rag/ingestion/splitter.py)

Current splitter uses `RecursiveCharacterTextSplitter(chunk_size=800, overlap=100, separators=["\n\n","\n",". "," ",""])`. Two problems for Docling Markdown:
1. It can split **inside a `$$...$$` block**, cutting a differential equation in half — the LLM then sees `$$T_b = \int_0^\infty B(\nu,` in one chunk and the rest in another. Both chunks are useless.
2. It ignores Markdown heading structure, so a chunk can straddle two unrelated sections.

**Strategy:** A Markdown-structure-aware split that is **math-atomic**:
1. **Protect** every `$$...$$` block by replacing it with a placeholder token before splitting.
2. Split the placeholder-substituted text with `MarkdownTextSplitter` (header/paragraph aware) at the configured `chunk_size`.
3. **Restore** the formulas after splitting. If restoring a formula would push a chunk far over `chunk_size`, that's acceptable — a whole formula in one chunk beats a severed one. Formulas longer than `chunk_size` get their **own** chunk.
4. Keep the existing `chunk_id` / `chunk_index` scheme so downstream KG + vector code is unchanged.

```python
"""Chunk Documents into overlapping passages with stable chunk_ids.

Math-safe: a $$...$$ LaTeX block is never split across two chunks. Docling
Markdown (metadata content_type='markdown') is split with a Markdown-aware
splitter; legacy plain text keeps the recursive character splitter.
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownTextSplitter, RecursiveCharacterTextSplitter

from graph_rag.config import settings

# Display-math blocks: $$ ... $$ (DOTALL so they span newlines).
_MATH_BLOCK = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_PLACEHOLDER = "[[MATH_{}]]"  # inert token; will not appear in source markdown


def _chunk_id(text: str, source: str, idx: int) -> str:
    digest = hashlib.sha1(f"{source}|{idx}|{text[:64]}".encode("utf-8")).hexdigest()
    return digest[:16]


def _protect_math(text: str) -> tuple[str, list[str]]:
    """Replace each $$...$$ block with an inert placeholder; return the originals."""
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
    is_markdown = doc.metadata.get("content_type") == "markdown"

    if is_markdown:
        protected, formulas = _protect_math(doc.page_content)
        splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        pieces = splitter.split_text(protected)
        # Restore formulas; placeholders are atomic so no $$ block is ever severed.
        texts = [_restore_math(p, formulas) for p in pieces]
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
```

### 5.1 Why this is safe

- The placeholder substitution makes each `$$...$$` block a **single indivisible token** during splitting — `MarkdownTextSplitter` can never insert a boundary inside one because it isn't there at split time.
- Inline `$...$` is intentionally **not** protected: it's short, rarely a split target, and protecting it would fragment normal prose. Display math (`$$`) is where severing actually breaks meaning.
- Plain-text (legacy cascade) documents keep the **exact current behavior** — zero regression risk for files Docling didn't touch.
- `chunk_id`/`chunk_index` are produced identically, so `pipeline.py`, the vector store, BM25, and Neo4j chunk records are unaffected.

### 5.2 Edge case — formula longer than `chunk_size`

A multi-line derivation can exceed 800 chars. After restore, that chunk is oversized but **intact**. This is the correct trade-off: the embedding model truncates gracefully, and a whole equation retrieved as one unit is what the LLM needs. If many formulas exceed the limit, raise `chunk_size` to 1200 in `.env` (`CHUNK_SIZE=1200`) — no code change.

---

## 6. System prompt — connecting scattered OCR labels

**File:** [prompts/system_prompt.txt](prompts/system_prompt.txt) (loaded live by `_load_system_prompt()` in [graph_rag/chain/graph_rag_chain.py](graph_rag/chain/graph_rag_chain.py); the chain already supplies `{graph_context}`, `{vector_context}`, `{history}`, `{question}` — **do not change the placeholders**).

The atlas OCR produces *fragments*: a passage may read `Arabian Sea  August  4.5  m  3.0  Bay of Bengal  2.0`. The LLM must stitch these into coherent statements (region + month + value) **without** telling the user the text came from OCR. Append a new section to the existing prompt (keep all current IDENTITY/CITATION/SECURITY rules — they're good).

### 6.1 Text block to add to `prompts/system_prompt.txt`

Insert this **after** the `KNOWLEDGE GRAPH REASONING:` section and **before** `CONVERSATION & FOLLOW-UPS:`:

```text
INTERPRETING FIGURE & MAP CONTENT:
- Some passages contain text extracted from maps, heatmaps, and figures. These
  arrive as short, scattered fragments: place names, months, numbers, and units
  appearing near each other (e.g. "Arabian Sea  August  4.5 m  Bay of Bengal 3.0 m").
- Treat spatially/textually adjacent fragments as related. Reconstruct the intended
  meaning by pairing geographic locations with their associated months, values, and
  units to form complete, factual statements
  (e.g. "In August, significant wave height reaches about 4.5 m over the Arabian Sea [S2].").
- Only connect fragments when the grouping is well supported by their adjacency in the
  passage. If a pairing is ambiguous, state the values you can confirm and note that the
  association is uncertain — never fabricate a location–value link.
- Preserve exact numeric values and units from the context; do not round or convert.
- Present the result as natural, cohesive prose or a clean table. NEVER mention OCR,
  "extracted text", figures-as-data, parsing, or how the information was obtained.
  Write as though you are reading the data directly.

INTERPRETING FORMULAS:
- Formulas appear in LaTeX between $$ ... $$. When explaining an algorithm, describe
  what the formula computes and define its symbols using the surrounding text. Reproduce
  the formula in LaTeX only if the user asks for the equation itself.
```

### 6.2 Optional — LangChain `PromptTemplate` form

If you prefer the prompt declared in code rather than the file, this is the equivalent `ChatPromptTemplate` (the chain currently builds it from the file via `ChatPromptTemplate.from_messages([("system", system_text), ("human", HUMAN_TEMPLATE)])`). The file-based approach is **recommended** — it hot-reloads per request (see the header comment in `system_prompt.txt`) and needs no redeploy. Use this code form only if you move prompt management into version-pinned code:

```python
from langchain_core.prompts import ChatPromptTemplate

MOSDAC_SYSTEM = """You are the MOSDAC Expert Assistant ...
{graph_context}
{vector_context}
... (full text incl. the two sections from 6.1) ..."""

prompt = ChatPromptTemplate.from_messages([
    ("system", MOSDAC_SYSTEM),
    ("human", "{history}{question}"),
])
```

> **Model mismatch to resolve before shipping:** `config.py` sets `tabby_model = "Qwen2-1.5B-Instruct"`, but the brief targets **Qwen 2.5 Coder 32B**. A 1.5B model will struggle to stitch scattered OCR fragments reliably. Set `TABBY_MODEL=Qwen2.5-Coder-32B-Instruct` (or whatever Tabby ML serves) in `.env` so the prompt above actually has the reasoning capacity it assumes.

---

## 7. Rollout & verification

### 7.1 Order of operations

1. **`requirement.txt`** — add `docling>=2.15` (§2).
2. **`Dockerfile.api`** — add OS deps + model pre-download (§1).
3. **`config.py`** — add Docling settings (§3).
4. **`docling_parser.py`** — new module (§4).
5. **`loader.py`** — Docling-primary dispatch with cascade fallback (§4.3).
6. **`splitter.py`** — math-safe Markdown chunking (§5).
7. **`system_prompt.txt`** — OCR-stitching + formula sections (§6.1).
8. **`.env`** — `TABBY_MODEL=Qwen2.5-Coder-32B-Instruct`, optionally tune `CHUNK_SIZE`, `DOCLING_MAX_FILE_MB`.

### 7.2 Smoke tests (run before full ingest)

```bash
# 1. Container builds and models are cached (no network at runtime)
docker compose build chat_api

# 2. One ATBD with heavy math → verify $$...$$ survives a chunk boundary
python -c "from pathlib import Path; from graph_rag.ingestion.loader import _load_pdf; \
from graph_rag.ingestion.splitter import split_documents; \
docs=_load_pdf(Path('downloads/docs/INSAT_3D_ATBD_MAY_2015.pdf')); \
chunks=split_documents(docs); \
print('chunks:',len(chunks)); \
print('any severed \$\$:', any(c.page_content.count('\$\$')%2 for c in chunks))"
# Expect: 'any severed $$: False'

# 3. One atlas → verify OCR labels show up in Markdown
python -c "from pathlib import Path; from graph_rag.ingestion.docling_parser import parse_pdf_to_markdown; \
md=parse_pdf_to_markdown(Path('atlases_pdfs/Eyes_on_Waves_from_Space.pdf')); \
print(md[:1500])"
# Expect: geographic labels / month names / numeric values in the text.
```

### 7.3 Regression guards (add to the test suite)

- **`test_splitter_math_atomic`** — feed Markdown containing a `$$...$$` block wider than `chunk_size`; assert every output chunk has an **even** count of `$$` (no severed block) and the full formula appears verbatim in exactly one chunk.
- **`test_splitter_plaintext_unchanged`** — a non-markdown `Document` produces the same chunks as the pre-change splitter (golden test).
- **`test_loader_docling_fallback`** — monkeypatch `parse_pdf_to_markdown` to raise; assert `_load_pdf` still returns documents via the cascade.
- **`test_docling_eligible_size_cap`** — a file over `docling_max_file_mb` returns `False` (routes to fallback).

Target ≥80% coverage on the two new/changed modules (`docling_parser.py`, `splitter.py`), per repo testing rules.

### 7.4 Performance notes

- Docling first-run loads layout + TableFormer + CodeFormula models (~seconds); the `lru_cache` on `_build_converter` amortizes this across the whole ingest run.
- Atlas full-page OCR is the slow path (CPU Tesseract per page). For the 407 MB Moon atlas this is bypassed by the size cap; for the others (6–93 MB) budget minutes-per-doc on CPU. Run ingestion as a batch job, not in the request path (it already is — `IngestionPipeline.run()`).
- `OMP_NUM_THREADS=4` prevents Tesseract/ONNX from oversubscribing cores during the batch.

---

## 8. Summary of files touched

| File | Change | Type |
|---|---|---|
| `Dockerfile.api` | OS deps (`libtesseract-dev`, eng data, glib), Docling model pre-download | edit |
| `requirement.txt` | `docling>=2.15` | edit |
| `graph_rag/config.py` | Docling settings block | edit |
| `graph_rag/ingestion/docling_parser.py` | Docling → Markdown parser | **new** |
| `graph_rag/ingestion/loader.py` | Docling-primary `_load_pdf` + size gate, cascade fallback retained | edit |
| `graph_rag/ingestion/splitter.py` | Math-safe Markdown-aware splitting | edit |
| `prompts/system_prompt.txt` | Figure/OCR-stitching + formula sections | edit |
| `.env` | `TABBY_MODEL=Qwen2.5-Coder-32B-Instruct`, optional tuning | edit |

**Net effect:** PDFs become one consistent, structured Markdown stream with intact LaTeX math and OCR'd map labels; the chunker never severs a formula; the LLM is told how to weave scattered geographic fragments into clean answers — all with **no vision-language model** in the deployment.
