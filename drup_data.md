# Drupal Ingestion — Bug Analysis, Fixes, and Pipeline Integration

## Observed Problems (from run log)

```
All 7 chunks already indexed.          ← ChromaDB (vector OK)
HTTP/1.1 200 OK  ×7                    ← 7 separate LLM calls for ONE article
NEW  7697caff…  GPS derived Integrated water vapour
Ingestion complete — scanned 1 | new 1
```

Two distinct bugs:
1. **N LLM calls per article** (7 for a single article) — wastes ~3 minutes per node.
2. **Junk data in the knowledge graph** — extracted triples are semantically wrong.

Plus a structural issue in the original `drupal_ingest.py`:
3. **KG creation is duplicated** — `push_to_graph_db` re-implements `llm_extractor`,
   `Neo4jStore`, `upsert_triples`, `upsert_chunks` directly, bypassing the full pipeline
   (no `quantity_parser`, no `_pick_anchor`, no `resolver`, no measurements). This means
   Drupal content gets an inferior, incomplete graph compared to file-based ingestion.

---

## Root Cause Analysis

### Bug 1 — Too many LLM calls

**Location:** `drupal_ingest.py → push_to_graph_db()`

```python
# CURRENT (broken): split first, then extract per chunk
chunks = split_documents([_to_document(parsed)])   # 7 chunks @ 800 chars each
for chunk in chunks:
    triples = extractor.extract(chunk.page_content, ...)  # 1 LLM call per chunk
```

`split_documents()` uses `settings.chunk_size` (800 chars). A ~5 000 char article
becomes 7 chunks → 7 LLM calls. The splitter is designed for retrieval precision;
for *extraction* the model needs full article context to link entities across paragraphs.

**Fix:** Extract from full article text (1 LLM call per article, ≤ `extraction_max_chars`),
splitting into chunks only for Chroma retrieval.

---

### Bug 2 — Junk knowledge graph

When the same article is split into 7 × 800-char fragments, the small model
(Qwen2-1.5B-Instruct) sees disconnected snippets with no document context and
hallucinates or trivialises triples. The signal-to-noise collapses at this granularity.

**Fix:** Document-level extraction (full text, 1 call) plus a confidence threshold.

---

### Structural Issue 3 — Duplicated KG code (bypasses full pipeline)

`push_to_graph_db` in the current `drupal_ingest.py` calls only:

- `llm_extractor.get_extractor()` + `extractor.extract()`
- `Neo4jStore.upsert_triples()` + `upsert_chunks()`

But `graph_rag/ingestion/pipeline.py → IngestionPipeline.run()` additionally calls:

- `quantity_parser.parse_quantities()` → quantitative Measurement nodes
- `_pick_anchor()` → anchors measurements to the right entity type
- `resolver.canonical_key()` → entity deduplication
- `neo4j.upsert_measurements()` → HAS_SPEC/HAS_UNIT edges

Drupal articles (e.g. about GPS water vapour) contain measurement specifications
("accuracy: ±2 mm", "resolution: 1 km") that the quantity parser would extract — but
only if the full pipeline runs. Bypassing it silently drops this data.

---

## Architecture — Correct Fix

The right fix is **not** to improve `push_to_graph_db` in isolation. It is to:

1. **Refactor `IngestionPipeline`** to expose a `run_on_documents()` method that
   accepts pre-loaded documents (skipping the file-loader step) and runs all
   remaining steps — split → vector → KG — through the exact same code path as
   file ingestion.

2. **Add `extract_at_document_level=True`** parameter so that for Drupal articles
   the LLM sees the full article text (1 call) rather than per-chunk fragments.

3. **Simplify `drupal_ingest.py`** to have zero KG code: remove `push_to_vector_db`
   and `push_to_graph_db` entirely and replace with a single call to
   `IngestionPipeline.run_on_documents()`.

```
File ingestion:
  IngestionPipeline.run()
    └─ load_all_documents()         (from disk)
    └─ _process_documents()
         ├─ split_documents()
         ├─ ChromaStore.add_documents()
         └─ _build_kg()             (per-chunk, existing)

Drupal ingestion (after fix):
  IngestionPipeline.run_on_documents([doc])
    └─ _process_documents()
         ├─ split_documents()
         ├─ ChromaStore.add_documents()
         └─ _build_kg_document_level()   (full-article, 1 LLM call, NEW)
               ├─ extractor.extract()    ← llm_extractor (same as file path)
               ├─ parse_quantities()     ← quantity_parser (same as file path)
               ├─ _pick_anchor()         ← same as file path
               ├─ neo4j.upsert_triples() ← Neo4jStore (same as file path)
               ├─ neo4j.upsert_measurements() ← same as file path
               └─ neo4j.upsert_chunks()  ← same as file path
```

---

## Implementation Plan

### Step 1 — Refactor `graph_rag/ingestion/pipeline.py`

#### 1a. Add parameters to `__init__`

```python
class IngestionPipeline:
    def __init__(
        self,
        folders: list[Path] | None = None,
        skip_vector: bool = False,
        skip_graph: bool = False,
        force: bool = False,
        extract_at_document_level: bool = False,  # NEW: True for Drupal
        kg_min_confidence: float = 0.0,           # NEW: filter low-quality triples
    ):
        ...
        self.extract_at_document_level = extract_at_document_level
        self.kg_min_confidence = kg_min_confidence
```

#### 1b. Extract `_process_documents()` from `run()`

Refactor `run()` to call a shared `_process_documents()` so both file and Drupal
ingestion execute identically from the split step onward:

```python
def run(self) -> IngestionStats:
    from graph_rag.ingestion.manifest import IngestionManifest
    from graph_rag.config import settings

    manifest = None if self.force else IngestionManifest.load(settings.ingest_manifest_path)
    documents = load_all_documents(self.folders, manifest=manifest, force=self.force)

    if not documents:
        # ... existing early-return logic ...
        return IngestionStats()

    stats = self._process_documents(documents)

    # Manifest update only for file ingestion (not Drupal — it has its own state).
    if manifest and not stats.errors and not self.skip_vector and not self.skip_graph:
        # ... existing manifest.record() loop + manifest.save() ...

    return stats

def run_on_documents(self, documents: list[Document]) -> IngestionStats:
    """Entry point for pre-loaded documents (Drupal). Skips file-loader + manifest."""
    if not documents:
        return IngestionStats()
    return self._process_documents(documents)

def _process_documents(self, documents: list[Document]) -> IngestionStats:
    """Steps 2-4 shared by both run() and run_on_documents()."""
    stats = IngestionStats()
    stats.documents_loaded = len(documents)

    chunks = split_documents(documents)
    stats.chunks_created = len(chunks)

    if not self.skip_vector:
        # ... same ChromaDB block as current run() Step 3/4 ...

    if not self.skip_graph:
        if self.extract_at_document_level:
            self._build_kg_document_level(chunks, stats)
        else:
            self._build_kg_chunk_level(chunks, stats)   # existing loop, unchanged

    return stats
```

#### 1c. Add `_build_kg_document_level()` — full-article extraction, 1 call per doc

```python
def _build_kg_document_level(self, chunks: list[Document], stats: IngestionStats) -> None:
    """KG extraction at document granularity — one LLM call per source document.

    Groups chunks by `source` metadata, concatenates their text, and calls the
    extractor once on the full document. Quantities and measurements are still
    processed per-chunk (they're deterministic regex, not LLM).
    All Neo4j writes go through the same upsert methods as file ingestion.
    """
    from graph_rag.config import settings
    from graph_rag.knowledge_graph.llm_extractor import get_extractor
    from graph_rag.knowledge_graph.neo4j_store import Neo4jStore
    from graph_rag.knowledge_graph.quantity_parser import parse_quantities
    from graph_rag.knowledge_graph.resolver import canonical_key

    extractor = get_extractor()
    stats.extraction_backend = type(extractor).__name__
    neo4j = Neo4jStore()
    neo4j.ensure_schema()

    # Group chunks by their source document.
    from collections import defaultdict
    by_source: dict[str, list[Document]] = defaultdict(list)
    for chunk in chunks:
        src = chunk.metadata.get("source", "")
        by_source[src].append(chunk)

    rels_total = 0
    meas_total = 0
    entity_keys: set[str] = set()

    try:
        for source, doc_chunks in by_source.items():
            # --- 1) Document-level triple extraction (1 LLM call) ---
            full_text = "\n\n".join(c.page_content for c in doc_chunks)
            snippet = full_text[: settings.extraction_max_chars]

            triples = extractor.extract(snippet, source_chunk_id="", source_path=source)

            # Quality gate: filter low-confidence + purely generic relations.
            if self.kg_min_confidence > 0.0:
                triples = [
                    t for t in triples
                    if t.confidence >= self.kg_min_confidence
                    and t.relation != "RELATED_TO"
                ]

            if triples:
                neo4j.upsert_triples(triples)
                rels_total += len(triples)
                for t in triples:
                    entity_keys.add(canonical_key(t.subject))
                    entity_keys.add(canonical_key(t.object_))

            # --- 2) Per-chunk quantities + measurements (same as file path) ---
            for chunk in doc_chunks:
                chunk_id = chunk.metadata.get("chunk_id", "")
                text = chunk.page_content
                quantities = parse_quantities(text)
                if quantities:
                    anchor = self._pick_anchor(triples, extractor, text)
                    if anchor is not None:
                        anchor_name, anchor_type = anchor
                        neo4j.upsert_measurements(
                            [
                                {
                                    "entity": anchor_name,
                                    "entity_type": anchor_type,
                                    "property": q.property_key,
                                    "value": q.value,
                                    "unit": q.unit,
                                    "raw": q.raw,
                                    "base_value": q.base_value,
                                    "base_unit": q.base_unit,
                                    "chunk_id": chunk_id,
                                    "source": source,
                                }
                                for q in quantities
                            ]
                        )
                        meas_total += len(quantities)
                        entity_keys.add(canonical_key(anchor_name))

        # --- 3) Provenance chunks (same as file path) ---
        chunk_records = [
            {
                "chunk_id": c.metadata["chunk_id"],
                "text": c.page_content,
                "source": c.metadata.get("source", ""),
            }
            for c in chunks
            if c.metadata.get("chunk_id")
        ]
        if chunk_records:
            neo4j.upsert_chunks(chunk_records)

        neo4j.close()
    except Exception as exc:
        if neo4j:
            neo4j.close()
        if isinstance(exc.__context__, KeyboardInterrupt):
            raise KeyboardInterrupt from exc.__context__
        logger.exception("Document-level KG build failed: %s", exc)
        stats.errors.append(f"graph: {exc}")
        return

    stats.entities_created = len(entity_keys)
    stats.relationships_created = rels_total
    stats.measurements_created = meas_total
```

#### 1d. Rename the existing KG loop to `_build_kg_chunk_level()`

Extract the existing `for chunk in tqdm(chunks)` block from `run()` into
`_build_kg_chunk_level(chunks, stats)`. Called by `_process_documents()` when
`extract_at_document_level=False` (default — existing file ingestion unchanged).

---

### Step 2 — Simplify `drupal_ingest.py`

Remove `push_to_vector_db` and `push_to_graph_db` entirely.
Replace with a single `ingest_node()` function that delegates to
`IngestionPipeline.run_on_documents()`:

```python
def ingest_node(
    parsed: ParsedNode,
    is_update: bool,
    skip_vector: bool = False,
    skip_graph: bool = False,
) -> IngestionStats:
    """Route one Drupal article through the full graph_rag pipeline."""
    from graph_rag.ingestion.pipeline import IngestionPipeline

    if is_update and not skip_vector:
        _delete_stale_vector_chunks(parsed.uuid)

    pipeline = IngestionPipeline(
        skip_vector=skip_vector,
        skip_graph=skip_graph,
        extract_at_document_level=True,                              # 1 LLM call per article
        kg_min_confidence=float(os.getenv("DRUPAL_KG_MIN_CONFIDENCE", "0.6")),
    )
    return pipeline.run_on_documents([_to_document(parsed)])


def _delete_stale_vector_chunks(uuid: str) -> None:
    """Remove old Chroma chunks for an UPDATED node before re-indexing."""
    from graph_rag.embeddings import get_embedder
    from graph_rag.vector_store.chroma_store import ChromaStore

    try:
        store = ChromaStore(embedder=get_embedder())
        raw = store._store._collection
        existing = raw.get(where={"drupal_uuid": uuid})
        if existing["ids"]:
            raw.delete(ids=existing["ids"])
            logger.debug("[vector] deleted %d stale chunks for uuid=%s", len(existing["ids"]), uuid)
    except Exception:
        logger.warning("[vector] could not delete old chunks for uuid=%s", uuid, exc_info=True)
```

The `run()` orchestrator becomes:

```python
def run(config: DrupalConfig) -> dict[str, int]:
    client = DrupalClient(config)
    state = StateManager(config.state_path)
    stats = {"scanned": 0, "new": 0, "updated": 0, "skipped": 0, "errors": 0}

    for node in client.iter_nodes():
        stats["scanned"] += 1
        try:
            parsed = parse_node(node)
        except Exception:
            stats["errors"] += 1
            logger.exception("Failed to parse node %s", node.get("id", "<unknown>"))
            continue

        verdict = state.verdict(parsed.uuid, parsed.content_hash)
        if verdict == "skip":
            stats["skipped"] += 1
            continue

        try:
            ingest_node(parsed, is_update=(verdict == "updated"))
        except Exception:
            stats["errors"] += 1
            logger.exception("Failed to ingest %s (%s)", parsed.uuid, parsed.title)
            continue

        state.record(parsed.uuid, parsed.content_hash)
        stats[verdict] += 1
        logger.info("%s  %s  %s", verdict.upper(), parsed.uuid, parsed.title)

    state.save()
    logger.info(
        "Ingestion complete — scanned %d | new %d | updated %d | skipped %d | errors %d",
        stats["scanned"], stats["new"], stats["updated"], stats["skipped"], stats["errors"],
    )
    return stats
```

---

### Step 3 — Wire into `main.py → cmd_ingest()`

```python
def cmd_ingest(argv: list[str] | None = None) -> int:
    """Run ingestion. Flags: --skip-vector, --skip-graph, --force, --skip-drupal"""
    import os
    from graph_rag.ingestion.pipeline import IngestionPipeline

    argv = argv or []

    # ── Step 1: file-based ingestion (HTML + PDF) — unchanged ────────────
    pipeline = IngestionPipeline(
        skip_vector="--skip-vector" in argv,
        skip_graph="--skip-graph" in argv,
        force="--force" in argv,
    )
    stats = pipeline.run()
    print(stats.summary())

    # ── Step 2: Drupal ingestion (auto when DRUPAL_JSONAPI_URL is set) ────
    drupal_url = os.getenv("DRUPAL_JSONAPI_URL", "").strip()
    skip_drupal = "--skip-drupal" in argv

    if drupal_url and not skip_drupal:
        print("\n── Drupal ingestion ──────────────────────────────────────")
        try:
            from drupal_ingest import DrupalConfig, run as drupal_run
            d_stats = drupal_run(DrupalConfig.from_env())
            print(
                f"Drupal: scanned {d_stats['scanned']} | "
                f"new {d_stats['new']} | updated {d_stats['updated']} | "
                f"skipped {d_stats['skipped']} | errors {d_stats['errors']}"
            )
        except Exception as exc:
            print(f"Drupal ingestion failed: {exc}")
    elif skip_drupal:
        print("\n(Drupal ingestion skipped via --skip-drupal)")
    else:
        print("\n(DRUPAL_JSONAPI_URL not set — Drupal ingestion skipped)")

    return 0 if not stats.errors else 1
```

Resulting CLI behaviour:

| Command | What runs |
|---|---|
| `python main.py ingest` | files + Drupal (if URL set) |
| `python main.py ingest --skip-drupal` | files only |
| `python main.py ingest --force` | re-ingest all files; Drupal uses its own hash state |
| `python main.py ingest --skip-graph` | files + Drupal, no KG writes in either |
| `python drupal_ingest.py` | Drupal standalone (unchanged) |

---

### Step 4 — New env vars

```dotenv
# Drupal → KG quality controls (optional — defaults shown)
DRUPAL_KG_MIN_CONFIDENCE=0.6   # drop triples below this confidence score
```

---

## Implementation Sequence

### Phase 1 — Refactor `pipeline.py` (no Drupal yet)

1. Add `extract_at_document_level` and `kg_min_confidence` params to `__init__`.
2. Extract the existing KG loop into `_build_kg_chunk_level(chunks, stats)`.
3. Add `_build_kg_document_level(chunks, stats)`.
4. Add `run_on_documents()` + `_process_documents()`.
5. Refactor `run()` to call `_process_documents()` (manifest logic stays in `run()`).
6. Verify existing file ingestion is unchanged:
   ```
   python main.py ingest --skip-drupal
   ```

### Phase 2 — Rewrite `drupal_ingest.py` (standalone test)

1. Remove `push_to_vector_db` and `push_to_graph_db`.
2. Add `ingest_node()` + `_delete_stale_vector_chunks()`.
3. Delete the state file and re-run:
   ```
   del drupal_ingestion_state.json
   python drupal_ingest.py
   ```
4. Verify: **exactly 1 LLM call** in the log, meaningful triples in Neo4j.

### Phase 3 — Integrate into `main.py` (only after Phase 2 passes)

1. Apply `cmd_ingest` changes to `main.py`.
2. Full test:
   ```
   python main.py ingest --skip-drupal        # files only, regression check
   python main.py ingest                      # files + Drupal together
   ```

---

## Summary of All Changes

| File | What changes | Why |
|---|---|---|
| `graph_rag/ingestion/pipeline.py` | Add `extract_at_document_level`, `kg_min_confidence` params | Control extraction granularity |
| `graph_rag/ingestion/pipeline.py` | Extract `_build_kg_chunk_level()` from `run()` | Reuse existing logic |
| `graph_rag/ingestion/pipeline.py` | Add `_build_kg_document_level()` | 1 LLM call + full pipeline (quantity_parser, measurements, resolver, etc.) |
| `graph_rag/ingestion/pipeline.py` | Add `run_on_documents()` + `_process_documents()` | Entry point for Drupal docs |
| `drupal_ingest.py` | Remove `push_to_vector_db`, `push_to_graph_db` | Eliminate duplicated KG code |
| `drupal_ingest.py` | Add `ingest_node()` calling `IngestionPipeline.run_on_documents()` | Route through full pipeline |
| `drupal_ingest.py` | Keep `_delete_stale_vector_chunks()` | Drupal-specific UPDATED handling |
| `main.py` | `cmd_ingest`: auto-call `drupal_run` when `DRUPAL_JSONAPI_URL` is set | Pipeline integration |
| `.env.example` | Add `DRUPAL_KG_MIN_CONFIDENCE=0.6` | Documentation |

```
