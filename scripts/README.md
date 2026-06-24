# `scripts/` — Operational & Utility Scripts

One-off and operational helpers that sit outside the main request/ingest paths. Run them
directly with `python scripts/<name>.py`.

---

## File-by-file

### [build_kg.py](build_kg.py) — rebuild the KG from existing Chroma chunks
Builds (or rebuilds) the **Neo4j knowledge graph** from chunks **already in ChromaDB** —
**without** re-running Docling/PDF parsing. Useful when you've changed the extraction model,
ontology, or quantity parser and want to regenerate the graph cheaply (the expensive PDF
parsing is reused from Chroma).
- **Entry point:** `main()`.
- **Depends on:** `graph_rag.embeddings`, `graph_rag.ingestion.pipeline`
  (`IngestionPipeline`, `IngestionStats`), `graph_rag.vector_store.ChromaStore`.
- **Use when:** `python scripts/build_kg.py` (compare with `python main.py ingest
  --skip-vector`, which re-parses from source).

### [loadtest.py](loadtest.py) — concurrency load test
A standalone concurrency load test for the chat API (production readiness, see
[production.md](../production.md) §4). Fires many concurrent `/chat` requests and reports
latency/throughput so you can validate the LLM concurrency cap and rate limiting under load.
- **Entry points:** `main()`, `_run`, `_one`.
- **Run:** `python scripts/loadtest.py` (see its `--help` for target URL / concurrency).

### [fix-windows-tabby-port.ps1](fix-windows-tabby-port.ps1) — Windows helper
A PowerShell helper for a Windows-specific Tabby port issue (port forwarding / WSL
networking). Windows-only, not part of the Python flow.

---

## Notes
- These scripts import the same `graph_rag` packages as the app, so run them from the repo
  root with the virtualenv active (or inside the `chat_api` container for the ones that need
  live Chroma/Neo4j/Ollama).
- `build_kg.py` writes to Neo4j and reads ChromaDB — it needs both services up and a
  populated Chroma collection.
