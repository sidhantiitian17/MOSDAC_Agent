# Plan — Improving Knowledge Graph Creation for Better Reasoning & Multi-Hop QA

**Goal:** Re-engineer the knowledge-graph construction strategy so the MOSDAC chatbot reasons better — especially across multiple hops and on **technical / mathematical queries** (sensor specs, resolutions, swath/orbit math, parameter comparisons, formula-driven answers).

**Scope:** This plan targets the *graph creation* side (ingestion → extraction → storage → graph retrieval). Vector/BM25 retrieval stays, but is upgraded to interlock with the graph.

---

## 1. Where we are today

| Layer | File | Current behaviour | Limitation for reasoning |
|-------|------|-------------------|--------------------------|
| Extraction | `graph_rag/knowledge_graph/extractor.py` | spaCy NER + SVO dependency parse → `Triple(subject, relation=VERB_LEMMA, object)` | Relations are raw verbs (`IS`, `HAS`, `USE`) — no semantics. Entity types collapse to `CONCEPT`. **Numbers/units/specs are dropped.** |
| Storage | `graph_rag/knowledge_graph/neo4j_store.py` | Flat `(:Entity)-[:RELATION {name}]->(:Entity)`, `MERGE` on exact `name` | No canonicalization → same real entity becomes many nodes → graph fragments → multi-hop paths break. No ontology/typed edges. |
| Graph retrieval | `graph_rag/retrieval/graph_retriever.py` | Query entities → fulltext match → `[*1..2]` undirected neighborhood, `LIMIT 50` | No path-to-question scoring, no decomposition, no directed reasoning. Context is noisy. |
| Chain | `graph_rag/chain/graph_rag_chain.py` | Single-shot: retrieve(graph+vector) → LLM | No iterative multi-hop loop; one retrieval pass only. |
| Provenance | — | `Triple` carries `source_chunk_id` but no `Chunk` node exists | Graph facts can't pull their supporting passage → weak grounding. |
| Eval | — | none | Can't measure whether multi-hop actually improved. |

**Root causes of weak multi-hop / technical reasoning**
1. **Low-information edges** — verb-lemma relations don't encode domain meaning.
2. **Entity fragmentation** — no alias resolution / canonical IDs.
3. **No quantitative layer** — specs, units, and equations never enter the graph.
4. **No ontology** — flat model can't express `Satellite→Sensor→Band→Product→Parameter`.
5. **Retrieval is not reasoning-aware** — neighborhood dump, not guided traversal.
6. **No graph↔passage linkage** — facts and their evidence live apart.

---

## 2. Target architecture (what "good" looks like)

```
            ┌─────────────────────────── Ingestion ───────────────────────────┐
 documents → load → split → ┬→ embed → Chroma (passages)                       │
                            └→ LLM schema-guided extraction → canonicalize →    │
                               Neo4j (typed ontology + quantitative facts +     │
                               :Chunk provenance + :Document)                   │
            └──────────────────────────────────────────────────────────────────┘

            ┌─────────────────────────── Query time ──────────────────────────┐
 question → decompose (sub-questions) → entity link to canonical nodes →        │
            guided multi-hop Cypher (typed, directed) + community summaries →    │
            graph facts + linked passages → RRF fuse w/ vector/BM25 →           │
            iterative reasoner (re-retrieve if gaps) → grounded answer           │
            └──────────────────────────────────────────────────────────────────┘
```

Five pillars:
1. **Domain ontology** (typed nodes & relations).
2. **LLM schema-guided extraction** with a **quantitative fact** layer.
3. **Entity resolution / canonicalization**.
4. **Provenance linking** (`:Chunk`, `:Document` nodes) so graph facts are grounded.
5. **Reasoning-aware retrieval** (decomposition + guided multi-hop + iterative loop).

---

## 3. Phased implementation

### Phase 0 — Evaluation harness first (so we can measure every later change)
*Why first:* without a multi-hop test set we can't tell if changes help.

- Create `tests/eval/multihop_questions.yaml` — ~30–50 curated Q/A pairs covering:
  - single-fact technical (e.g. *"What is INSAT-3D's IR channel spatial resolution?"*),
  - **multi-hop** (e.g. *"Which sensors on Oceansat-2 measure sea-surface parameters, and at what resolution?"*),
  - **comparison / math** (e.g. *"How does Scatterometer swath compare between Oceansat-2 and SCATSAT-1?"*).
- Add `graph_rag/eval/harness.py` — runs each question through the chain, scores with: retrieval hit-rate (does the gold passage/triple get retrieved), answer correctness (LLM-as-judge using `get_llm()`), and faithfulness (is every number grounded in context).
- Output a scorecard table to compare before/after. **Capture a baseline now.**

**Deliverable:** baseline scores committed to `eval_baseline.md`.

---

### Phase 1 — Domain ontology & richer schema

Define an explicit MOSDAC ontology instead of flat `Entity/RELATION`.

- New `graph_rag/knowledge_graph/ontology.py`:
  - **Node types:** `Mission`, `Satellite`, `Sensor`, `Band`/`Channel`, `Product`, `Parameter`, `Algorithm`, `Unit`, `Measurement`, `Organization`, `Location`, `Event`, `Document`, `Chunk`.
  - **Relation types (typed, directed):** `CARRIES` (Satellite→Sensor), `HAS_BAND` (Sensor→Band), `PRODUCES` (Sensor/Algorithm→Product), `MEASURES` (Product/Sensor→Parameter), `HAS_SPEC` (→Measurement), `HAS_UNIT` (Measurement→Unit), `LAUNCHED_BY`, `OPERATES_IN`, `DERIVED_FROM`, `MENTIONED_IN` (→Chunk).
  - A controlled relation vocabulary + a mapping from free-text verbs → canonical relations (so legacy verb edges normalize, e.g. `onboard`/`has sensor`/`carries` → `CARRIES`).
- Update `neo4j_store.py`:
  - Store the node's ontology type as a **Neo4j label** (e.g. `:Satellite`) in addition to `:Entity`, and the relation's canonical type as the **relationship type** (`-[:CARRIES]->`) instead of everything being `:RELATION {name}`. Typed relationships make directed multi-hop Cypher both correct and fast.
  - Extend `ensure_schema()` with indexes per key label and a uniqueness constraint on canonical id.

*Why this helps:* directed, typed edges let the retriever ask precise multi-hop questions ("Satellite-CARRIES->Sensor-MEASURES->Parameter") instead of walking an untyped blob.

---

### Phase 2 — LLM schema-guided extraction (replace shallow SVO)

Keep spaCy as a cheap fallback, but make **LLM extraction the primary path**.

- New `graph_rag/knowledge_graph/llm_extractor.py`:
  - Prompt the existing `get_llm()` (Tabby/Qwen) with the ontology from Phase 1 and ask for **strict JSON** triples: `{subject, subject_type, relation, object, object_type, qualifiers, confidence}`.
  - Use **few-shot examples drawn from MOSDAC text** (satellite/sensor/parameter sentences) so the model emits domain-correct types and canonical relations.
  - Validate output with a Pydantic schema; drop/repair malformed rows; clamp to the controlled relation vocabulary.
  - Batch chunks and cache by `chunk_id` (extraction is the expensive step — make it idempotent and resumable).
- Keep `EntityRelationExtractor` (spaCy) as fallback when the LLM endpoint is down, but route its verb relations through the Phase-1 verb→canonical mapping so even fallback edges are typed.
- Wire into `graph_rag/ingestion/pipeline.py` step 4 (swap `extractor.extract(...)` for the new extractor, with a config flag `extraction_backend = llm | spacy`).

*Why this helps:* the LLM captures relationships spaCy misses (implicit, cross-clause, table-derived) and assigns correct domain types — directly improving the density and correctness of the graph that multi-hop reasoning walks over.

---

### Phase 3 — Quantitative / mathematical fact layer (the key for technical & math queries)

This is what makes *numbers* reasoning-able.

- Extend the extractor to emit **Measurement facts**: `(:Sensor)-[:HAS_SPEC]->(:Measurement {property:"spatial_resolution", value:1.0, unit:"km", raw:"1 km", chunk_id})`.
  - Add a `graph_rag/knowledge_graph/quantity_parser.py` that normalizes values + units (use `pint` for unit handling) so `1 km`, `1000 m`, `1.0km` unify and become **comparable**.
  - Capture property keys the system prompt already cares about: spatial resolution, temporal resolution, swath width, frequency, wavelength, channel number, revisit time, data format, file size.
- Preserve `raw` strings verbatim (the system prompt forbids paraphrasing numbers — keep exact text for citation) while also storing normalized numeric value+unit for **computation/comparison**.
- For **equations/formulas** found in passages, store as `:Formula` nodes with the LaTeX/plain expression and `MENTIONED_IN` the chunk, linked to the `:Parameter` they compute.

*Why this helps:* comparison and math queries ("which has finer resolution", "compute revisit") can now be answered from structured, unit-normalized facts instead of hoping two numbers happen to land in the same retrieved chunk.

---

### Phase 4 — Entity resolution & canonicalization (fix graph fragmentation)

- New `graph_rag/knowledge_graph/resolver.py`:
  - **Alias normalization**: strip determiners ("the INSAT-3D satellite" → `INSAT-3D`), unify separators/casing (`INSAT 3D` ↔ `INSAT-3D`), maintain an alias table (`:Entity {name, canonical_id, aliases[]}`).
  - **Embedding-based dedupe**: reuse `get_embedder()` (Nomic) to merge near-duplicate entity names above a similarity threshold, gated by same ontology type.
  - Maintain a curated **seed lexicon** of known MOSDAC satellites/sensors/products to anchor canonical ids (high precision).
- Change `neo4j_store.upsert_*` to `MERGE` on `canonical_id` (not raw `name`), keeping `aliases` as a property so fulltext still matches surface forms.

*Why this helps:* multi-hop only works if a chain passes *through* shared nodes. Canonicalization is the single highest-leverage fix for connecting otherwise-orphaned subgraphs.

---

### Phase 5 — Provenance: link graph facts to passages

- During ingestion, create `(:Chunk {chunk_id, source})` and `(:Document {source})` nodes, and connect every extracted entity/measurement via `-[:MENTIONED_IN]->(:Chunk)-[:PART_OF]->(:Document)`.
- `GraphRetriever` returns, alongside each triple, the **chunk text** of its supporting passage (one extra Cypher hop).

*Why this helps:* the LLM gets the *fact* (from the graph) **and** its *evidence sentence* (from the linked chunk) together — improving faithfulness and letting it quote exact numbers as the system prompt demands.

---

### Phase 6 — Reasoning-aware graph retrieval

Replace the neighborhood-dump in `graph_rag/retrieval/graph_retriever.py`.

- **Query decomposition**: add `graph_rag/retrieval/query_planner.py` — use `get_llm()` to break a complex question into sub-questions / a target traversal pattern (e.g. identify anchor entity + target type).
- **Guided multi-hop Cypher**: traverse with **typed, directed** patterns derived from the plan instead of `[*1..2]` undirected. Rank paths by (a) relation-type relevance to the question, (b) path length (shorter preferred), (c) edge confidence.
- **Path-to-question scoring**: embed each candidate path's serialized form and score against the question embedding; keep top-N (replaces the current `1/(1+len)` heuristic and naive `LIMIT 50`).
- **Community summaries (GraphRAG-style, optional/stretch):** precompute clusters (e.g. per-satellite subgraphs) and an LLM summary per community for *global* questions ("overview of ISRO ocean-observing sensors"). Store summaries as nodes retrievable by vector search.

*Why this helps:* turns retrieval from "dump the neighborhood" into "walk the specific chain the question needs," which is the definition of multi-hop reasoning.

---

### Phase 7 — Iterative reasoning loop in the chain

Upgrade `graph_rag/chain/graph_rag_chain.py`.

- Add an **iterative retrieve→reason→re-retrieve** loop (bounded, e.g. ≤3 iterations): after the first answer attempt, if the LLM flags missing links (a "need more info about X" signal), re-enter retrieval seeded by the newly surfaced entities. This is classic multi-hop chaining.
- Add a **self-check pass**: before finalizing, the LLM verifies every numeric claim is present in the provided context (faithfulness guard), aligning with the existing "never invent numbers" rule in `prompts/system_prompt.txt`.
- Update the system prompt to: (a) present graph facts as typed paths, (b) instruct the model to reason step-by-step over paths for multi-hop questions, (c) show measurements with normalized + raw values for comparisons.

*Why this helps:* single-shot RAG can't follow a chain it didn't retrieve up front; a bounded loop lets the agent gather the *next* hop once it knows what it's missing.

---

## 4. File-by-file change summary

| Action | Path | Purpose |
|--------|------|---------|
| **new** | `graph_rag/knowledge_graph/ontology.py` | Node/relation vocabulary + verb→canonical mapping |
| **new** | `graph_rag/knowledge_graph/llm_extractor.py` | LLM schema-guided triple extraction |
| **new** | `graph_rag/knowledge_graph/quantity_parser.py` | Unit-normalized measurement facts (`pint`) |
| **new** | `graph_rag/knowledge_graph/resolver.py` | Alias + embedding entity canonicalization |
| **new** | `graph_rag/retrieval/query_planner.py` | Question decomposition / traversal planning |
| **new** | `graph_rag/eval/harness.py` + `tests/eval/multihop_questions.yaml` | Measure multi-hop & math accuracy |
| **edit** | `graph_rag/knowledge_graph/extractor.py` | Keep as typed fallback; map verbs → canonical relations |
| **edit** | `graph_rag/knowledge_graph/neo4j_store.py` | Typed labels/relationships, `MERGE` on `canonical_id`, `:Chunk`/`:Document` provenance, guided-traversal queries |
| **edit** | `graph_rag/ingestion/pipeline.py` | Use new extractor + resolver + provenance; resumable/cached extraction |
| **edit** | `graph_rag/retrieval/graph_retriever.py` | Guided directed multi-hop + path scoring + return supporting chunk text |
| **edit** | `graph_rag/chain/graph_rag_chain.py` | Iterative multi-hop loop + faithfulness self-check |
| **edit** | `graph_rag/config.py` | New settings: `extraction_backend`, similarity thresholds, max reasoning iterations, decomposition on/off |
| **edit** | `prompts/system_prompt.txt` | Typed-path presentation + step-by-step multi-hop + numeric-comparison guidance |
| **edit** | `requirement.txt` / `pyproject.toml` | Add `pint` (units); `rank-bm25`/`neo4j` already present |

---

## 5. Sequencing & dependencies

```
Phase 0 (eval)  ─┬─────────────────────────────────────────────► measures all below
                 │
Phase 1 (ontology) ─► Phase 2 (LLM extraction) ─► Phase 3 (quantitative facts)
                                                 └─► Phase 4 (resolution)
                                                       └─► Phase 5 (provenance)
                                                             └─► Phase 6 (retrieval)
                                                                   └─► Phase 7 (reasoning loop)
```

Each phase is independently shippable and is gated by re-running the Phase-0 harness. **Do not advance a phase that regresses the scorecard.**

---

## 6. Success criteria

- **Multi-hop accuracy** on the Phase-0 set improves materially vs. baseline (target: +20–30 pts).
- **Technical-spec faithfulness**: every numeric answer is grounded in a retrieved chunk/measurement (target: ≥95% faithful, 0 invented numbers).
- **Comparison/math queries** answerable from unit-normalized graph facts (e.g. correct "finer/coarser" verdicts).
- **Graph health**: entity-node count drops after canonicalization (fewer duplicates), typed-edge ratio rises (fewer generic `RELATION` edges).
- No regression in single-fact accuracy or latency budget (loop bounded; extraction cached offline).

---

## 7. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| LLM extraction is slow/expensive over the full corpus | Cache by `chunk_id`, batch, run offline during `python main.py ingest`; spaCy fallback stays for cheap/degraded mode |
| Small local model (Qwen2-1.5B) emits noisy JSON | Strict Pydantic validation + repair + controlled vocab clamp; few-shot domain examples; allow a larger model via existing `TABBY_MODEL` swap |
| Over-merging entities in resolution | Gate merges by ontology type + high similarity threshold + curated seed lexicon; log every merge for audit |
| Iterative loop increases latency | Hard cap iterations (≤3), short-circuit when graph context already answers, keep single-shot path as default for simple questions |
| Neo4j schema migration on existing data | Provide a re-ingest path (`neo4j.clear()` already exists) and a one-time migration script; version the graph build |

---

## 8. Quick wins (can land before the full plan)

1. **Drop trivial relations** (`IS`, `HAS`, `BE`) and stopword-only entities in `extractor.py` — instantly raises signal-to-noise in the current graph.
2. **Basic alias normalization** (strip `the`/trailing `satellite|sensor`, unify `-`/space) in `upsert_triple` — cheap partial fix for fragmentation.
3. **Directed + scored neighborhood** in `query_neighbors` (prefer shorter paths, drop the blunt `LIMIT 50`) — better graph context with no new infra.
4. **Return supporting chunk text** with graph triples — improves grounding using data already stored (`source_chunk_id`).

These deliver value immediately and de-risk the larger phases.
