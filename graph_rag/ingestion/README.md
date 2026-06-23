# `graph_rag/ingestion/` — Discover, Load & Split Documents

This package is **Step 1–2 of the ingestion pipeline**: it finds source files, parses
them into clean text (Markdown), splits them into overlapping chunks, and is the
top-level orchestrator that drives embedding (→ ChromaDB) and KG extraction (→ Neo4j).

Run it with `python main.py ingest` (CLI wiring in [main.py](../../main.py)).

> Pipeline context: [readme_main.md §6](../../readme_main.md). Heavy parsing/cleaning is
> delegated to [../preprocessing/](../preprocessing/).

---

## The flow this package implements

```
source folders (downloads/, atlases_pdfs/) + Drupal
        │
        ▼  loader.py + formats.py  (discover, parse, quality-gate)
   clean Markdown documents
        │
        ▼  splitter.py / preprocessing.chunk_markdown  (math/table-safe chunking)
   chunks with stable chunk_ids + text-feature metadata
        │
        ▼  pipeline.py  (orchestrate)
   ├─► embeddings → vector_store (ChromaDB)
   └─► knowledge_graph extraction → Neo4j
        │
        ▼  manifest.py  (record SHA-256 only after a clean run → incremental + crash-safe)
```

---

## File-by-file

### [pipeline.py](pipeline.py) — `IngestionPipeline` (the orchestrator)
**The heart of ingestion.** Loads documents, splits them, embeds into ChromaDB, extracts
the KG into Neo4j, and updates the manifest. Honours the `--skip-vector` / `--skip-graph`
/ `--force` flags.
- **Key types:** `IngestionStats` (scanned/new/updated/skipped/errors + `summary()`),
  `IngestionPipeline` with `run()` (file-based) and `run_on_documents()` (used by Drupal
  so both sources share **one** KG/vector code path).
- **Depends on:** `config`, `ingestion.loader`, `ingestion.splitter`, `ingestion.manifest`,
  `embeddings.get_embedder`, `vector_store` (Chroma), `knowledge_graph` (`get_extractor`,
  `Neo4jStore`, `quantity_parser`, `resolver`).
- **Used by:** [main.py](../../main.py) `cmd_ingest`, [drupal_ingest.py](../../drupal_ingest.py),
  [scripts/build_kg.py](../../scripts/build_kg.py).

### [loader.py](loader.py) — discover & parse files
Walks the source folders, decides how to parse each file, and returns LangChain
`Document` objects. Routes PDFs through **Docling** (primary) with a fallback cascade
(pypdf → PyMuPDF → OCR via PyMuPDF/pdf2image) for tricky files; handles HTML and text;
applies the manifest skip and the quality gate.
- **Key functions:** `load_all_documents`, `iter_source_files`, `load_file`,
  `_load_pdf`/`_load_pdf_pymupdf`/`_load_pdf_ocr`, `_load_html`, `_load_text`,
  `_load_via_docling`, `_docling_eligible`.
- **Depends on:** `config`, `ingestion.formats`, `ingestion.docling_parser`,
  `ingestion.manifest`, `preprocessing.preprocessor`.

### [formats.py](formats.py) — the format registry
A **central, extensible registry** of every ingestible file type — extension → parser,
size limits, metadata defaults, and per-family kill-switches (`INGEST_ENABLE_OFFICE`,
`INGEST_ENABLE_IMAGES`). Adding a new format is a registry entry, not scattered `if`s.
- **Key functions:** `get_spec`, `is_enabled`/`is_supported`, `supported_suffixes`,
  `docling_input_format_names`, `metadata_defaults_for`, `within_size_limit`.
- **Depends on:** `config`. **Used by:** `loader.py`, `preprocessing/`.

### [docling_parser.py](docling_parser.py) — structured PDF → Markdown
Builds and runs the Docling converter that extracts Markdown structure, **LaTeX formulas**
(`$$...$$`), and tables, with optional full-page OCR for raster atlases. Loads its ML
models from a **local, baked-in path** (`DOCLING_ARTIFACTS_PATH`) so parsing makes **zero
network calls** (air-gapped).
- **Key functions:** `parse_pdf_to_markdown`, `_build_converter`,
  `_should_force_full_page_ocr`.
- **Depends on:** `config`, `docling`. **Used by:** `loader.py`.

### [splitter.py](splitter.py) — chunking with stable ids
Splits documents into overlapping ~`CHUNK_SIZE` passages while **protecting math and
tables** (a formula/table is never cut), and assigns each chunk a **stable `chunk_id`** so
re-ingesting is idempotent.
- **Key functions:** `split_documents`, `_chunk_id`, `_protect_math`/`_restore_math`,
  `_split_one`.
- **Depends on:** `config`. **Used by:** `pipeline.py`.

### [manifest.py](manifest.py) — incremental ingestion
The **content-hash manifest** that makes ingestion incremental and crash-safe. Records the
SHA-256 of every successfully-ingested file; the manifest is written **only after a clean
run**, so a partial run is safely retried.
- **Key pieces:** `compute_file_hash`, `IngestionManifest` (load/has/record/save).
- **Path:** `INGEST_MANIFEST_PATH` (default `./ingest_manifest.json`).
- **Used by:** `loader.py`/`pipeline.py`, **and** the guardrail
  [source allowlist](../../guardrails/retrieval/source_allowlist.py) (only chunks from
  manifest-ingested files may be cited).

### [__init__.py](__init__.py)
Re-exports `load_all_documents`, `split_documents`, `IngestionPipeline`.

---

## Dependencies at a glance

- **Internal:** `graph_rag.config`, `graph_rag.preprocessing`, `graph_rag.embeddings`,
  `graph_rag.vector_store`, `graph_rag.knowledge_graph`.
- **External:** `docling`, `pypdf`, `pymupdf`, `pytesseract`, `pdf2image`, `Pillow`,
  `beautifulsoup4`/`lxml`, `unstructured`.
- **System binaries:** Tesseract + Poppler (for OCR).
