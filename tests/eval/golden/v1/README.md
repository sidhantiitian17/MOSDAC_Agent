# Golden eval dataset — v1 (SEED)

This is the **versioned** gold set consumed by the RAGAS gate
(`graph_rag/eval/ragas_runner.py`, see [evaluation_plan.md](../../../../evaluation_plan.md) §3).

**Status: SEED — not yet gate-ready.** It demonstrates the full schema and every
stratum, but the per-stratum counts are well below the §3.2 minimums and the
content needs **domain human curation** before any GO/NO-GO is admissible.

## Files
| File | Strata | Source |
|------|--------|--------|
| `from_seed.jsonl` | single, multihop, comparison, followup | machine-converted from `tests/eval/multihop_questions.yaml` via `python -m graph_rag.eval.migrate_yaml` |
| `formula_numeric.jsonl` | formula, numeric_edge | hand-authored (illustrative — verify formulas/quantities against source PDFs) |
| `negatives.jsonl` | should_refuse_oos, should_refuse_unsafe, answerable_but_sparse | hand-authored |

## Schema
One JSON object per line; see `GoldenItem` in [dataset.py](../../../../graph_rag/eval/dataset.py)
and §3.3 of the plan. `//` and `#` lines are treated as comments.

## Before this gates a release (the human-in-the-loop work, §3.1 / §4.3)
1. **Re-ingest the corpus clean** (retrieval_boost §6) and confirm every `should_refuse_oos`
   item is genuinely *absent* — an item that is actually in the corpus would wrongly
   penalise a correct answer.
2. **Verify** every `reference`, `expected_formula`, and `expected_quantities` against
   the source documents. The machine-converted references are domain-typical, not
   confirmed.
3. **Expand** each stratum to its §3.2 minimum (overall n ≥ 175), ideally seeded by
   RAGAS `TestsetGenerator` over the real corpus, then human-curated.
4. **Calibrate the judge** (§4.3) and record judge↔human κ before trusting LLM metrics.

Bumping content here is a **breaking change to the benchmark** — cut a `v2/` rather
than mutating `v1/` once a baseline has been captured against it.
