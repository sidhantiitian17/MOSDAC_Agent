# `graph_rag/preprocessing/` — Cleaning, Chunking & Quality

This package is the **document-cleaning layer** that sits between raw parsing and the rest
of ingestion. It converts files to clean Markdown, **rejects garbage extractions**, splits
sections into math/table-safe chunks, and tags each chunk with structural features.

It is called from [../ingestion/loader.py](../ingestion/loader.py); the output (clean
chunks) flows on to embedding and KG extraction.

> Pipeline context: [readme_main.md §6 (Step 2)](../../readme_main.md).

---

## What it produces

```
raw file ──► clean Markdown ──► quality gate ──► header-aware chunks ──► enriched chunks
            (Docling/HTML)      (drop junk)      (math/table-safe)       (has_formula,
                                                                          numeric_density,
                                                                          has_table tags)
```

---

## File-by-file

### [preprocessor.py](preprocessor.py) — the universal preprocessing layer
The main module. Parses a file to Markdown (Docling for PDFs/Office, cleaned HTML
otherwise), normalizes images, **protects math/table blocks**, cleans boilerplate, then
splits into header sections (sub-splitting overly long sections with overlap), and
enriches each chunk with text features.
- **Public functions:** `preprocess_file` (the entry point used by the loader),
  `clean_markdown`, `chunk_markdown`, `enrich_chunks`.
- **Internal helpers:** `_build_converter` (Docling, offline), `_clean_html_to_tempfile`,
  `_is_junk_container`, `_normalize_gif_to_png`, `_protect_math`/`_restore_math`,
  `_protect_blocks`/`_restore_blocks`, `_subsplit_section`.
- **Depends on:** `config`, `ingestion.formats`, `preprocessing.quality`
  (`assess_quality`), `text_features` (`has_formula`, `has_table`, `numeric_density`),
  `docling`, `beautifulsoup4`, `Pillow`.
- **Used by:** [ingestion/loader.py](../ingestion/loader.py).

### [quality.py](quality.py) — the garbage-data quality gate
A **deterministic** filter that drops low-signal extractions before they pollute the
stores: near-empty text, OCR gibberish (low alphanumeric ratio), degenerate repetition
("the the the…"), and replacement/control-char soup. All thresholds are config-driven so a
noisy corpus is tuned without code edits.
- **Public function:** `assess_quality(text) -> (ok, reason)`.
- **Knobs:** `INGEST_MIN_CHARS`, `INGEST_MIN_ALNUM_RATIO`, `INGEST_MIN_UNIQUE_TOKENS`,
  `INGEST_MAX_REPEAT_RATIO`, `INGEST_MAX_REPLACEMENT_RATIO`.
- **Depends on:** `config`. **Used by:** `preprocessor.py`.

### [__init__.py](__init__.py)
Re-exports `preprocess_file`; documents the layer's role.

---

## Why "math/table-safe" matters

The corpus is **scientific** — satellite specs, spectral bands, formulas. A naïve splitter
that cuts a chunk in the middle of `$$ \rho = ... $$` or a Markdown table would destroy the
exact information users ask about. So both this package and
[../ingestion/splitter.py](../ingestion/splitter.py) detect and **protect** those blocks,
keeping each formula/table whole and searchable. The `has_formula` / `numeric_density`
tags written here are later used by retrieval's **feature boost** to push numeric/formula
chunks up the ranking for quantitative questions.

## Dependencies at a glance
- **Internal:** `graph_rag.config`, `graph_rag.ingestion.formats`, `graph_rag.text_features`.
- **External:** `docling`, `beautifulsoup4`/`lxml`, `Pillow`.
