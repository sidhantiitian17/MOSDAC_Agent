# `graph_rag/eval/` — Evaluation & the Production Go/No-Go Gate

This package decides **whether the system is good enough to ship**. It runs the pipeline
against a curated **golden dataset** and scores it with **RAGAS** metrics plus custom
domain metrics (CE1–CE4), then renders a **GO / NO-GO scorecard**. It is intentionally a
"no-mercy" gate that tries to *fail* the system before real users do.

Run it with `python main.py ragas-eval` (CLI wiring in [main.py](../../main.py)). Legacy
cheap harness: `python main.py eval`. Full methodology: [evaluation_plan.md](../../evaluation_plan.md).

---

## How a gate run works

```
golden dataset (tests/eval/golden/v1)   ──► dataset.load_golden
        │
        ▼  probe.capture_all  (replay each question through a REAL ChatService)
   captured turns (answer, contexts, citations, refused?)
        │
        ▼  ragas_runner  ──► RAGAS metrics (faithfulness, answer relevancy, …)
        │                └─► custom_metrics CE1–CE4 (numeric/formula/citation/refusal)
        ▼  stats  (bootstrap confidence intervals, paired deltas)
        ▼  scorecard  ──► thresholds → GateResult per metric → overall GO / NO-GO
```

---

## File-by-file

### [ragas_runner.py](ragas_runner.py) — the gate runner
Orchestrates a full RAGAS run: builds the **judge** model, builds the metric set, runs the
pipeline under PROD or RAW config, aggregates per-item records, and writes the markdown
report + manifest. `run_gate(...)` is the top-level called by the CLI.
- **Key functions:** `build_judge`, `_build_metrics`, `guard_config_override`,
  `build_ragas_dataset`, `run_ragas_scores`, `aggregate_results`, `render_markdown`,
  `write_outputs`, `run_gate`. Types: `ItemRecord`, `ResultBundle` (with `go_scorecard()`).
- **Depends on:** `config`, `eval.dataset`, `eval.probe`, `eval.custom_metrics`,
  `eval.scorecard`, `eval.stats`, `ragas`, `datasets`.

### [dataset.py](dataset.py) — the golden dataset
Schema + JSONL loader + validation + checksum for the gold questions (with expected
answers, quantities, and source ids). `golden_checksum` pins the dataset version into the
report so results are reproducible.
- **Key pieces:** `GoldenItem`, `Quantity`, `load_golden`, `stratum_counts`,
  `golden_checksum`, `DEFAULT_GOLDEN_DIR`. Data lives in
  [tests/eval/golden/v1/](../../tests/eval/golden/v1/).

### [probe.py](probe.py) — pipeline capture
Replays each golden question through a **real** `ChatService` (with a `RecordingRetriever`
that captures exactly what context was retrieved), producing `CapturedTurn`s the metrics
score. This guarantees the eval measures the *actual* production path.
- **Key pieces:** `build_probe_service`, `capture_turn`, `capture_all`, `CapturedTurn`,
  `RecordingRetriever`.
- **Depends on:** `chat_api.service`, `chat_api.session`, `chain.graph_rag_chain`,
  `llm.tabby_client`, `retrieval.hybrid_retriever`, `eval.dataset`.

### [custom_metrics.py](custom_metrics.py) — CE1–CE4 domain metrics
Domain-specific evaluators that RAGAS doesn't cover:
- **CE1 NumericFidelity** — did numbers/units survive correctly (no unit swaps)?
- **CE2 formula fidelity** — formulas reproduced faithfully?
- **CE3 CitationIntegrity** — no fabricated or uncited claims?
- **CE4 RefusalConfusion** — did it refuse when it should (and not when it shouldn't)?
- **Reuses guardrail logic:** imports `guardrails.output.citation_verify` and
  `guardrails.output.grounding_check` so eval and runtime agree on what "grounded" means.

### [scorecard.py](scorecard.py) — GO/NO-GO scorecard
Defines the **gate thresholds** and turns metric values into pass/fail `GateResult`s and an
overall `ScoreCard` with `.render()` and a `.go` boolean.
- **Key pieces:** `GateThresholds`, `GateDef`, `GateResult`, `ScoreCard`, `build_scorecard`,
  and the metric-name constants used by `ragas_runner`.

### [stats.py](stats.py) — statistical helpers
Bootstrap confidence intervals and paired deltas so the gate reports *significant*
differences, not noise. `bootstrap_ci`, `paired_bootstrap_delta`, `excludes_zero`, `mean`,
`CI`.

### [harness.py](harness.py) — legacy Phase-0 harness
A cheaper, deterministic evaluation (`python main.py eval`) for fast local iteration:
retrieval hit-rate, numeric extraction, optional LLM judge, markdown scorecard.
- **Key pieces:** `EvalHarness`, `EvalQuestion`, `EvalResult`, `faithfulness_score`,
  `retrieval_hit_rate`, `extract_numbers`, `DEFAULT_QUESTION_SET`.
- **Depends on:** `chat.chatbot`, `retrieval.hybrid_retriever`, `llm.tabby_client`.

### [migrate_yaml.py](migrate_yaml.py) — dataset migration tool
One-off converter from the legacy Phase-0 question YAML into golden JSONL.

### [__init__.py](__init__.py)
Package marker (Phase-0 eval).

---

## Dependencies at a glance
- **Internal:** `graph_rag.config`, `graph_rag.chain`, `graph_rag.retrieval`,
  `graph_rag.llm`, `chat_api.service` (probe), and `guardrails.output.*` (custom metrics).
- **External:** `ragas` (0.2 line, pinned), `datasets`.
- **Requires:** a configured **judge** (`RAGAS_JUDGE_*`) that is a *stronger* model than the
  generator under test, plus the live pipeline (Chroma/Neo4j/Tabby).
