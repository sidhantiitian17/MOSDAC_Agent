Plan: Hash-based incremental ingestion (skip already-ingested files)
Context
python main.py ingest currently re-processes every file in the source folders on every run. The flow is load_all_documents() → split_documents() → embed into Chroma → extract triples/measurements into Neo4j (graph_rag/ingestion/pipeline.py).

The two stores are already data-idempotent (Chroma dedups by chunk_id in chroma_store.py:45; Neo4j MERGEs on canonical keys), but they are not compute-idempotent: even when nothing changed, every run still pays for PDF parsing/OCR (loader.py), re-embedding, and — most expensive — one LLM extraction call per chunk (pipeline.py:126-168).

Goal: when ingestion has already been done and only a few new files appear, only those new files should be processed. We track each ingested file by a SHA-256 content hash in a manifest. During the recursive folder walk, a file whose hash is already in the manifest is skipped entirely (never loaded, embedded, or extracted); a file whose hash is absent is ingested.

Scope decisions (confirmed with user):

Changed files: a file edited in place gets a new hash → treated as new and re-ingested. Stale chunks/nodes from the old version are left in place (acceptable; no cleanup logic).
Crash safety: new hashes are committed to the manifest at the end of a successful full run (no stats.errors, neither --skip-vector nor --skip-graph). A mid-run crash records nothing, so new files are safely retried next run.
This reuses the content-hash-cache-pattern (~/.claude/.../content-hash-cache-pattern/SKILL.md): hash content not paths, chunked hashing for large files, corruption-tolerant cache reads, keep processing functions pure and add the cache as a separate layer. No new dependencies (hashlib, json, dataclasses are stdlib).

Changes
1. New module — graph_rag/ingestion/manifest.py
The single source of truth for "has this file been ingested?". Stored as one JSON file.

compute_file_hash(path: Path) -> str — SHA-256, streamed in 64 KB chunks (PDFs can be large; don't load whole file into memory). Mirrors the skill's compute_file_hash.
IngestionManifest dataclass wrapping { file_hash: {source, file_name, chunk_count, ingested_at} }:
load(path) -> IngestionManifest — read JSON; treat missing/corrupt as empty (graceful, never crash), matching the skill's "corruption returns None" guidance.
is_ingested(file_hash) -> bool
record(file_hash, *, source, file_name, chunk_count) — adds an entry with a UTC ingested_at timestamp.
save() — mkdir(parents=True) on the parent, write pretty JSON with a {"version": 1, "files": {...}} envelope.
Manifest JSON shape (synthetic example):

{
  "version": 1,
  "files": {
    "<sha256-hex>": {
      "source": "downloads/insat3d_handbook.pdf",
      "file_name": "insat3d_handbook.pdf",
      "chunk_count": 42,
      "ingested_at": "2026-06-11T09:30:00+00:00"
    }
  }
}
2. graph_rag/ingestion/loader.py — filter at discovery, tag with hash
Keep load_file() pure (no cache knowledge). Add filtering in the walk:

Add SUPPORTED_SUFFIXES = PDF_SUFFIXES | HTML_SUFFIXES | TEXT_SUFFIXES and extract the recursive walk into iter_source_files(folders) -> Iterator[Path] (yields supported files only).
Extend load_all_documents(folders=None, *, manifest=None, force=False):
For each discovered file: when manifest is not None and not force, compute its hash and continue (skip) if manifest.is_ingested(hash) — the core requirement.
Otherwise load it via the existing load_file(path), and tag every returned Document with d.metadata["file_hash"] = hash so the pipeline knows what to record later.
Log a clear summary: Loaded N documents from M new files (K files skipped — already ingested), distinguishing "all skipped" from "nothing found".
Backwards compatible: with the default manifest=None, behaviour is byte-for-byte identical to today (no hashing, no file_hash tag) — existing tests / main.py test unaffected.
3. graph_rag/ingestion/pipeline.py — wire in the manifest
IngestionPipeline.__init__ gains force: bool = False.
In run(): load the manifest (None when force), pass it to load_all_documents(...).
Replace the misleading "No documents found" early-return message so it also covers the "everything already ingested → nothing to do" case.
At the end of run(), gate on a complete, clean run — if manifest is not None and not stats.errors and not self.skip_vector and not self.skip_graph:
Build hash → {source, file_name, chunk_count} by aggregating over chunks (each chunk's metadata carries file_hash, source, file_name from steps 1–2 above).
manifest.record(...) each, then manifest.save(). Log how many files were recorded.
Rationale for the skip-flag gate: a --skip-graph run only built vectors; recording it would wrongly cause a later full run to skip building the graph. Record skipped files only when both stores were populated.
4. graph_rag/config.py — manifest location (from .env)
Add ingest_manifest_path: str = "./ingest_manifest.json" near the data-source settings (config.py:32-34). Configurable via INGEST_MANIFEST_PATH in .env, consistent with how everything else in this project is configured.

5. main.py — --force flag
cmd_ingest: parse --force and pass to IngestionPipeline(force=...). Update the module docstring's ingest line to document --force (re-ingest everything, ignore the manifest) alongside the existing --skip-vector / --skip-graph.
6. Housekeeping
.gitignore: add ingest_manifest.json (runtime state, like chroma_db/).
.env.example: document INGEST_MANIFEST_PATH=./ingest_manifest.json.
Files
File	Change
graph_rag/ingestion/manifest.py	new — hashing + manifest load/record/save
graph_rag/ingestion/loader.py	iter_source_files(), manifest-aware load_all_documents(), file_hash tagging
graph_rag/ingestion/pipeline.py	load manifest, pass to loader, record hashes at end of clean full run, force param
graph_rag/config.py	ingest_manifest_path setting
main.py	--force flag + docstring
.gitignore, .env.example	ignore + document the manifest
tests/test_manifest.py	new — unit tests (below)
Tests (tests/test_manifest.py, no external services)
Following the project's AAA style and the offline-friendly fixtures in tests/conftest.py:

compute_file_hash: same content (different paths) → same hash; one-byte change → different hash.
Manifest round-trip: record → save → load → is_ingested is True; unknown hash → False.
Corruption tolerance: write garbage to the manifest path → load returns an empty manifest (no raise).
Loader filtering (the heart of the feature): create temp .txt/.md files, pre-seed a manifest with one file's hash, call load_all_documents(folder, manifest=...), and assert the seeded file is absent from the result while new files are present and tagged with file_hash.
Backwards-compat: load_all_documents(folder) (no manifest) loads everything and adds no file_hash metadata.
Verification
python -m pytest tests/test_manifest.py -v — all green.
python -m pytest tests/test_embeddings.py tests/test_retrieval.py -v — confirm no regression (these skip gracefully if services are down).
End-to-end timing demo:
Delete any existing ingest_manifest.json; run python main.py ingest → full ingestion, manifest written with one entry per file.
Re-run python main.py ingest immediately → log shows K files skipped — already ingested, documents loaded : 0, and the run finishes in seconds (no LLM extraction).
Drop one new .md/.pdf into downloads/, run again → only that file is loaded/embedded/ extracted; the manifest gains exactly one entry.
python main.py ingest --force → ignores the manifest and re-ingests everything.
Sanity: python main.py test still passes (loader path unchanged when no manifest is involved).