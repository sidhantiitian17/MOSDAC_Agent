# Evaluation Baseline & Phase 0/6/7 Verification

This document records the **Phase 0 evaluation harness**, how to run it, the first
captured baseline, and the live-verification results for the reasoning phases
(6 and 7) from `plan.md`.

---

## 1. The harness (Phase 0)

- **Question set:** [tests/eval/multihop_questions.yaml](tests/eval/multihop_questions.yaml) —
  19 curated questions across three buckets: `single` (one fact), `multihop`
  (link 2+ facts), `comparison` (compare/compute with numbers).
- **Runner:** [graph_rag/eval/harness.py](graph_rag/eval/harness.py) — `EvalHarness`.
- **Metrics (per question):**
  - **retrieval** — fraction of the question's expected entities/keywords that
    appear in the retrieved context (graph + vector).
  - **faithful** — fraction of the numbers in the answer that are grounded in the
    retrieved context (1.0 when the answer has no numbers).
  - **correct** — optional LLM-as-judge score (0–1) vs. the reference; `—` when
    run with `--no-judge`.

### Run it

```bash
python main.py eval                       # full set, LLM judge on
python main.py eval --limit 10 --no-judge # quick, no judge
python main.py eval --set path/to.yaml --out my_results.md
```

Output: a scorecard table (per type + overall) and a detailed `eval_results.md`.

---

## 2. Captured baseline (2026-06-10)

First baseline, run with `python main.py eval --limit 2 --no-judge`:

```
type           n   retrieval   faithful   correct
-------------------------------------------------
single         2        0.50       0.50      0.00
-------------------------------------------------
OVERALL        2        0.50       0.50      0.00
```

| id | type | retrieval | faithful | question |
|----|------|-----------|----------|----------|
| s1 | single | 0.33 | 0.00 | What spatial resolution does the OCM sensor provide? |
| s2 | single | 0.67 | 1.00 | Which satellite carries the Scatterometer (OSCAT)? |

> **Important caveats on this baseline — it is intentionally conservative:**
> 1. **Embeddings endpoint was down** during this run (Tabby's `/v1/embeddings`
>    returned 404 after a Docker restart), so **vector retrieval contributed
>    nothing** — only the graph + BM25 paths fed the context. This depresses
>    `retrieval` and `faithful`.
> 2. **The graph is still the legacy spaCy build** (~44k generic-verb edges); the
>    Phase 1–5 LLM-extracted, measurement-rich graph has not been rebuilt over the
>    full corpus yet. Re-ingest with `EXTRACTION_BACKEND=llm` and a capable
>    `TABBY_EXTRACTION_MODEL` to populate typed edges + `Measurement` nodes, then
>    re-run this harness — that is the before/after the plan targets.
>
> Treat these numbers as a **floor**. The value of Phase 0 is the repeatable
> measurement, not this particular score.

To capture a fuller baseline once embeddings are back and the graph is rebuilt:

```bash
python main.py ingest --skip-vector        # rebuild typed KG (LLM extraction)
python main.py eval --out eval_baseline_run.md
```

---

## 3. Live verification of the reasoning phases

All three phases were exercised against the live stack (Neo4j + Tabby) this session:

| Phase | Feature | Live result |
|------|---------|-------------|
| 6 | **LLM query decomposition** (`QueryPlanner`) | ✅ "Which sensors on Oceansat-2 measure sea-surface parameters, and at what resolution?" → `multihop=True`, anchors `['Oceansat-2','sea-surface parameters','resolution']`, split into 2 atomic sub-questions (30 s). |
| 6 | **Community summaries** (`CommunitySummarizer`) | ✅ Hub selection + `elementId` neighborhood traversal + `:Community` node storage ran against the real 44k graph (3 nodes created, then cleaned up). Embedding/vector-search step blocked only by the embeddings outage. |
| 6 | **Path reranking** | ✅ Active in `GraphRetriever`; degrades gracefully to length order when embeddings are unavailable. |
| 7 | **Iterative reasoner** (`IterativeReasoner`, real LLM) | ✅ Multi-hop grounded answer: *"Oceansat-2 carries the Ocean Colour Monitor (OCM), which measures chlorophyll concentration with a spatial resolution of 360 m."* Loop terminated cleanly (1 pass, 30 s); faithfulness self-check kept the number `360 m`. |
| 7 | **Faithfulness self-check guard** | ✅ A weak verifier model initially truncated the answer to "Oceansat-2"; added a guard that keeps the draft when the correction is a drastic truncation (covered by a new unit test). |
| 0 | **Eval harness** | ✅ Ran end-to-end live, produced the scorecard above and `eval_results.md`. |

Unit coverage (all offline, mocked): `test_query_planner.py`, `test_iterative_chain.py`,
`test_eval_harness.py` — plus the full suite (**132 passing**).

---

## 4. Enabling the features

The heavier LLM-loop features are **opt-in** (default off) to preserve latency.
Turn them on in `.env`:

```ini
ENABLE_QUERY_DECOMPOSITION=true   # Phase 6: sub-question planning
ENABLE_ITERATIVE_REASONING=true   # Phase 7: retrieve→reason→re-retrieve loop
ENABLE_COMMUNITY_SUMMARIES=true   # Phase 6: use community overviews (build first)
GRAPH_RERANK=true                 # Phase 6: embedding rerank of paths (on by default)
MAX_REASONING_ITERATIONS=3
```

Build community summaries offline first (one LLM call per hub — slow on a small
local model; point `TABBY_EXTRACTION_MODEL`/`TABBY_MODEL` at a larger model):

```bash
python main.py build-communities --limit 30 --min-degree 4
```
