# MOSDAC GraphRAG — Code Reference (`document.md`)

A file-by-file reference of the MOSDAC GraphRAG chatbot: every module, the
classes and functions it defines, and how they fit together. The focus is the
`graph_rag/` package (ingestion → knowledge-graph construction → retrieval →
answer), with the surrounding service layers summarized at the end.

> **What changed in the knowledge-graph overhaul** (see `plan.md`): extraction
> is now LLM-driven and schema-guided, entities are canonicalized so variants
> collapse to one node, numeric specs become comparable `Measurement` nodes, and
> every fact is linked to its source passage. The extraction model is switchable
> from `.env` via `TABBY_EXTRACTION_MODEL`.

---

## 1. Architecture at a glance

```
INGEST  (python main.py ingest)
  loader → splitter → ┬─ embeddings → ChromaStore         (semantic vectors)
                      └─ get_extractor() ─ LLMExtractor / EntityRelationExtractor
                                 │  + quantity_parser
                                 ▼
                         resolver (canonical key) → Neo4jStore
                         (Entities, RELATION edges, Measurements, Chunk/Document provenance)

ASK     (chat)
  question → HybridRetriever ┬─ VectorRetriever + BM25Retriever → RRF fuse
                             └─ GraphRetriever → Neo4j paths + supporting chunks
                                 ▼
                         build_graph_rag_chain → Tabby LLM → grounded answer
```

---

## 2. Configuration

### `graph_rag/config.py`
Central settings loaded from `.env` via `pydantic-settings`.

**Class `Settings(BaseSettings)`** — every field maps to an env var (case-insensitive).
Key fields:
- **Neo4j**: `neo4j_uri`, `neo4j_username`, `neo4j_password`, `neo4j_database`.
- **ChromaDB**: `chroma_persist_dir`, `chroma_collection`.
- **Chunking**: `chunk_size` (800), `chunk_overlap` (100).
- **Retrieval**: `top_k_vector`, `top_k_graph`, `graph_depth` (2), `top_k_bm25`, `hybrid_rrf_k`.
- **LLM (Tabby ML)**: `tabby_base_url`, `tabby_api_token`, `tabby_model`.
- **Embeddings (Nomic)**: `nomic_model_name`, `nomic_base_url`, `nomic_api_token` (fall back to `TABBY_*`).
- **KG extraction (new)**:
  - `extraction_backend` — `"llm" | "spacy" | "auto"` (how triples are mined).
  - `tabby_extraction_model` — **the switchable model** (`TABBY_EXTRACTION_MODEL`); blank → reuse `tabby_model`.
  - `extraction_llm_base_url`, `extraction_llm_api_token` — default to `TABBY_*`.
  - `extraction_temperature` (0.0), `extraction_max_tokens` (2048), `extraction_max_chars` (6000).

**Method `extraction_model_name() -> str`** — returns `TABBY_EXTRACTION_MODEL` if set, else `TABBY_MODEL`. This single accessor is how every extraction call picks its model.

**Module global `settings`** — the singleton imported across the codebase.

> **Switching the extraction model:** set `TABBY_EXTRACTION_MODEL=<model-name>` in
> `.env`. No code change is needed; the next `ingest` uses it. Leave it blank to
> reuse the chat model. `EXTRACTION_BACKEND=spacy` forces the offline parser.

---

## 3. Ingestion (`graph_rag/ingestion/`)

### `loader.py` — discover & load source documents
Loads PDF / HTML / text from the configured folders, with a multi-tier PDF
recovery path (pypdf → PyMuPDF → OCR) so corrupt or image-only PDFs still yield text.
- `load_file(path) -> list[Document]` — dispatch by suffix; tags metadata (`source`, `file_type`, `file_name`).
- `load_all_documents(folders=None) -> list[Document]` — walk every folder, load every file.
- Internals: `_load_pdf`, `_load_pdf_pymupdf`, `_load_pdf_ocr`, `_ocr_via_pymupdf`, `_ocr_via_pdf2image`, `_load_html`, `_load_text`, `_has_fitz_format_errors`, `_mute_fitz_stderr`.

### `splitter.py` — chunk documents
- `split_documents(documents, chunk_size=None, chunk_overlap=None) -> list[Document]` — `RecursiveCharacterTextSplitter` with separators `["\n\n","\n",". "," ",""]`; assigns a stable `chunk_id` (SHA1 of source+index+prefix) and `chunk_index` to each piece.
- `_chunk_id(text, source, idx) -> str` — 16-char stable hash.

### `pipeline.py` — orchestrate end-to-end ingestion
**Dataclass `IngestionStats`** — counters: `documents_loaded`, `chunks_created`, `chunks_indexed`, `entities_created`, `relationships_created`, **`measurements_created`**, **`extraction_backend`**, `errors`. `summary()` renders a report.

**Class `IngestionPipeline`**
- `__init__(folders=None, skip_vector=False, skip_graph=False)`.
- `run() -> IngestionStats` — 4 steps:
  1. load documents, 2. split into chunks, 3. embed + store in ChromaDB,
  4. **build the knowledge graph**: `get_extractor()` → per chunk, extract typed triples (`upsert_triples`), mine specs with `parse_quantities` and attach `Measurement` nodes to an anchor entity (`upsert_measurements`), and record chunk text (`upsert_chunks`) for provenance.
- `_pick_anchor(triples, extractor, text) -> (name, type) | None` *(classmethod)* — chooses the entity a chunk's measurements belong to: first Satellite/Sensor/Instrument/Product/Mission subject, else first object of those types, else first subject, else a spaCy-detected entity. `_ANCHOR_PRIORITY` lists the preferred types.

---

## 4. Knowledge graph (`graph_rag/knowledge_graph/`)

### `ontology.py` — the controlled vocabulary *(new)*
Keeps the graph typed and consistent regardless of which extractor produced a triple.
- **`NODE_TYPES: set[str]`** — Mission, Satellite, Sensor, Instrument, Band, Channel, Product, Parameter, Algorithm, Unit, Measurement, Organization, Location, Event, Orbit, DataFormat, Application, Formula, Concept.
- **`RELATION_TYPES: set[str]`** — CARRIES, HAS_INSTRUMENT, HAS_BAND, HAS_CHANNEL, PRODUCES, MEASURES, HAS_SPEC, HAS_UNIT, LAUNCHED_BY, LAUNCHED_ON, OPERATED_BY, OPERATES_IN, PART_OF, DERIVED_FROM, USES, PROVIDES, LOCATED_IN, APPLIES_TO, RELATED_TO, MENTIONED_IN, PART_OF_DOCUMENT.
- **`VERB_TO_RELATION: dict`** — maps free-text verbs/phrases ("onboard", "launched by", …) onto canonical relations.
- **`TRIVIAL_RELATIONS: set`** — IS/ARE/HAS/… verbs that are dropped (they add no signal).
- `canonical_relation(verb_or_phrase) -> str | None` — map a verb to a canonical relation; `None` means "drop". Unknown-but-meaningful verbs are kept as a sanitized uppercase relation.
- `normalize_node_type(raw_type) -> str` — map a NER label / freeform type onto a `NODE_TYPES` member (default `Concept`).
- `is_trivial_relation(relation) -> bool`.

### `quantity_parser.py` — comparable technical specs *(new)*
Turns spec sentences into structured, unit-normalized facts (the key to math/comparison queries).
- **`PROPERTY_KEYWORDS: dict`** — keyword sets per property (spatial_resolution, temporal_resolution, swath_width, frequency, wavelength, altitude, inclination, spectral_channels, data_rate, spatial_coverage).
- **Dataclass `Quantity`** — `property_key, value, unit, raw, base_value, base_unit`; `as_dict()`. `raw` preserves the verbatim span (never paraphrase numbers); `base_value`/`base_unit` allow direct comparison.
- `parse_quantities(text) -> list[Quantity]` — per sentence, find each number+unit and attach it to the nearest preceding property keyword; dedupe.
- Internals: `_normalize_unit` (km/m/GHz/days/… → base m/Hz/s/deg/bps), `_parse_value` (handles `1.2 x 10^3`), `_property_for_span`.

### `resolver.py` — entity canonicalization *(new)*
Collapses surface variants onto one node so multi-hop chains connect.
- **`SEED_LEXICON: dict`** — curated alias → canonical display name (INSAT-3D, Oceansat-2, SCATSAT-1, …).
- `canonical_key(name) -> str` — case/space/hyphen-insensitive merge key (`"INSAT-3D"`, `"INSAT 3D"`, `"the INSAT-3D satellite"` → `"insat3d"`).
- `canonical_name(name) -> str` — clean display name (seed lexicon when known; strips determiners/trailing type words otherwise).
- **Dataclass `ResolvedEntity`** — `name, key, surface`; `resolve(name)` returns one.
- **Class `EntityResolver`** — optional embedding-based near-duplicate merger for an offline cleanup pass: `cluster(names, types=None) -> dict` (gated by similarity `threshold` and equal type), `_get_embedder`, `_cosine`. Not used in the hot path by default.

### `extractor.py` — spaCy SVO extractor (fallback)
- **Dataclass `Triple`** — `subject, subject_type, relation, object_, object_type, source_chunk_id, source_path, confidence`; `as_dict()`. The shared unit of KG storage.
- **Class `EntityRelationExtractor`**
  - `extract(text, source_chunk_id="", source_path="") -> list[Triple]` — spaCy dependency-parse SVO triples; relations now routed through `canonical_relation` (trivial verbs dropped), types via `normalize_node_type`. Falls back to regex when spaCy is absent.
  - `extract_entities(text) -> list[(name, type)]` — NER entities (used at query time and for anchor selection).
  - Internals: `_spacy_triples`, `_noun_span`, `_entity_type`, `_fallback`.
- `_load_spacy()`, `_sanitize_relation()` module helpers.

### `llm_extractor.py` — schema-guided LLM extraction (primary) *(new)*
- **Class `LLMExtractor`**
  - `__init__(model=None, base_url=None, api_token=None, temperature=None, max_tokens=None)` — defaults from `settings` (model = `extraction_model_name()`).
  - `extract(text, source_chunk_id="", source_path="") -> list[Triple]` — prompt the model with the ontology, parse strict JSON triples, validate/clamp to the controlled vocabulary, dedupe.
  - `_complete(messages, max_tokens=None) -> str` — streaming chat completion (Tabby requires streaming).
  - `_validate_row(row, chunk_id, path) -> Triple | None` *(staticmethod)* — drop self-loops/empties/trivial relations; normalize types; clamp confidence.
  - `extract_entities(text)` — delegates to spaCy NER (drop-in compatibility).
  - `property model`.
- `_all_balanced_objects(text) -> list[str]` — stack-based scan that recovers complete `{...}` objects even from **truncated** output (so a token-limited response still yields triples).
- `_extract_json(raw) -> dict | None` — strict parse, then salvage individual triple objects.
- `llm_extraction_available() -> bool` — cached one-shot reachability probe.
- `get_extractor()` — **factory** honoring `EXTRACTION_BACKEND`: returns `LLMExtractor` or `EntityRelationExtractor` (auto picks the LLM when reachable).

### `neo4j_store.py` — canonicalized, provenance-linked graph storage
**Class `Neo4jStore`** — Neo4j driver wrapper.
- `ensure_schema()` — unique constraint on `Entity.key`; indexes on entity name/type, `Chunk.chunk_id`, `Document.source`, `Measurement.key`/`property`; fulltext index on `Entity.name`.
- `upsert_triple(triple)` / `upsert_triples(triples, batch_size=200)` — MERGE entities **on canonical `key`** (variants collapse), set `name`/`type`/`aliases`, MERGE the `:RELATION {name, confidence}` edge, and create `:MENTIONED_IN`→`:Chunk`→`:PART_OF_DOCUMENT`→`:Document` provenance. `_triple_row` augments each triple with canonical keys/names.
- `upsert_chunks(chunks, batch_size=200)` — store `Chunk.text` + Document link (so facts can cite evidence). Each dict: `{chunk_id, text, source}`.
- `upsert_measurements(measurements, batch_size=200)` — create `:Measurement` nodes via `:HAS_SPEC`, with `:HAS_UNIT`→`:Unit` and `:MENTIONED_IN`→`:Chunk`. Each dict: `{entity, entity_type, property, value, unit, raw, base_value, base_unit, chunk_id, source}`.
- `query_neighbors(entity, depth=None, limit=50)` — semantic paths restricted to `:RELATION` edges (provenance excluded), shortest first; matches by name CONTAINS **or** canonical key.
- `entity_chunks(names, limit=5)` — return supporting passage text for entities (grounding).
- `fulltext_search(query, limit=10)` — Lucene fulltext on entity names with CONTAINS fallback. `_lucene_phrase` escapes the query.
- `schema_report()` — counts of `entities`, `relationships`, `measurements`, `chunks`.
- `ping()`, `clear()`, `close()`, context-manager `__enter__`/`__exit__`.

**Neo4j data model produced:**
| Element | Shape |
|---|---|
| Entity | `(:Entity {key, name, type, aliases[]})` |
| Semantic edge | `(:Entity)-[:RELATION {name, confidence, source_chunk_id, source_path}]->(:Entity)` |
| Measurement | `(:Entity)-[:HAS_SPEC]->(:Measurement {key, property, value, unit, raw, base_value, base_unit})-[:HAS_UNIT]->(:Unit)` |
| Provenance | `(:Entity|:Measurement)-[:MENTIONED_IN]->(:Chunk {chunk_id, text, source})-[:PART_OF_DOCUMENT]->(:Document {source})` |

---

## 5. Embeddings & vector store

### `embeddings/nomic_embedder.py`
- **Class `NomicEmbedder(Embeddings)`** — Nomic Embed Text via Tabby's OpenAI-compatible `/v1/embeddings`. `embed_documents`, `embed_query`, `_embed_batch`.
- `get_embedder()` — cached singleton, **provider-switchable from `.env`**. `EMBEDDING_PROVIDER=ollama` returns the Ollama backend; `=tabby` returns `NomicEmbedder`.

### `embeddings/ollama_embedder.py`
- **Class `OllamaEmbedder(Embeddings)`** — bge-large (or any Ollama embedding model, 1024-dim) via Ollama's native `/api/embeddings`. The endpoint comes only from `OLLAMA_BASE_URL` in `.env` (the `/api/embeddings` path is appended); no token needed. `embed_documents` (one request per text), `embed_query`, `_embed_one`.
- **Config:** `EMBEDDING_PROVIDER` (`ollama`|`tabby`), `OLLAMA_BASE_URL` (e.g. `http://localhost:11434`), `OLLAMA_EMBEDDING_MODEL` (e.g. `bge-large`).
- **Note:** switching the embedding model changes the vector dimension (bge-large = 1024). Re-run `python main.py ingest` to rebuild the Chroma collection — vectors from a different model/dimension are not query-compatible.

### `vector_store/chroma_store.py`
- **Class `ChromaStore`** — persistent ChromaDB wrapper. `add_documents` (dedupe by `chunk_id`, batch under the Rust 5461 cap), `similarity_search`, `similarity_search_with_score`, `count`, `reset`, `store` property.

---

## 6. Retrieval (`graph_rag/retrieval/`)

### `vector_retriever.py`
- **Dataclass `VectorHit`** — `text, source, score, chunk_id`.
- **Class `VectorRetriever`** — `retrieve(query, k=None) -> list[VectorHit]`, `as_context`.

### `bm25_retriever.py`
- **Class `BM25Retriever`** — BM25Okapi keyword search over the Chroma corpus; lazy `_build_index`, `retrieve(query, k=None) -> list[VectorHit]` (catches exact sensor IDs/numbers semantic search under-ranks).

### `graph_retriever.py`
- **Dataclass `GraphPath`** — `triples: list[(s,r,o)]`, `score`.
- **Class `GraphRetriever`**
  - `retrieve(query) -> list[GraphPath]` — query entities → fulltext match → `query_neighbors` → dedup'd triples.
  - `as_context(query) -> str` — serialize typed paths **and append the supporting passages** linked to the matched entities (grounding).
  - `_supporting_passages(query, limit=3)` — pull chunk text via `store.entity_chunks`.
  - `_query_entities(query)` — entities from the extractor, with an acronym/proper-noun regex fallback (`_ENTITY_RE`).

### `hybrid_retriever.py`
- **Class `HybridRetriever`** — merges semantic + keyword + graph.
  - `retrieve(query) -> {"vector_context", "graph_context"}` — vector & BM25 fused via `_rrf_fuse` (Reciprocal Rank Fusion), graph context assembled independently; each source degrades gracefully on failure.
  - `_rrf_fuse`, `_format_hits` (staticmethods); lazy `vector`/`graph`/`bm25` properties.

---

## 7. LLM clients & chain

### `llm/tabby_client.py`
- `get_llm(temperature=0.1, max_tokens=2048) -> ChatOpenAI` — cached Tabby chat client (streaming=True). Also: `qwen_client.py`, `longcat_client.py` alternative backends.

### `chain/graph_rag_chain.py`
- `build_graph_rag_chain(retriever=None, llm=None)` — LCEL chain: retrieve (graph+vector) → fill the system prompt (`prompts/system_prompt.txt`, with `{graph_context}`/`{vector_context}`) → Tabby LLM → string output.
- `_load_system_prompt()`, `HUMAN_TEMPLATE`, `_DEFAULT_SYSTEM_PROMPT`.

### `chat/chatbot.py`
- **Class `GraphRagChatbot`** — conversation wrapper over the chain with history management (`chat`, `reset`).

---

## 8. Entry point & service layers (summary)

- **`main.py`** — CLI: `cmd_ingest` (flags `--skip-vector`, `--skip-graph`), `cmd_chat` (REPL), `cmd_test` (health-check every layer), `main`.
- **`chat_api/`** — FastAPI gateway: `main.py` (app), `routes.py`, `service.py` (chain wiring), `session.py` (history backends), `models.py`, `config.py`.

---

## 9. Tests (`tests/`)

| File | Covers |
|---|---|
| `test_ontology.py` | relation mapping, trivial drop, type normalization |
| `test_quantity_parser.py` | spec extraction, unit normalization, comparability |
| `test_resolver.py` | variant→one-key collapse, seed lexicon, name preservation |
| `test_llm_extractor.py` | JSON parse/salvage, row validation, mocked extraction (offline) |
| `test_kg_integration.py` | **live Neo4j push**: canonical merge + typed relation + measurement + provenance (skips if Neo4j down) |
| `test_extractor.py`, `test_neo4j.py`, `test_pipeline.py` | spaCy extractor, store, pipeline smoke |
| `test_retrieval.py`, `test_chain.py`, `test_chatbot.py`, `test_chroma.py`, `test_embeddings.py`, `test_splitter.py`, `test_loader.py` | retrieval/chain/vector layers |
| `conftest.py` | `neo4j_available` / `nomic_available` fixtures, auto-skip when a service is down |

---

## 10. How to run & verify

```bash
# 1. Configure (.env) — copy .env.example, fill TABBY_API_TOKEN, Neo4j creds.
#    Optionally pick a stronger extraction model:
#    TABBY_EXTRACTION_MODEL=<model-name>      EXTRACTION_BACKEND=auto

# 2. Health check every component
python main.py test

# 3. Build the graph (load → split → embed → extract → push to Neo4j)
python main.py ingest                 # full
python main.py ingest --skip-vector   # graph only

# 4. Verify the push
#    python main.py test  prints schema = {entities, relationships, measurements, chunks}

# 5. Chat
python main.py chat

# 6. Tests (live Neo4j/Tabby tests auto-skip when unavailable)
python -m pytest -q
```

**Verified end-to-end** (synthetic doc, LLM backend on Tabby): one chunk pushed
4 entities, 5 typed relationships (CARRIES / HAS_INSTRUMENT / MEASURES / …),
3 unit-normalized measurements, and chunk/document provenance — confirming the
ingestion pipeline writes correctly to Neo4j.

> **Performance note:** LLM extraction is an *offline batch* step. On a small
> local model (Qwen2-1.5B on CPU) each chunk takes ~1.5–2.5 min; point
> `TABBY_EXTRACTION_MODEL` at a larger/GPU-served model for production-scale or
> higher-quality extraction, or set `EXTRACTION_BACKEND=spacy` for a fast,
> offline (lower-quality) build.

---

## 11. Reasoning & evaluation (Phases 0, 6, 7)

These layers improve *how the chatbot reasons* over the graph. The heavier
LLM-loop features are **opt-in via `.env`** so default latency is unchanged.

### Config flags (`config.py`)
| Flag | Default | Effect |
|---|---|---|
| `ENABLE_QUERY_DECOMPOSITION` | false | Split a question into sub-questions before retrieval (Phase 6) |
| `MAX_SUBQUESTIONS` | 4 | Cap on sub-questions |
| `GRAPH_RERANK` | true | Embedding rerank of graph paths vs. the question (Phase 6) |
| `TOP_K_PATHS` | 8 | Paths kept after rerank |
| `ENABLE_ITERATIVE_REASONING` | false | Bounded retrieve→reason→re-retrieve answer loop (Phase 7) |
| `MAX_REASONING_ITERATIONS` | 3 | Loop bound |
| `ENABLE_FAITHFULNESS_CHECK` | true | Final pass: verify numbers are grounded (Phase 7) |
| `ENABLE_COMMUNITY_SUMMARIES` | false | Use GraphRAG community overviews at query time (Phase 6) |
| `MAX_COMMUNITIES` / `COMMUNITY_MIN_DEGREE` | 50 / 3 | Community build bounds |

### Phase 6 — reasoning-aware retrieval
- **`graph_rag/retrieval/query_planner.py`** — `QueryPlanner.decompose(question) -> QueryPlan`
  (`sub_questions`, `anchors`, `multihop`) via the chat LLM; robust JSON parse with
  fallback to the original question. `GraphRetriever._plan_entities` uses it when
  `ENABLE_QUERY_DECOMPOSITION` is on.
- **`GraphRetriever._rerank_paths`** — embeds each candidate path and the question
  (Nomic) and ranks by cosine similarity, replacing the blunt length heuristic.
- **`graph_rag/knowledge_graph/community.py`** — `CommunitySummarizer.build()` finds
  hub entities (high `:RELATION` degree), LLM-summarizes each neighborhood, stores a
  `:Community {id,title,summary,members[]}` node + an embedding in the
  `graph_communities` Chroma collection. `community_search(query, k)` vector-searches
  those summaries for global/overview questions; `GraphRetriever._community_block`
  appends them to the graph context when `ENABLE_COMMUNITY_SUMMARIES` is on.

### Phase 7 — iterative reasoning loop
- **`graph_rag/chain/iterative_chain.py`** — `IterativeReasoner` (and
  `build_iterative_chain`). Same `invoke({"question","history"})` interface as the LCEL
  chain. Each pass lets the LLM answer or emit `NEED_MORE: <terms>`; on `NEED_MORE` it
  re-retrieves seeded by the new terms (bounded by `MAX_REASONING_ITERATIONS`), then a
  faithfulness self-check confirms every number is grounded. `GraphRagChatbot` selects
  it automatically when `ENABLE_ITERATIVE_REASONING` is on.
- **`prompts/system_prompt.txt`** gained a *MULTI-HOP & QUANTITATIVE REASONING* section:
  treat graph lines as typed directed facts, chain them step by step, and decide
  comparisons from normalized measurement values while quoting the raw value.

### Phase 0 — evaluation harness
- **`graph_rag/eval/harness.py`** — `EvalHarness` runs a question set through the
  chatbot and scores **retrieval hit-rate**, **faithfulness** (numbers grounded), and
  **correctness** (optional LLM-as-judge). Pure metric helpers
  (`retrieval_hit_rate`, `faithfulness_score`, `extract_numbers`) are unit-tested
  offline. `scorecard()` / `save_markdown()` render the results.
- **`tests/eval/multihop_questions.yaml`** — curated single / multihop / comparison
  questions with soft `expected_entities`/`expected_keywords` and a short `reference`.

### CLI
```bash
python main.py eval --limit 10 --no-judge   # scorecard → eval_results.md
python main.py build-communities --limit 30 # GraphRAG community summaries (offline)
```
Enable the reasoning features for a session by setting the flags in `.env`
(e.g. `ENABLE_ITERATIVE_REASONING=true`, `ENABLE_QUERY_DECOMPOSITION=true`).
