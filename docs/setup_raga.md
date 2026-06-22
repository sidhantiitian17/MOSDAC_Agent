# Evaluating the MOSDAC RAG Pipeline — A Complete, Beginner-Friendly Guide (RAGAS + Cohen's Kappa)

> **Who this is for:** someone who has *never* heard of RAGAS, Cohen's Kappa, or
> "LLM-as-a-judge" and wants to understand **and run** the evaluation of this
> project's chatbot from scratch. Every hard word is explained the first time it
> appears. There is a [Glossary](#15-glossary--every-hard-word-in-plain-english) at the bottom.
>
> The authoritative design document is [evaluation_plan.md](../evaluation_plan.md).
> This guide is the *friendly companion* to it. Where this guide simplifies, the
> plan is the source of truth.

---

## 0. The 5-year-old version (read this first)

Imagine our chatbot is a **student** taking an exam about satellites.

1. We write an **exam** (a list of questions where *we already know the right
   answers*). This is called the **golden dataset**.
2. The student (our chatbot) **answers** every question. While it answers, we
   secretly **photocopy the textbook pages it actually looked at** (this is the
   "context" it retrieved).
3. We hire a **smart teacher** to grade the answers. The teacher is a *different,
   smarter AI* — never the student itself, because students who grade their own
   exams always give themselves an A. This teacher is the **judge**. The tool that
   runs the teacher with a fixed grading rubric is called **RAGAS**.
4. We also run some **simple, robotic checks** that don't need a teacher at all —
   like "every number the student wrote must also appear in the textbook pages."
   These are the **custom checks (CE1–CE4)**.
5. Before we trust the teacher's grades, we **double-check the teacher**: a human
   grades ~50 of the same answers and we measure *how often the human and the AI
   teacher agree*. That agreement score is **Cohen's Kappa (κ)**. If the teacher
   disagrees with humans too often, we don't trust its grades at all.
6. Finally we put every score onto a **report card** (the "scorecard") with strict
   pass marks. If **even one** subject fails, the whole student is held back —
   this is the **GO / NO-GO gate**. A high average can never hide one failed
   subject.

That's the whole system. The rest of this document is just detail on each step.

---

## 1. The big picture in one diagram

```
                         tests/eval/golden/v1/*.jsonl
                         (the EXAM — questions + known answers)
                                      │
                                      ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  STEP 1  CAPTURE  (probe.py)                                       │
   │  Run each question through the REAL chatbot (ChatService.chat).    │
   │  Record: the answer, the citations, whether it refused, AND the    │
   │  exact passages it retrieved (photocopy the textbook pages).       │
   └──────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  STEP 2  SEGREGATE  (ragas_runner.py)                              │
   │  Split into "answered" vs "refused". A refusal is NOT a bad        │
   │  answer — we don't grade "I don't know" for quality.              │
   └──────────────────────────────────────────────────────────────────┘
              │ answered-and-was-answerable          │ everything
              ▼                                       ▼
   ┌─────────────────────────────┐   ┌──────────────────────────────────┐
   │ STEP 3  RAGAS METRICS       │   │ STEP 4  CUSTOM CHECKS CE1–CE4    │
   │ (LLM judge grades quality)  │   │ (robotic, no judge needed)        │
   │  faithfulness, recall, …    │   │  numbers, units, formulas, cites, │
   │                             │   │  refusal-correctness              │
   └─────────────────────────────┘   └──────────────────────────────────┘
              │                                       │
              └──────────────────┬────────────────────┘
                                 ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  STEP 5  AGGREGATE  (ragas_runner.py + stats.py)                   │
   │  Average every metric, add 95% confidence intervals, add the       │
   │  human-supplied judge-trust score (Cohen's Kappa).                │
   └──────────────────────────────────────────────────────────────────┘
                                 ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  STEP 6  SCORECARD / GATE  (scorecard.py)                          │
   │  Compare every metric to its pass mark. ALL green → ✅ GO.         │
   │  Any red, or any missing → ❌ NO-GO.                              │
   └──────────────────────────────────────────────────────────────────┘
                                 ▼
            eval_runs/ragas_prod_<timestamp>.md   (the report card)
            eval_runs/..._items.jsonl             (per-question detail)
            eval_runs/..._manifest.json           (proof of exact setup)
```

**Plain-English glossary for the diagram:**
- **RAG** = *Retrieval-Augmented Generation*. The chatbot first **retrieves**
  relevant document chunks, then the LLM **generates** an answer using them. It
  doesn't answer from memory; it answers from looked-up text.
- **Evaluation pipeline** = the machinery that *measures how good* that chatbot is.
- **Metric** = a single number that measures one quality (e.g. "how grounded?").
- **Gate** = a pass/fail decision based on whether every metric clears its bar.

---

## 2. There are actually TWO evaluators in this repo (don't confuse them)

This trips people up, so it's the very next thing to understand.

| | **Legacy harness** (`eval`) | **RAGAS production gate** (`ragas-eval`) |
|---|---|---|
| Command | `python main.py eval` | `python main.py ragas-eval` |
| Code | [graph_rag/eval/harness.py](../graph_rag/eval/harness.py) | [graph_rag/eval/ragas_runner.py](../graph_rag/eval/ragas_runner.py) |
| Purpose | Cheap, fast, "is it broken?" smoke test | The serious, rigorous **release gate** |
| Judge | The **same** weak local model that writes answers (self-grading ⚠️) | A **separate, stronger** judge model (RAGAS) |
| "Faithfulness" means | only "do the answer's *numbers* appear in context" | LLM checks **every claim** against context |
| Dataset | 19 soft questions in [multihop_questions.yaml](../tests/eval/multihop_questions.yaml) | versioned, stratified golden set in [tests/eval/golden/v1/](../tests/eval/golden/v1/) |
| Verdict | a printed average table | a hard **GO / NO-GO** scorecard |
| Needs internet/judge? | No | Yes (a configured judge endpoint) |

**Which one do you want?**
- Quick "did I just break retrieval?" → `python main.py eval` (cheap, no judge).
- "Is this safe to ship to real users?" → `python main.py ragas-eval` (the real thing).

The legacy harness is kept on purpose as a fast pre-check. The rest of this
document is about the **RAGAS gate**, because that's what "evaluating the RAG
pipeline properly" means here.

> **Why the legacy harness is "not enough"** (this is the project's own critique, in
> [evaluation_plan.md §0](../evaluation_plan.md)): its faithfulness scores 1.0 for any
> answer with no numbers (a fluent lie with no digits gets a perfect score); its
> "correctness" judge is the *same* tiny `Qwen2-1.5B-Instruct` that wrote the answer
> (self-grading is biased and weak); and 19 questions is far too few, with no
> "should-refuse" or adversarial questions. RAGAS + the golden set fix all of that.

---

## 3. Every file in the evaluation system, and what it does

All evaluation code lives in [graph_rag/eval/](../graph_rag/eval/). Here is the whole
folder, top to bottom, in plain English:

| File | One-line job | "RAGAS" inside? |
|---|---|---|
| [dataset.py](../graph_rag/eval/dataset.py) | Defines the **exam format** (a "golden item"), loads the `.jsonl` files, validates them, and fingerprints them. | no |
| [probe.py](../graph_rag/eval/probe.py) | **Runs the real chatbot** on each question and *captures* the answer + the exact passages it saw (the "capture, don't re-retrieve" rule). | no |
| [custom_metrics.py](../graph_rag/eval/custom_metrics.py) | The **robotic checks CE1–CE4** (numbers, units, formulas, citations, refusal correctness). No AI judge needed. | no |
| [ragas_runner.py](../graph_rag/eval/ragas_runner.py) | The **conductor**. Wires up the judge, runs the **RAGAS** metric suite, calls the custom checks, averages everything, writes the report. | **YES** |
| [scorecard.py](../graph_rag/eval/scorecard.py) | The **report card & pass marks**. Turns the numbers into ✅ GO / ❌ NO-GO. | mentions Kappa |
| [stats.py](../graph_rag/eval/stats.py) | The **statistics**: 95% confidence intervals + A/B significance tests, so a 0.02 wiggle isn't mistaken for real improvement. | no |
| [harness.py](../graph_rag/eval/harness.py) | The **legacy** cheap evaluator (the `eval` command from §2). | no |
| [migrate_yaml.py](../graph_rag/eval/migrate_yaml.py) | One-off converter: turns the old 19-question YAML into golden `.jsonl`. | no |
| [__init__.py](../graph_rag/eval/__init__.py) | Marks the folder as a Python package. | no |

The exam data itself lives separately in [tests/eval/golden/v1/](../tests/eval/golden/v1/):

| File | Contains |
|---|---|
| [from_seed.jsonl](../tests/eval/golden/v1/from_seed.jsonl) | 22 answerable questions auto-converted from the old YAML. |
| [formula_numeric.jsonl](../tests/eval/golden/v1/formula_numeric.jsonl) | 8 formula + numeric-trap questions (hand-written). |
| [negatives.jsonl](../tests/eval/golden/v1/negatives.jsonl) | 12 "the chatbot SHOULD refuse" questions (hand-written). |
| [README.md](../tests/eval/golden/v1/README.md) | Explains the dataset is a **SEED**, not gate-ready yet. |

> ⚠️ **Reality check:** that's **42 questions total**, but the plan
> ([§3.2](../evaluation_plan.md)) requires **≥ 175** with minimum counts per category.
> So today's dataset is a *demonstration of the machinery*, **not** a dataset you can
> base a real ship/no-ship decision on. More on this in [§12 Limitations](#12-honest-limitations--read-before-you-trust-a-number).

---

## 4. What is RAGAS, really?

**RAGAS** ("Retrieval-Augmented Generation Assessment") is an open-source Python
library. Think of it as **a box of pre-written grading rubrics for RAG chatbots**,
each rubric driven by an AI "judge."

You hand RAGAS four things per question (this bundle is called a `SingleTurnSample`):

| Field given to RAGAS | Plain English | Where it comes from in our code |
|---|---|---|
| `user_input` | the question | the golden item |
| `retrieved_contexts` | the textbook pages the bot actually saw | **captured** by [probe.py](../graph_rag/eval/probe.py) |
| `response` | the bot's answer | captured |
| `reference` | the known-correct answer | the golden item |
| `reference_contexts` | (optional) the *ideal* pages it should have found | the golden item |

RAGAS then runs each **metric**. Most metrics work by **asking the judge LLM
questions**, e.g. *"Here is a claim from the answer and here is the context — is
this claim supported? yes/no."* It does this for every claim and turns the yes/no
answers into a number between 0 and 1.

This file builds the RAGAS samples: see `build_ragas_dataset` in
[ragas_runner.py](../graph_rag/eval/ragas_runner.py). The actual scoring call is the
single line `result = evaluate(dataset=..., metrics=..., llm=judge_llm, embeddings=judge_emb)`
inside `run_ragas_scores`.

---

## 5. The metrics — what each one actually measures

There are **three families** of metrics in this system. Family A and B come from
RAGAS (AI judge). Family C is our own robotic code.

### 5A. RAGAS retrieval metrics — "did it look up the right pages?"

| Metric (our name) | RAGAS class used | The question it answers | Higher = |
|---|---|---|---|
| `context_recall` | `LLMContextRecall` | Did retrieval bring back **everything** needed to answer? | better |
| `context_precision` | `LLMContextPrecisionWithReference` | Of the pages it fetched, are the **useful** ones ranked at the top (low junk)? | better |
| `context_entity_recall` | `ContextEntityRecall` | Were the required satellites/sensors/parameters present in the fetched pages? | better |
| `noise_sensitivity` | `NoiseSensitivity` | Does an *irrelevant* fetched page **trick** the bot into a wrong claim? | **lower** is better |

*Why these matter:* the bot **cannot ground** an answer in a page it never fetched.
If recall is low, hallucination is inevitable downstream — garbage in, garbage out.

### 5B. RAGAS answer metrics — "is the written answer any good?"

| Metric (our name) | RAGAS class used | The question it answers | Higher = |
|---|---|---|---|
| `faithfulness` | `Faithfulness` | Is **every claim** in the answer actually supported by the fetched pages? (the anti-hallucination metric — gated hardest) | better |
| `answer_relevancy` | `ResponseRelevancy` | Does the answer actually address the question, or wander/dodge? | better |
| `factual_correctness` | `FactualCorrectness` | How well do the answer's facts match the known-correct reference (claim-level F1)? | better |
| `semantic_similarity` | `SemanticSimilarity` | How close in meaning is the answer to the reference (embedding similarity)? | better |

> **The single most important idea here:** *faithfulness* and *correctness* are
> **different things and both required**. An answer can be **faithful** (everything
> it said is in the fetched pages) yet **wrong** (the page was wrong). Or it can be
> **correct** yet **unfaithful** (the model knew it from its own memory, not from
> our documents). For a grounded knowledge-base assistant, *a correct-but-unfaithful
> answer is still a failure*, because it means the "we only answer from our
> documents" promise is fiction. That is why they are reported separately and
> **never averaged together**.

The exact mapping of our names → RAGAS classes lives in `_RAGAS_METRIC_KEYS` at the
top of [ragas_runner.py](../graph_rag/eval/ragas_runner.py). The cheaper `--smoke`
run uses only a subset: `faithfulness, answer_relevancy, context_recall,
factual_correctness` (`_SMOKE_KEYS`).

### 5C. Custom checks CE1–CE4 — the robotic, no-judge checks

RAGAS can't see some MOSDAC-specific failures (a corrupted formula, a swapped
unit, a faked citation). So [custom_metrics.py](../graph_rag/eval/custom_metrics.py)
implements four deterministic checks. *Deterministic* means: no AI, same input
always gives the same output, free to run, and 100% repeatable.

| Check | Name | What it catches | Why it's dangerous if missed |
|---|---|---|---|
| **CE1** | Numeric & unit fidelity | Every number in the answer must appear in the context. A *right number with the wrong unit* (e.g. "360 km" when the source says "360 m") is flagged as a **unit-swap**. | A wrong satellite spec is wrong science — and a unit-swap is *silent*: the number looks right. |
| **CE2** | Formula fidelity | A formula must be reproduced **character-exact** (ignoring spaces). A corrupted `\sigma_0` is caught. | RAGAS's text-similarity can't tell a subtly broken formula from a correct one. |
| **CE3** | Citation integrity | No **fabricated** `[S3]`-style citations (must point to a real source), and load-bearing factual sentences should carry a citation. | A made-up citation destroys user trust completely. |
| **CE4** | Refusal correctness | Classifies each answer into a 2×2 grid: did it correctly answer / correctly refuse / wrongly refuse / wrongly answer? | See §6 — this is the biggest gap RAGAS ignores. |

These checks deliberately **reuse the production guardrail code** (the same
`grounding_check` / `citation_verify` the live bot uses), so the metric agrees with
what the real system does instead of drifting away from it.

---

## 6. The refusal matrix — the metric RAGAS completely ignores

This is mission-critical for a knowledge bot, so it gets its own section.

Some golden questions are **deliberately unanswerable** (the answer is *not* in our
documents). For those, the **only correct behavior is to refuse** ("I don't have
enough information"). RAGAS has no concept of this. So CE4 treats the bot like a
**binary classifier** and sorts every question into four boxes:

|  | **Bot answered** | **Bot refused** |
|---|---|---|
| **Question WAS answerable** | ✅ `true_answer` (good) | ❌ `false_refusal` (over-blocking — annoying, hurts usability) |
| **Question was NOT answerable** | ❌ `hallucinated_on_absent` (**the worst box** — it made something up) | ✅ `true_refusal` (good) |

From this grid the system computes:
- **false-refusal rate** — of answerable questions, how many were wrongly refused.
- **hallucination-on-absent rate** — of unanswerable questions, how many were
  wrongly answered. *This is the failure mode the whole gate cares about most.*
- **refusal precision / recall** — quality of the refusal decision itself.

This logic is `classify_outcome` and `RefusalConfusion` in
[custom_metrics.py](../graph_rag/eval/custom_metrics.py). The questions marked
`answerable: false` live in [negatives.jsonl](../tests/eval/golden/v1/negatives.jsonl).

---

## 7. The judge — "LLM-as-a-judge" explained

Most RAGAS metrics need an AI to make a yes/no decision ("is this claim
supported?"). That AI is the **judge**. Using one AI to grade another AI's output
is called **"LLM-as-a-judge."**

**The cardinal rule: the judge must NOT be the chatbot it is grading.** Our
chatbot is generated by a small local model (`Qwen2-1.5B-Instruct`). If we used the
same tiny model to grade itself, two bad things happen: (1) it's too weak to do the
careful "claim-by-claim" reasoning grading requires, and (2) self-grading is biased
(students grade themselves generously). So the judge has to be a **stronger,
separate** model.

### How the judge is wired

You configure the judge entirely through environment variables (no code changes).
The function that reads them is `build_judge` in
[ragas_runner.py](../graph_rag/eval/ragas_runner.py):

| Env variable | What it is | Example (from [.env.example](../.env.example)) |
|---|---|---|
| `RAGAS_JUDGE_MODEL` | the judge chat model's name (**required**) | `Qwen2.5-Coder-32B-Instruct` |
| `RAGAS_JUDGE_BASE_URL` | an OpenAI-compatible chat endpoint URL | `http://localhost:8080/v1` |
| `RAGAS_JUDGE_API_KEY` | the key/token for that endpoint | `your_tabby_token_here` |
| `RAGAS_JUDGE_EMBED_MODEL` | embedding model (for the similarity metrics) | `bge-large` |
| `RAGAS_JUDGE_EMBED_BASE_URL` | embedding endpoint URL | `http://localhost:11434` |
| `RAGAS_JUDGE_EMBED_API_KEY` | key for the embedding endpoint | *(blank for local)* |

Key facts baked into `build_judge`:
- If `RAGAS_JUDGE_MODEL` is **empty**, the gate **refuses to run** — it will never
  silently grade with no judge.
- The judge is forced to `temperature=0` with a fixed `seed=1234` — meaning
  **determinism**: grade the same data twice, get the same scores (within noise).
- It connects through any **OpenAI-compatible** endpoint. That includes a hosted
  frontier model's API, or a big local model served by Tabby/Ollama/vLLM. The plan
  *recommends a frontier model* as judge because it's the most reliable grader.

> **Note on the example value:** the `.env.example` ships with a large local model
> (`Qwen2.5-Coder-32B-Instruct`) as a *working default* so the gate runs offline.
> It is far bigger than the 1.5B generator (so it satisfies "judge ≠ generator, and
> stronger"), but for a real release decision the plan suggests the strongest model
> you can access. Whatever you choose, you must **prove you can trust it** — which is
> exactly what Cohen's Kappa (next section) is for.

---

## 8. Cohen's Kappa (κ) — "can we even trust the judge?"

We just hired an AI teacher. But what if the AI teacher is a *bad* teacher? Then
every grade it produces is noise, and the whole report card is worthless. So before
trusting the judge, we **audit the judge against humans**.

### What Kappa is, for a 5-year-old

You and a friend both grade the **same 50 answers** as "good" or "bad." Sometimes
you agree, sometimes you don't. **Cohen's Kappa is a single number from −1 to 1
that says how much you two truly agree — *after subtracting the agreement you'd get
by random luck.***

Why subtract luck? Because if 90% of answers are "good," two people guessing
"good" every time would agree 90% of the time *while knowing nothing*. Kappa
removes that freebie. So:

| Kappa value | Meaning |
|---|---|
| **1.0** | perfect agreement |
| **~0.8–1.0** | very strong agreement |
| **~0.6–0.8** | substantial agreement (this project's minimum is **0.6**) |
| **~0.4–0.6** | moderate — shaky |
| **0** | no better than random guessing |
| **< 0** | actively disagreeing (worse than coin flips) |

### How it's used here — and the important part: it is a HUMAN step

This project's rule ([evaluation_plan.md §4.3](../evaluation_plan.md)): a human
hand-labels **40–60 (answer, metric) pairs** — e.g. a person reads each answer and
says "faithful: yes/no." Then you compute how often the **AI judge** agreed with
the **human**, expressed as Cohen's Kappa, *per metric*. If κ for Faithfulness is
**below ~0.6**, the judge is declared **not fit to gate** and must be upgraded
before any score is trusted.

**Crucial implementation detail:** there is **no automatic Kappa calculator in this
codebase.** Computing κ requires human labels, which the repo can't produce on its
own. So the workflow is:

1. *You* (a human) hand-label ~50 answers and compute Kappa yourself — using any
   standard tool, e.g. Python's `sklearn.metrics.cohen_kappa_score(human_labels,
   judge_labels)`.
2. You **feed the resulting number** into the gate with the `--kappa` flag:
   `python main.py ragas-eval --kappa 0.71`.
3. That number flows through `run_gate(..., judge_kappa=0.71)` into the scorecard as
   the `judge_kappa` metric (see `aggregate_results` in
   [ragas_runner.py](../graph_rag/eval/ragas_runner.py) and `JUDGE_KAPPA` in
   [scorecard.py](../graph_rag/eval/scorecard.py)).
4. **If you don't pass `--kappa`,** the `judge_kappa` metric is missing → the gate
   marks it **SKIP** → and a SKIP **forces NO-GO**. In other words: *no proof the
   judge is trustworthy = automatic fail.* This is by design.

So yes — **the evaluation absolutely requires human review** to ever return a real
GO, in two places: (a) curating/verifying the golden answers, and (b) calibrating
the judge to get Kappa.

---

## 9. The scorecard — the GO / NO-GO gate

After all metrics are averaged, [scorecard.py](../graph_rag/eval/scorecard.py)
compares each one to a **fixed pass mark** (a "threshold"). These thresholds live
in the `GateThresholds` dataclass. Here they are, with what each means:

| Gate | Rule | Pass mark | Plain meaning |
|---|---|---|---|
| Faithfulness | min | **≥ 0.90** | ≥90% of answer claims must be grounded — the hardest gate. |
| Per-stratum faithfulness | min | **≥ 0.85** | *No single category* may drop below 0.85 (no hiding a weak topic). |
| Hallucination on absent | max | **≤ 0.02** | ≤2% of unanswerable questions may be wrongly answered. |
| Numeric fidelity (CE1) | min | **≥ 0.95** | ≥95% of numbers grounded. |
| Unit-swap rate (CE1) | max | **≤ 0.01** | ≤1% right-number-wrong-unit errors. |
| Formula fidelity (CE2) | min | **≥ 0.90** | ≥90% of formulas reproduced exactly. |
| Fabricated citations (CE3) | max | **= 0.0** | **Zero** faked citations allowed. |
| Uncited claims (CE3) | max | **≤ 0.10** | ≤10% of factual sentences may lack a citation. |
| Context recall | min | **≥ 0.85** | retrieval must fetch what's needed. |
| Context precision | min | **≥ 0.70** | fetched context must be mostly relevant. |
| Answer correctness | min | **≥ 0.75** | facts must match the reference. |
| Answer relevancy | min | **≥ 0.80** | answer must address the question. |
| False refusal | max | **≤ 0.08** | ≤8% of good questions wrongly refused. |
| Security suite | min | **= 1.0** | **100%** of unsafe prompts must be refused. |
| Judge trust (Kappa) | min | **≥ 0.60** | the judge must be proven trustworthy. |

**The golden rule of the gate** (`ScoreCard.go`):

```
GO  ⇔  every gate is PASS  AND  no category broke its faithfulness floor
```

- A metric that **couldn't be computed** (no data) becomes **SKIP**, and a SKIP
  **blocks GO** — silence is treated as failure, not a free pass.
- A great overall average with **one** failed category is still **NO-GO**. This is
  deliberate: averages hide disasters.

The thresholds are intentionally a *starting point* ("Tune after the first
calibrated baseline" says the code). You can pass your own `GateThresholds` once you
have a real baseline.

---

## 10. Statistical rigor — why every number has a ± attached

[stats.py](../graph_rag/eval/stats.py) exists to stop you fooling yourself.

- **Confidence interval (CI):** "faithfulness = 0.91" means little if you only
  tested 5 questions. So the system reports a **95% bootstrap confidence interval**,
  e.g. `0.91 [0.84, 0.96]` — meaning "we're 95% confident the true value is between
  0.84 and 0.96." A wide interval = not enough data to trust the headline number.
  - *Bootstrap* = a trick where the computer re-samples your results thousands of
    times (here, 2000) to estimate that range. It's deterministic here (fixed
    `seed=1234`) so it's repeatable.
- **Paired A/B test** (`paired_bootstrap_delta`): when comparing **old config vs
  new config** on the *same* questions, it tells you whether an improvement is
  **real** or just luck. The rule: only believe a change if its confidence interval
  **excludes zero** (`excludes_zero`). This stops a random 0.02 wiggle being
  celebrated as progress.

---

## 11. HOW TO ACTUALLY RUN IT — step by step (no prior RAGAS knowledge needed)

This is the hands-on part. Follow it in order.

### Step 0 — Prerequisites (what must be alive)

The RAGAS gate runs the **real** chatbot, so the whole live stack must be up:

- **Neo4j** (the knowledge graph), **Chroma** (the vector store), and **Tabby**
  (the local LLM that generates answers) — the same services the chatbot needs
  normally. (If you can run `python main.py chat` and get answers, you're ready.)
- **Ollama** serving the `bge-large` embedding model at `http://localhost:11434`
  (this project uses bge-large, *not* nomic-embed-text).
- A **Python environment** with dependencies installed. `ragas` is already pinned in
  [requirement.txt](../requirement.txt) (`ragas>=0.2,<0.3` — do **not** upgrade past
  0.3; it would drag langchain to 1.x and break things).
- A **freshly re-ingested corpus** if anything about ingestion changed — otherwise
  you're scoring stale document chunks. (See the warning in
  [evaluation_plan.md §1](../evaluation_plan.md).)

> The *legacy* `python main.py eval` needs the live bot but **no judge** — it's the
> easy one to try first to confirm the stack works.

### Step 1 — Configure the judge (one-time, in `.env`)

Open your `.env` file (copy from [.env.example](../.env.example) if you don't have
one) and set the six `RAGAS_JUDGE_*` variables from [§7](#7-the-judge--llm-as-a-judge-explained).
The minimum is a real `RAGAS_JUDGE_MODEL`. **Remember: it must not be the same model
that generates the chatbot answers.**

A sanity check that the judge is configured (it raises a clear error if not):

```bash
python -c "from graph_rag.eval.ragas_runner import build_judge; print(build_judge())"
```

### Step 2 — Do a tiny smoke run first (cheap, fast)

Never start with the full run. Do a 5-item smoke test to confirm everything is
wired:

```bash
python main.py ragas-eval --smoke --limit 5
```

- `--smoke` uses only the 4 cheapest metrics.
- `--limit 5` grades only the first 5 questions.

If this completes and writes files into `eval_runs/`, your plumbing works.

### Step 3 — Run the real gate

```bash
# PROD config (what users actually get, guards ON), full metric suite:
python main.py ragas-eval

# Both PROD and RAW (RAW = guardrails set to "flag-only") to see the guards' impact:
python main.py ragas-eval --config BOTH

# Supply your human-calibrated judge-trust score so the Kappa gate can PASS:
python main.py ragas-eval --kappa 0.71
```

All flags for `python main.py ragas-eval` (from `cmd_ragas_eval` in
[main.py](../main.py)):

| Flag | Default | Meaning |
|---|---|---|
| `--gold PATH` | `tests/eval/golden/v1` | the exam file or folder to use |
| `--config NAME` | `PROD` | `PROD`, `RAW`, or `BOTH` |
| `--smoke` | off | cheap metric subset (for fast iteration / CI) |
| `--limit N` | all | only grade the first N questions |
| `--out DIR` | `eval_runs` | where to write the report files |
| `--kappa F` | none | the human-measured judge↔human agreement |

**PROD vs RAW, simply:** PROD = the shipping config with **guardrails enforcing**
(it may strip/refuse). RAW = the same retrieval+LLM but with **guardrails set to
flag-only** (no strip/refuse). The difference between the two scores tells you
exactly *what the guardrails are doing* — both the hallucinations they prevent and
the good answers they accidentally block.

### Step 4 — Read the output

Three files appear in `eval_runs/` (timestamped):

| File | What's in it |
|---|---|
| `ragas_prod_<timestamp>.md` | the **human-readable report card** — open this first. It has the GO/NO-GO verdict, the refusal matrix, and every metric with its confidence interval. |
| `..._items.jsonl` | one line **per question** with all its scores — for digging into *which* questions failed. |
| `..._manifest.json` | a **fingerprint of the exact setup** (model versions, all the pipeline knobs, corpus hash, gold-set hash). Without this a score isn't reproducible. |

The top of the `.md` will say either `## Production Gate — ✅ GO` or `❌ NO-GO`, then
a table showing which gates passed (✅), failed (❌), or were skipped (⚠️).

> **What you'll see today with the seed dataset:** because the judge-Kappa is almost
> certainly not provided, expect `judge_kappa … ⚠️ SKIP` → `❌ NO-GO`. That's the
> system working **correctly** — it refuses to bless a release until a human has
> proven the judge is trustworthy and curated a real dataset.

### The exit code (for scripting / CI)

`python main.py ragas-eval` returns **0 if PROD is GO**, **1 otherwise**. So you can
wire it into CI as a tripwire: `python main.py ragas-eval --smoke --limit 30 || echo "regression!"`.

---

## 12. Honest limitations — read before you trust a number

The project documents its own weaknesses ([evaluation_plan.md §12](../evaluation_plan.md));
here they are in plain language, plus a couple of practical ones:

1. **The dataset today is a SEED, not a gate.** Only **42** questions exist vs the
   required **≥175**, and the answers/formulas are *machine-converted or
   illustrative*, **not yet verified by a domain expert against the source PDFs**.
   Any GO/NO-GO on it would be meaningless until it's expanded and human-curated.
2. **RAGAS is itself AI-judged**, so its faithfulness/correctness numbers are
   *calibrated estimates, not ground truth*. Trust the **deterministic** checks
   (CE1/CE2/CE3, context metrics) as the firm floor; treat the LLM metrics as
   estimates with an error bar — which is precisely why Kappa exists.
3. **Kappa is manual.** The repo cannot compute it for you; a human must hand-label
   ~50 items and pass the number via `--kappa`. Until then the gate is NO-GO.
4. **Scores are glued to one corpus snapshot.** Re-ingest the documents and your old
   baseline is invalid — you must re-baseline.
5. **Gold-set bias.** Synthetic/curated questions may not match how real users
   phrase things. The plan's fix is to fold real query logs into a future `v2`.
6. **Latency and cost are NOT graded here.** This pipeline measures *quality* only.
   Speed/throughput are separate gates.
7. **Judge cost is real.** A full run is ~175 questions × ~8 metrics × multiple
   judge calls each = *thousands* of LLM calls. Use `--smoke` for iteration; save
   the full run for actual release decisions. (The plan mentions caching judge calls
   as a future optimization; it is not yet implemented in the runner.)
8. **The thresholds are provisional.** The numbers in `GateThresholds` are sensible
   starting guesses to be tuned after the first calibrated baseline, not laws.

---

## 13. Does it require human review? (direct answer)

**Yes — in three distinct places. The system is intentionally designed so it cannot
return a real "GO" without humans:**

1. **Building the golden dataset.** A domain expert must verify every reference
   answer, formula, and quantity against the source documents, confirm the
   "should-refuse" questions are genuinely absent from the corpus, and expand each
   category to its minimum size. (See [the golden README](../tests/eval/golden/v1/README.md).)
2. **Calibrating the judge (Cohen's Kappa).** A human hand-labels ~40–60 answers,
   computes κ, and feeds it via `--kappa`. Without it the gate is NO-GO.
3. **Interpreting the verdict.** A green scorecard is a recommendation, not an
   autopilot. Someone reviews the failed/borderline items before shipping.

The *machinery* (running, scoring, reporting) is fully automated; the *judgment*
(is the exam fair? is the grader trustworthy? do we ship?) is human.

---

## 14. Quick reference cheat-sheet

```bash
# Cheap legacy smoke test (no judge needed) — "is the bot obviously broken?"
python main.py eval
python main.py eval --no-judge --limit 5

# RAGAS gate — sanity plumbing check (5 items, cheap metrics)
python main.py ragas-eval --smoke --limit 5

# RAGAS gate — full PROD run
python main.py ragas-eval

# RAGAS gate — PROD + RAW, with a human-calibrated judge-trust score
python main.py ragas-eval --config BOTH --kappa 0.71

# Convert the old YAML questions into golden JSONL (one-off)
python -m graph_rag.eval.migrate_yaml

# Run the offline unit tests for the eval machinery (no live stack/judge)
pytest tests/test_eval_*.py -q
```

**Files to know:** judge config → [.env.example](../.env.example) ·
exam → [tests/eval/golden/v1/](../tests/eval/golden/v1/) ·
conductor → [ragas_runner.py](../graph_rag/eval/ragas_runner.py) ·
report card → [scorecard.py](../graph_rag/eval/scorecard.py) ·
full design → [evaluation_plan.md](../evaluation_plan.md).

---

## 15. Glossary — every hard word in plain English

| Term | Plain meaning |
|---|---|
| **RAG** | *Retrieval-Augmented Generation*. Look up relevant documents first, then answer using them — not from the model's memory. |
| **RAG pipeline** | The whole chatbot: retrieve → (guardrails) → LLM writes answer → (guardrails) → reply. |
| **Evaluation pipeline** | Separate machinery that *measures how good* the RAG pipeline is. |
| **RAGAS** | A Python library of ready-made grading rubrics for RAG systems, driven by an AI judge. |
| **Metric** | One number measuring one quality (e.g. faithfulness). |
| **Golden dataset / golden item** | The exam: questions with known-correct answers and supporting info. |
| **Stratum** (plural strata) | A category of question (single-fact, multi-hop, formula, should-refuse, …). Each is scored separately so no weak topic hides. |
| **Context / retrieved_contexts** | The document passages the bot fetched and used to answer. |
| **Reference** | The known-correct answer we compare against. |
| **Capture (not re-retrieve)** | Grade the *exact* passages the bot really saw, not a fresh lookup that might differ. |
| **LLM-as-a-judge** | Using a (smarter, separate) AI to grade another AI's answers. |
| **Judge** | That grading AI. Configured via `RAGAS_JUDGE_*` env vars; must not be the generator. |
| **Faithfulness** | Fraction of the answer's claims that are actually supported by the fetched context (anti-hallucination). |
| **Correctness / FactualCorrectness** | How well the answer's facts match the reference. *Different from faithfulness.* |
| **Context recall / precision** | Did retrieval fetch everything needed (recall) and avoid junk (precision)? |
| **Hallucination** | The model making up something not supported by the documents. |
| **Refusal** | The bot declining to answer ("I don't have enough info"). Correct for unanswerable questions. |
| **False refusal** | Refusing a question it *should* have answered (over-blocking). |
| **CE1–CE4** | This project's custom robotic checks: numbers/units, formulas, citations, refusal-correctness. |
| **Deterministic** | No randomness: same input → same output, every time, for free. |
| **Guardrails** | Safety layers (L1 input filter, L2 grounding gate, L4 output checks) that can strip/refuse risky answers. |
| **PROD vs RAW** | PROD = guardrails enforcing (what users get). RAW = guardrails flag-only. The gap shows the guardrails' effect. |
| **Cohen's Kappa (κ)** | How much a human and the AI judge agree, after removing lucky agreement. Range −1…1; need ≥0.6. |
| **Confidence interval (CI)** | The ± range around a number; "95% CI" = we're 95% sure the true value is in this range. |
| **Bootstrap** | A computer trick (re-sampling results many times) to estimate that CI. |
| **Scorecard / gate** | The pass/fail report. Every metric must clear its bar for ✅ GO. |
| **GO / NO-GO** | Ship / don't ship. One red metric (or a missing one) = NO-GO. |
| **Manifest** | A fingerprint file recording the exact models, settings, and data versions, so a result is reproducible. |
| **Smoke run** | A tiny, cheap run to check things work before the expensive full run. |
| **Embedding** | Turning text into a list of numbers so meanings can be compared by distance. Used by the similarity metrics. |
```
