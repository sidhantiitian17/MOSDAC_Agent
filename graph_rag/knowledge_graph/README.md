# `graph_rag/knowledge_graph/` — Building & Storing the Knowledge Graph

This package is **Step 4 of ingestion**: it mines **typed facts** out of each chunk and
stores them as a graph in **Neo4j**. The graph is what makes this *Graph* RAG — it captures
**relationships** between entities (satellites, payloads, instruments, measurements) that
flat text passages can't express well, and it provides provenance so every fact links back
to its source chunk.

> Ingestion context: [readme_main.md §6](../../readme_main.md). The graph is queried at
> answer time by [../retrieval/graph_retriever.py](../retrieval/graph_retriever.py).

---

## What gets built

```
chunk text
   │
   ▼  llm_extractor.py (or extractor.py)      ──► typed triples  (Satellite)-[HAS_PAYLOAD]->(Payload)
   ▼  quantity_parser.py                      ──► measurements   "36 m"  → Measurement{value, unit, property}
   ▼  ontology.py                             ──► normalized node types + relation names
   ▼  resolver.py                             ──► canonical entity (collapse "INSAT 3D"/"INSAT-3D")
   ▼  neo4j_store.py                          ──► MERGE into Neo4j + Chunk provenance nodes
```

Node/relationship shape (simplified):
`(:Entity {name, type})-[:REL]->(:Entity)`, `(:Entity)-[:HAS_MEASUREMENT]->(:Measurement)`,
and `(:Chunk {chunk_id, source})` linked to the entities/facts it supports.

---

## File-by-file

### [llm_extractor.py](llm_extractor.py) — schema-guided LLM extraction (primary)
Asks the **extraction LLM** (Tabby) to emit typed `(subject, relation, object)` triples
constrained to the domain ontology — the richest graph. Robustly parses JSON from the LLM
output (`_extract_json`, `_all_balanced_objects`).
- **Key pieces:** `LLMExtractor`, `get_extractor()` (factory honouring
  `EXTRACTION_BACKEND` = `llm`/`spacy`/`auto`), `llm_extraction_available()`.
- **Depends on:** `config`, `knowledge_graph.extractor` (fallback + `Triple`),
  `knowledge_graph.ontology`, `llm.tabby_client`.

### [extractor.py](extractor.py) — spaCy fallback extraction (offline)
Subject-Verb-Object extraction via **spaCy** NER + dependency parsing — no LLM needed, so
it works fully offline and is the `auto` fallback when the LLM endpoint is down.
- **Key pieces:** `Triple` (the shared triple type), `EntityRelationExtractor`,
  `_load_spacy`, `_sanitize_relation`.
- **Depends on:** `knowledge_graph.ontology`, `spacy` (`en_core_web_sm`).

### [ontology.py](ontology.py) — the controlled vocabulary
The **MOSDAC domain ontology**: the canonical set of node types and relation names the
graph is allowed to use, plus normalization so messy extractions map onto clean labels.
Keeps the graph consistent and queryable.
- **Key functions:** `canonical_relation`, `normalize_node_type`, `is_trivial_relation`.
- **Used by:** both extractors.

### [quantity_parser.py](quantity_parser.py) — specs → Measurement nodes
Regex/unit-aware parser that turns unit-bearing specs ("36 m", "0.65 µm", "10-bit") into
**structured, comparable `Measurement` facts** — the thing users most often ask about.
- **Key pieces:** `Quantity`, `parse_quantities`, `_normalize_unit`, `_parse_value`,
  `_property_for_span`.
- **Used by:** `pipeline.py` during ingestion.

### [resolver.py](resolver.py) — entity canonicalization
Collapses surface variants of the same entity ("INSAT 3D", "INSAT-3D", "Insat3D") onto a
single canonical node using affix-stripping + embedding similarity, so the graph doesn't
fragment into near-duplicates.
- **Key pieces:** `canonical_key`, `canonical_name`, `resolve`, `EntityResolver`,
  `ResolvedEntity`.
- **Depends on:** `embeddings.get_embedder`.

### [neo4j_store.py](neo4j_store.py) — the Neo4j driver wrapper
The **only** module that talks to Neo4j. Idempotent (`MERGE` on canonical keys),
provenance-linked (Chunk nodes), with a tuned connection pool. Provides upserts for
triples, measurements, and chunks, plus the fulltext/entity lookups the graph retriever
uses.
- **Key class:** `Neo4jStore`.
- **Depends on:** `config`, `knowledge_graph.extractor` (`Triple`), `knowledge_graph.resolver`,
  `neo4j` driver (Bolt).
- **Used by:** `pipeline.py` (writes), [retrieval/graph_retriever.py](../retrieval/graph_retriever.py)
  (reads), [community.py](community.py), [graph_rag/health.py](../health.py).

### [community.py](community.py) — GraphRAG community summaries
Builds **GraphRAG-style community summaries** for global/overview questions ("give an
overview of ISRO ocean missions"): clusters the graph, summarizes each cluster with the
LLM, embeds the summaries, and serves them at query time.
- **Key pieces:** `Community`, `CommunitySummarizer.build(...)`, `community_search(...)`.
- **Built by:** `python main.py build-communities`. Toggle: `ENABLE_COMMUNITY_SUMMARIES`.
- **Depends on:** `config`, `embeddings`, `neo4j_store`, `resolver`, `llm.tabby_client`,
  `vector_store`.

### [__init__.py](__init__.py)
Re-exports `EntityRelationExtractor`, `Triple`, `Neo4jStore`.

---

## Dependencies at a glance
- **Internal:** `graph_rag.config`, `graph_rag.embeddings`, `graph_rag.llm`,
  `graph_rag.vector_store`.
- **External:** `neo4j` (Bolt driver), `spacy`.
- **External service:** Neo4j 5.18 (`NEO4J_URI`), Tabby ML (extraction LLM).
