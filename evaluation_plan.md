# Evaluation Plan — GraphRAG Pipeline (RAGAS), Production Gate

**Status:** Proposed
**Owner:** Eval / QA
**Bar:** *No-mercy.* This plan exists to **try to fail the system** before users do. A
"pass" means the pipeline survived a deliberately hostile evaluation, not that it
produced nice answers on easy questions.

---

## 0. Why this document exists (and why the current harness is not enough)

The existing harness ([graph_rag/eval/harness.py](graph_rag/eval/harness.py),
documented in [eval_baseline.md](eval_baseline.md)) gives us a repeatable scorecard,
but it is **not a production gate**. Concretely:

| Current metric | What it actually measures | Why it under-tests |
|---|---|---|
| `retrieval_hit_rate` | substring presence of a few expected keywords in context | A keyword can appear in an *irrelevant* passage; says nothing about ranking, precision, or whether the *right* chunk was retrieved. No recall denominator. |
| `faithfulness_score` | fraction of answer **numbers** present verbatim in context | Ignores every non-numeric claim. `1.0` when the answer has no numbers — a fluent hallucination with no digits scores perfect. |
| `correctness` (judge) | one weak LLM scores 0–1 vs a one-line reference | Judge is `get_llm()` = the **same** local model (`Qwen2-1.5B-Instruct`) that *generates* answers → self-grading, low discriminative power, no calibration. |
| dataset | 19 hand-written questions, "soft" targets | Too small for stratified CIs; no should-refuse / out-of-scope / formula / adversarial strata; answers assumed in corpus. |

The pipeline's own stated production goals ([retrieval_boost.md](retrieval_boost.md))
are **Grounded (G)**, **Precise/to-the-point (P)**, **Formula fidelity (F)**, and
**Least hallucination (H)**. The current harness measures none of G/P/F/H rigorously.
RAGAS gives us metric definitions and an LLM-judge harness for most of G/P/H;
**F, refusal-correctness, and numeric/unit fidelity are MOSDAC-specific and RAGAS
does not cover them — this plan adds custom evaluators for those.**

---

## 1. System under test (freeze the exact path)

Evaluation is meaningless unless the configuration is pinned. The graded path is the
**production text path** in [chat_api/service.py](chat_api/service.py) →
`_answer_text_only`, i.e. the full stack, **with guardrails ON**:

```
L1 input guard → QueryContextualizer → HybridRetriever
  (VectorRetriever bge-large/Chroma  +  BM25Okapi  +  GraphRetriever Neo4j 2-hop)
  → RRF fuse → rerank (bge cosine, or cross-encoder if enabled)
→ L2 grounding gate (check_groundable) → CitationRegistry
→ LCEL chain (prompt + Tabby LLM)  [graph_rag/chain/graph_rag_chain.py]
→ L4 output guard (citation_verify, numeric/sentence grounding, PII/leakage/toxicity)
→ answer + citations
```

**Two configurations must be evaluated and reported separately** — the system behaves
very differently with the guards' teeth in or out, and we must know both:

1. **`PROD`** — exactly the shipping `.env` (guards enforce; `GUARD_GROUNDING_ACTION=strip`).
2. **`RAW`** — same retrieval + LLM but guardrails set to `flag`-only (no strip/refuse).

`RAW` measures the *model+retrieval* quality; `PROD` measures what the *user* actually
gets. The delta between them is the guardrails' contribution (and their collateral
damage — see §6 refusal confusion matrix).

**Frozen knobs to record in every run's manifest** (pulled from
[graph_rag/config.py](graph_rag/config.py) and [guardrails/config.py](guardrails/config.py)):
`ollama_embedding_model`, `embed_query_instruction`, `enable_passage_rerank`,
`enable_cross_encoder_rerank`, `top_k_passages`, `hybrid_rrf_k`,
`enable_parent_expansion`, `enable_section_subsplit`, `chunk_max_section_chars`,
`enable_feature_boost`, `enable_query_decomposition`, `enable_iterative_reasoning`,
`tabby_model`, `llm_temperature`, `GUARD_RETRIEVAL_MIN_SCORE`,
`min_supporting_passages`, `GUARD_GROUNDING_ACTION`,
`grounding_min_sim`, `scope_min_sim`, **and the corpus/ingest manifest SHA**. A score
without this manifest is non-reproducible and inadmissible.

> ⚠️ Per [retrieval_boost.md](retrieval_boost.md) §6, cosine HNSW space, size-capped
> chunking, and `has_formula`/`parent_id` metadata only exist on chunks ingested
> *after* those changes. **Evaluation must run on a clean, fully re-ingested corpus**,
> or retrieval scores are measuring stale chunks. Re-ingest first; record the manifest SHA.

---

## 2. Metric suite — RAGAS mapped to pipeline stage and goal

Use **RAGAS ≥ 0.2** (`SingleTurnSample` / `MultiTurnSample`, `evaluate(...)`). Each
RAGAS sample carries `user_input`, `retrieved_contexts`, `response`, and
`reference` (+ `reference_contexts` where we have them). We populate
`retrieved_contexts` from the **real** pipeline output (`ctx["_hits"]` text in
[chat_api/service.py](chat_api/service.py#L193)) — *not* a re-retrieval — so the eval
sees exactly what the LLM saw.

### 2.1 Retrieval metrics (diagnose G and P at the source)

| RAGAS metric | Needs reference? | What it tells us | Goal |
|---|---|---|---|
| **LLMContextPrecisionWithReference** | gold answer | Are the retrieved chunks that matter ranked at the top? (signal-to-noise of the context) | P |
| **LLMContextRecall** | gold answer | Did retrieval bring back *everything* needed to answer? (the denominator the current hit-rate lacks) | G, P |
| **ContextEntityRecall** | gold entities | Fraction of required MOSDAC entities (satellites/sensors/params) present in context — directly relevant to this domain | G |
| **NonLLMContextPrecision/Recall** | gold `reference_contexts` | Cheap, deterministic, judge-free cross-check of the LLM-judged versions (catches judge drift) | P |
| **NoiseSensitivity** (relevant + irrelevant) | gold answer | Does the model get *misled* by an irrelevant-but-retrieved chunk into making wrong claims? The single best RAGAS proxy for "hybrid retrieval dragged in graph/BM25 noise." | H |

### 2.2 Generation / answer metrics (diagnose H and overall quality)

| RAGAS metric | Needs reference? | What it tells us | Goal |
|---|---|---|---|
| **Faithfulness** | no (uses contexts) | Fraction of answer claims entailed by the retrieved context — the core anti-hallucination metric, and the one to **gate hardest** on | H, G |
| **ResponseRelevancy** (answer relevancy) | no | Does the answer actually address the question, or wander? | P |
| **FactualCorrectness** (claim-level F1) | gold answer | Precision/recall of atomic claims vs reference — stricter than a single 0–1 judge | correctness |
| **SemanticSimilarity** | gold answer | Embedding similarity answer↔reference; cheap regression tripwire | correctness |

> **Faithfulness vs Correctness are orthogonal and both required.** An answer can be
> faithful to retrieved context yet wrong (bad chunk), or correct yet unfaithful
> (model knew it from pretraining, not the corpus). For a grounded KB assistant,
> **an unfaithful-but-correct answer is still a failure** — it means the grounding
> guarantee is fiction. Report them separately; never average them.

### 2.3 Custom evaluators RAGAS does **not** provide (MOSDAC-specific, mandatory)

These plug into the same harness as RAGAS metrics but are bespoke:

- **CE1 — Numeric & unit fidelity.** Extend the existing
  [grounding_check.py](guardrails/output/grounding_check.py) normalized comparison into
  a *scored* metric: for every quantity in the answer (value+unit), is it present in
  context after normalization (`1,400`/`1400`/`1.4 km`)? Report
  *grounded-quantity rate* **and** *unit-swap error rate* (right number, wrong unit —
  a silent, dangerous failure for satellite specs). Goal **H/F**.
- **CE2 — Formula fidelity.** For questions whose gold answer contains a `$$…$$`/symbol
  run, check the answer reproduces the formula **character-exact** (after the
  `_protect_math` normalization). Pass/fail per formula question. Goal **F** — RAGAS's
  text-similarity metrics cannot see a corrupted `\sigma_0`.
- **CE3 — Citation integrity.** Every `[Sx]` in the answer resolves to a real registry
  source (no fabricated cites — [citation_verify.py](guardrails/output/citation_verify.py)),
  AND every load-bearing factual sentence carries a citation. Report
  *fabricated-cite rate* and *uncited-claim rate*. Goal **G**.
- **CE4 — Refusal correctness** (the biggest gap RAGAS ignores). See §6.

---

## 3. The golden dataset (the hard part — most of the work is here)

RAGAS scores are only as good as the dataset. The 19-question YAML
([tests/eval/multihop_questions.yaml](tests/eval/multihop_questions.yaml)) is a seed,
not a gate. Build a **curated, stratified, versioned** set of **≥ 150 answerable +
≥ 60 negative** items.

### 3.1 Construction (two-source, human-in-the-loop)

1. **Synthetic bootstrap from the real corpus.** Use RAGAS `TestsetGenerator` over the
   *actually ingested* documents (the same Docling-parsed markdown the pipeline uses)
   to auto-generate single-hop, multi-hop, and "abstract"/comparison questions with
   reference answers and `reference_contexts`. This grounds the gold set in *our*
   corpus, not generic trivia, and gives `reference_contexts` for the judge-free
   NonLLM metrics for free.
2. **Human curation — mandatory, no exceptions.** Every synthetic item is reviewed by a
   domain reviewer who (a) fixes the reference answer against the source PDF, (b)
   verifies the gold contexts, (c) drops leaky/ambiguous items, (d) tags strata. The
   weak local generator *will* produce subtly wrong references; ungated synthetic gold
   is worse than no gold. Record inter-annotator agreement (§8).
3. **Adversarial hand-authoring.** Reviewers add the items the generator won't:
   should-refuse, out-of-scope, near-miss numerics, formula questions, multi-constraint
   comparisons.

### 3.2 Strata (each must hit a minimum n for a per-stratum CI)

| Stratum | n (min) | Purpose / what it stresses |
|---|---|---|
| `single` | 25 | one-fact retrieval + faithfulness |
| `multihop` | 25 | graph traversal + multi-passage synthesis (the GraphRAG thesis) |
| `comparison` | 20 | quantitative reasoning over 2+ entities (numeric fidelity, CE1) |
| `formula` | 15 | `$$…$$` retrieval + verbatim reproduction (CE2, F2 in retrieval_boost) |
| `numeric_edge` | 15 | unit-swap / thousands-separator / scale traps (CE1) |
| `followup` | 15 | history-aware contextualization (QueryContextualizer) |
| `should_refuse_oos` | 30 | **out-of-corpus** but plausible (e.g. "What's GISAT-1's downlink frequency?" when absent) — must refuse, not hallucinate |
| `should_refuse_unsafe` | 15 | injection / off-topic / PII — L1 must catch (overlaps security suite) |
| `answerable_but_sparse` | 15 | answer exists but in *one* long section — stresses chunking (F3) and the grounding gate's tendency to false-refuse |

Total ≈ 175 unanswerable+answerable. **Do not balance for a pretty average** — keep the
negative strata large *because false-confidence on absent data is this system's worst
failure mode.*

### 3.3 Format & versioning

Store as `tests/eval/golden/*.jsonl` (one record per line), schema:

```jsonc
{
  "id": "m7",
  "stratum": "multihop",
  "user_input": "...",
  "reference": "gold answer (curated)",
  "reference_contexts": ["chunk text 1", "chunk text 2"],   // for NonLLM metrics
  "expected_entities": ["Oceansat-2", "OCM"],               // for ContextEntityRecall / CE3
  "expected_quantities": [{"value": 360, "unit": "m"}],      // for CE1
  "expected_formula": "$$\\sigma_0 = ...$$",                 // for CE2 (formula stratum)
  "answerable": true,                                         // false ⇒ correct behavior is refusal
  "setup": ["prior turn 1"]                                  // followup only
}
```

Version the gold set (`golden/v1/`, checksum in the run manifest). Changing the gold
set is a **breaking change to the benchmark** and must be called out, never silent.

---

## 4. The evaluator (judge) model — get this wrong and every number is noise

RAGAS LLM metrics (Faithfulness, ContextPrecision/Recall, FactualCorrectness,
ResponseRelevancy) are **LLM-as-judge**. The judge is the measuring instrument; it must
be calibrated and trustworthy.

**Non-negotiables:**

1. **Judge ≠ generator.** The graded pipeline uses local Tabby `Qwen2-1.5B-Instruct`.
   The judge **must not** be that model (it can't reliably do NLI/claim
   decomposition, and self-grading is biased). Use the strongest available model as
   judge — a frontier model via API (e.g. a current Claude model through
   `langchain` → `LangchainLLMWrapper`), or at minimum a much larger local model than
   the generator. Wire embeddings for SemanticSimilarity/ResponseRelevancy via
   `LangchainEmbeddingsWrapper` (a stable embedder, **not** the one under test, to keep
   the metric independent of the component being graded).
2. **Determinism.** Judge `temperature=0`, fixed `seed`, pinned model version recorded
   in the manifest. Re-running the eval on the same data must reproduce within noise.
3. **Judge calibration (the step everyone skips).** Hand-label **40–60 (sample,
   metric)** pairs (a human says faithful/not, correct/not) and measure judge↔human
   agreement (Cohen's κ / correlation) **per metric**. If κ < ~0.6 on Faithfulness, the
   judge is not fit for gating and must be upgraded *before* trusting any score. Re-run
   calibration whenever the judge model version changes.
4. **Cost/throughput budget.** Faithfulness + context metrics are multi-call per item.
   175 items × ~6 metrics × claim-decomposition ≈ thousands of judge calls. Budget it,
   cache by `(item_id, pipeline_manifest_sha, judge_version)`, and allow a `--smoke`
   subset (e.g. 30 items) for fast iteration vs the full gated run.

---

## 5. Harness design — wire RAGAS to the *real* pipeline

Add `graph_rag/eval/ragas_runner.py` alongside the existing harness (reuse its YAML
loader and stratum plumbing; do **not** rip out [graph_rag/eval/harness.py](graph_rag/eval/harness.py) —
keep its cheap deterministic signals as a fast pre-check).

**Critical correctness requirements for the runner:**

- **Capture, don't re-retrieve.** Run each gold item through the actual
  `ChatService.chat` / `_answer_text_only` path and capture `(answer, citations,
  grounded, refused)` **and** the `ctx["_hits"]` passages that fed the LLM. RAGAS
  `retrieved_contexts` = those exact hits. Re-running a fresh retrieve for the eval
  would measure a *different* context than the user got.
- **Segregate refused vs answered before scoring.** A refusal
  (`REFUSAL_NO_CONTEXT`) is **not** a low-quality answer — running Faithfulness on
  "I don't have enough information" is meaningless and pollutes averages. Route:
  - `answerable && answered` → RAGAS generation+retrieval metrics + CE1–CE3.
  - `answerable && refused` → **false refusal** (count in §6 confusion matrix; exclude from RAGAS means).
  - `!answerable && refused` → **true refusal** (correct).
  - `!answerable && answered` → **hallucinated-on-absent** (the worst bucket; inspect every one).
- **Per-stratum + overall reporting**, with the answered/refused split shown, never hidden in an average.
- **Two-config run** (`PROD`, `RAW` from §1) in one invocation; emit the delta.
- **Determinism:** set generator `llm_temperature` for the eval to the production value
  but log it; if production temp > 0, run **k=3 repeats** per item and report
  mean ± spread so we see answer instability, not just one lucky sample.

Output: `eval_results_ragas_<config>_<date>.md` + machine-readable `.jsonl` of every
per-item score (for error analysis and CI diffing), plus the go/no-go scorecard (§9).

---

## 6. Refusal & guardrail evaluation (RAGAS-blind, mission-critical)

This is a guardrail-heavy system — the grounding gate
([grounding_gate.py](guardrails/retrieval/grounding_gate.py)) and L4 enforcement can
**refuse a good answer** or **let a bad one through**. RAGAS says nothing about this.
Evaluate the guardrails as a **binary classifier** over the should-refuse strata + the
answerable strata:

|  | model answered | model refused |
|---|---|---|
| **answerable** (gold) | ✅ true answer | ❌ **false refusal** (over-blocking; usability + grounding-gate miscalibration, cf. F1) |
| **not answerable** (gold) | ❌ **hallucination / leak** (worst case) | ✅ true refusal |

Report: **precision/recall of the refusal decision, false-refusal rate, and
hallucination-on-absent rate.** Plus, separately, the L1 security suite (injection /
scope / PII) — reuse [tests/test_pipeline_security.py](tests/test_pipeline_security.py)
fixtures; these are pass/fail security gates, **not** RAGAS-scored.

Also measure the **PROD−RAW delta**: how many answers did the L4 guard *strip/refuse*,
and of those, how many were genuinely ungrounded (good) vs correct-and-grounded
(false-positive strips, a regression on usability)?

---

## 7. Critical / adversarial evaluation ("no mercy")

Beyond the static gold set, actively try to break it:

1. **NoiseSensitivity sweep.** Inject a known-irrelevant passage into the context and
   confirm Faithfulness/answer don't degrade — quantifies hybrid-retrieval noise harm.
2. **Paraphrase robustness.** 3 paraphrases per question (incl. typos, Indian-English
   phrasing, sensor acronym vs full name "OSCAT" vs "Ku-band Scatterometer"). Variance
   in answer/score across paraphrases = brittleness. Flag any stratum whose score
   std-dev across paraphrases exceeds a threshold.
3. **Distractor / near-miss numerics.** Questions where the corpus contains *two*
   similar numbers (e.g. swath of OSCAT vs OCM) to catch the model grabbing the wrong
   grounded value — CE1 catches the unit/value, but this stresses *selection*.
4. **Counterfactual / unanswerable-by-design.** Ask for a spec that *plausibly* exists
   but isn't in the corpus. Must refuse. (Populated in `should_refuse_oos`.)
5. **Multi-hop that requires the graph.** Questions answerable *only* by 2-hop Neo4j
   traversal, not by a single vector chunk — isolates whether GraphRetriever actually
   contributes (otherwise the "Graph" in GraphRAG is dead weight). Compare
   graph-on vs graph-off retrieval recall on this stratum.
6. **Long-section recall (F3 regression).** Questions whose answer lives deep in a
   >512-token section — verifies chunking/parent-expansion fixes actually surfaced the
   fact (context recall here is the canary).
7. **Conversation drift.** Multi-turn `followup` chains of length ≥ 3 to test the
   contextualizer + summary buffer, scored with RAGAS `MultiTurnSample` where applicable.

---

## 8. Statistical rigor (so a 0.02 swing isn't mistaken for progress)

- **Sample size:** ≥ 15–30 per stratum (§3.2) so each stratum has a usable CI; overall n ≥ 175.
- **Confidence intervals:** report **bootstrap 95% CI** on every reported mean (per
  stratum and overall). A headline number without a CI is not a result.
- **Significance for regressions:** A/B (old vs new config) compared with a paired test
  on per-item scores; only call a change "real" if the CI excludes zero. Pre-register
  the threshold; don't eyeball.
- **Judge agreement:** report Cohen's κ (judge↔human) from §4.3 alongside scores so a
  reader knows the instrument's error bar.
- **Inter-annotator agreement** on the gold set's human labels (≥ 2 annotators on a
  20% overlap sample); κ < 0.6 means the gold itself is ambiguous — fix the items.
- **Determinism / repeats:** if generation temp > 0, k=3 repeats; report
  mean ± spread (answer-stability is itself a production metric).

---

## 9. Acceptance thresholds — the production go/no-go gate

These are **hard gates** on the `PROD` config over the full gold set. Tune the exact
numbers after the first calibrated baseline, but **the structure (per-metric floors,
not an average) is fixed** — a high mean must never paper over a catastrophic stratum.

| Dimension | Metric | Gate (initial) | Rationale |
|---|---|---|---|
| **Faithfulness** | RAGAS Faithfulness (answered items) | **≥ 0.90**, no stratum < 0.85 | Grounding is the product's core promise; this is the hardest gate. |
| **Hallucination on absent** | answered rate on `!answerable` | **≤ 2%** | Confidently answering when the corpus is silent is unacceptable. |
| **Numeric fidelity (CE1)** | grounded-quantity rate / unit-swap rate | **≥ 0.95 / ≤ 1%** | Wrong satellite spec = wrong science. |
| **Formula fidelity (CE2)** | char-exact pass rate on `formula` | **≥ 0.90** | Explicit product goal (F). |
| **Citation integrity (CE3)** | fabricated-cite rate / uncited-claim rate | **0% / ≤ 10%** | Fabricated `[Sx]` destroys trust. |
| **Context recall** | RAGAS LLMContextRecall | **≥ 0.85** | Can't ground what wasn't retrieved. |
| **Context precision** | RAGAS LLMContextPrecision | **≥ 0.70** | Noise in context drives hallucination. |
| **Answer correctness** | FactualCorrectness F1 (answered) | **≥ 0.75** | End-to-end usefulness. |
| **Answer relevancy** | RAGAS ResponseRelevancy | **≥ 0.80** | Don't wander / dodge. |
| **False refusal** | false-refusal rate on answerable | **≤ 8%** | Over-blocking kills usability; the F1 score-orientation bug makes this a live risk. |
| **Security** | L1 injection/scope/PII suite | **100% pass** | Non-negotiable. |
| **Judge trust** | judge↔human κ (Faithfulness) | **≥ 0.6** | If the instrument is untrusted, the gate is void. |

**Go/no-go rule:** **all** hard gates green on `PROD` over the **versioned** gold set,
with CIs, on a **freshly re-ingested** corpus, with a **calibrated judge**. Any red →
no production. A green average with one red stratum → **no-go** (investigate the stratum).

---

## 10. Execution plan & sequencing

| Phase | Work | Output | ~Effort |
|---|---|---|---|
| **P0** | Add `ragas` (+ judge LLM/embeddings wrappers) to [requirement.txt](requirement.txt); pin versions; smoke `evaluate()` on 5 items | deps + `ragas_runner.py` skeleton | 0.5 d |
| **P1** | Clean **re-ingest** of full corpus (cosine space, capped chunks, feature metadata — retrieval_boost §6); record manifest SHA | reproducible corpus | 0.5 d |
| **P2** | Build gold set: RAGAS `TestsetGenerator` → human curation → adversarial authoring → `golden/v1/*.jsonl` | versioned dataset (§3) | 3–5 d |
| **P3** | Judge selection + **calibration** vs 40–60 human labels (κ per metric) | calibrated judge + κ report (§4) | 1.5 d |
| **P4** | `ragas_runner.py`: capture-not-re-retrieve, refused/answered segregation, PROD+RAW, CE1–CE4, bootstrap CIs | full harness (§5–6) | 2–3 d |
| **P5** | First **baseline** run; error analysis; failure taxonomy; set final thresholds | baseline scorecard | 1 d |
| **P6** | Adversarial / robustness sweep (§7) | robustness report | 1 d |
| **P7** | Wire a `--smoke` subset into CI as a regression tripwire (not the full gate) | CI check | 0.5 d |

Suggested order mirrors the dependency chain: **P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7.**
Nothing downstream is trustworthy until P1 (fresh corpus) and P3 (calibrated judge) are done.

---

## 11. Reproducibility, CI, and cost

- **Every run emits a manifest** (§1 frozen knobs + corpus SHA + gold version + judge
  version + seeds). No manifest, no admissible result.
- **CI:** the `--smoke` subset (≈30 items, deterministic NonLLM + CE1/CE3 + a cheap
  faithfulness sample) runs per-PR as a **tripwire** to catch regressions; the **full
  gated run** is run manually/nightly before a release decision (cost + judge-call
  volume make it unsuitable for every PR).
- **Caching:** memoize judge calls on `(item_id, pipeline_sha, judge_version, metric)`
  so re-runs after a code change only re-grade changed items.
- **Cost guardrail:** report judge token spend per run; the `--smoke` path must be cheap
  enough to run freely.

---

## 12. Known limits of this plan (stated honestly, per the no-mercy bar)

- **RAGAS is itself LLM-judged** → it has its own error bar. §4.3 calibration bounds it,
  but Faithfulness/Correctness numbers are estimates, not ground truth. Treat the
  deterministic checks (NonLLM context metrics, CE1/CE2/CE3) as the *trusted floor* and
  the LLM metrics as *calibrated estimates*.
- **Gold-set bias.** A synthetic-seeded gold set can under-represent real user phrasing.
  Mitigate by folding **real production query logs** (from the L5 audit,
  [guardrails/audit/logger.py](guardrails/audit/logger.py)) into `golden/v2` once
  available — close the loop with reality.
- **Corpus coupling.** Scores are valid only for the ingested corpus snapshot; a corpus
  change invalidates the baseline. Re-baseline on any significant re-ingest.
- **Latency/cost are separate gates.** This plan grades *quality*; production also needs
  p50/p95 latency and throughput budgets (the `latency_ms` already logged in
  [chat_api/service.py](chat_api/service.py)) — track them, but they are out of scope here.

---

## 13. Implementation status (delivered)

The **machinery** of this plan is implemented, modular, and offline-tested. The
**human/live** phases (corpus re-ingest, gold-set curation to the §3.2 minimums,
judge calibration, the actual gated run) remain — they need a domain reviewer, the
live stack, and a configured judge.

| Plan section | Status | Code |
|---|---|---|
| §3 golden dataset schema/loader/checksum | ✅ code; ⏳ seed only | [dataset.py](graph_rag/eval/dataset.py), seed in [tests/eval/golden/v1/](tests/eval/golden/v1/) |
| §3.1 YAML→golden migration | ✅ | [migrate_yaml.py](graph_rag/eval/migrate_yaml.py) |
| §2.3/§6 custom evaluators CE1–CE4 | ✅ | [custom_metrics.py](graph_rag/eval/custom_metrics.py) |
| §8 bootstrap CIs + paired A/B test | ✅ | [stats.py](graph_rag/eval/stats.py) |
| §9 go/no-go gate (per-metric floors, stratum floor) | ✅ | [scorecard.py](graph_rag/eval/scorecard.py) |
| §5 capture-not-re-retrieve probe + refused/answered split | ✅ | [probe.py](graph_rag/eval/probe.py) |
| §2 RAGAS suite + §4 judge wiring + §1 PROD/RAW + manifest + reporting | ✅ code; ⏳ needs live judge | [ragas_runner.py](graph_rag/eval/ragas_runner.py) |
| CLI `python main.py ragas-eval` | ✅ | [main.py](main.py) |
| Offline test coverage (57 tests) | ✅ | `tests/test_eval_*.py` |
| §4.3 judge calibration (κ vs human) | ⏳ human | feed result via `--kappa`; gate SKIPs (→ NO-GO) until provided |
| §3.2 gold set to n≥175 | ⏳ human | expand `tests/eval/golden/v1/` |
| §1 clean re-ingest + first baseline (§P5) | ⏳ live | run after re-ingest |

**Dependency note:** `ragas` is pinned to the **0.2 line** in [requirement.txt](requirement.txt);
it targets the langchain 0.3 stack this project uses. Do not unpin to ≥0.4 — it drags
langchain to 1.x and breaks `langchain-chroma`/`langchain-neo4j`.

**To run the gate** (after re-ingest + judge config in `.env`):
```bash
python main.py ragas-eval --config BOTH            # PROD + RAW, full metric suite
python main.py ragas-eval --smoke --limit 30       # cheap CI tripwire subset
python main.py ragas-eval --kappa 0.71             # supply judge↔human agreement
```

---

### TL;DR

Re-ingest clean → build a **versioned, stratified, human-curated** gold set (heavy on
**negatives/refusals/formulas/numerics**, the strata the current 19-question set lacks)
→ grade the **real captured pipeline output** (not a re-retrieval) with **RAGAS
(Faithfulness, Context Precision/Recall, Entity Recall, NoiseSensitivity, Factual
Correctness, Response Relevancy, Semantic Similarity)** **plus custom CE1–CE4** for
numerics/formulas/citations/refusals, using a **strong, calibrated, non-generator
judge** → segregate **refused vs answered**, evaluate the **guardrails as a classifier**,
report **per-stratum bootstrap CIs** → ship only when **every hard gate is green**.
```