# `guardrails/retrieval/` — L2 Retrieval / Grounding Guard

The **second** checkpoint, run **after retrieval but before the LLM**. It answers one
question: *"Is there enough trustworthy, relevant evidence to answer at all?"* If not, the
turn is refused with "I don't have that information" — **before** spending the LLM call.
This is the system's primary anti-hallucination gate, and it builds the **citation
registry** the answer is allowed to cite from.

Invoked by [guardrails/pipeline.py](../pipeline.py) `check_retrieval_groundable()`.

---

## File-by-file

### [grounding_gate.py](grounding_gate.py) — the relevance floor + citation registry
Decides groundability and builds the per-turn list of citable sources.
- **`check_groundable(hits, min_score, min_passages)`** — passes only if the top hit clears
  `GUARD_RETRIEVAL_MIN_SCORE` and there are at least `GUARD_MIN_SUPPORTING_PASSAGES`
  relevant hits. Returns `(passes, top_score)`.
- **`build_registry_from_hits(hits, manifest_path, check_allowlist)`** — assembles a
  `CitationRegistry` mapping `[S1],[S2]…` IDs to allowed source chunks (filtered through the
  source allowlist). The L4 citation verifier later checks the model only cited these IDs.
- **Types:** `Citation`, `CitationRegistry`. Helper: `_hit_relevance`.
- **Depends on:** `guardrails.retrieval.source_allowlist`.

### [source_allowlist.py](source_allowlist.py) — only cite ingested sources
Enforces that a chunk may only be used/cited if its source file is in the **ingestion
manifest** (i.e. it was deliberately ingested) — so a stray or stale chunk can't sneak into
an answer. Caches the manifest; `invalidate_cache()` is called on `/reload`.
- **Functions:** `is_allowed(source)`, `_load_manifest`, `invalidate_cache`.
- **Reads:** `INGEST_MANIFEST_PATH` (written by [graph_rag/ingestion/manifest.py](../../graph_rag/ingestion/manifest.py)).

### [cypher_safe.py](cypher_safe.py) — Cypher / fulltext injection defence
Sanitizes entity names extracted from the user's query **before** they go into Neo4j
fulltext / Cypher queries, so a crafted query can't inject graph operations.
- **Used by:** [graph_rag/retrieval/graph_retriever.py](../../graph_rag/retrieval/graph_retriever.py).
- *(Begins with a UTF-8 BOM — keep encoding.)*

### [__init__.py](__init__.py)
Package marker (L2 retrieval guard modules).

---

## Why this gate is the anti-hallucination cornerstone

A plain RAG system will happily ask the LLM to "answer from these passages" even when the
passages are irrelevant — and the LLM then confabulates. By **refusing before generation**
when there isn't a relevant-enough, allowlisted source, this gate makes "I don't know" the
default for anything the corpus doesn't actually support. L4's grounding enforcement then
double-checks that what the LLM *did* say is backed by those same sources.

## Dependencies at a glance
- **Internal:** `guardrails.config` (thresholds), the ingestion manifest.
- **Consumed by:** `guardrails.pipeline` (L2) and `graph_rag.retrieval` (cypher-safe names).
