# `guardrails/output/` — L4 Output Guard

The **third** checkpoint, run **after the LLM** and **before the answer reaches the user**.
The LLM has spoken — now we make sure it didn't leak secrets, didn't cite sources that
don't exist, didn't hallucinate numbers/sentences, didn't emit PII, and wasn't toxic. This
is the last line of defence and the second half of the anti-hallucination story (L2 was the
first).

Invoked by [guardrails/pipeline.py](../pipeline.py) `check_output()` → `_check_output_inner()`.

---

## Order of checks

```
leakage scrub → citation verify → grounding enforcement → PII redact → toxicity
(remove system    ([Sx] must exist   (ungrounded numbers/    (strip      (refuse if
 prompt/secret     in the registry)   sentences → flag/        PII)        toxic)
 echoes)                               strip/refuse)
```

`GUARD_GROUNDING_ACTION` controls the grounding step: `flag` (log only), `strip` (remove
ungrounded sentences; refuse if too little survives or `GUARD_GROUNDING_MAX_UNGROUNDED_RATIO`
is exceeded), or `refuse` (any ungrounded content → canonical refusal).

---

## File-by-file

### [leakage.py](leakage.py) — secret / system-prompt leakage scrub
Detects and scrubs cases where the model echoes the system prompt, the raw context, file
paths, or secrets.
- **Functions:** `check_leakage(answer)`, `scrub_leakage(answer)`.

### [citation_verify.py](citation_verify.py) — citation integrity
Extracts the `[Sx]` IDs the model cited and verifies **every one exists** in the L2
`CitationRegistry`; fabricated citations are removed. Returns the cleaned answer + the list
of valid citations surfaced to the user.
- **Functions:** `extract_cited_ids(answer)`, `verify(answer, registry)`.
- **Reused by:** [graph_rag/eval/custom_metrics.py](../../graph_rag/eval/custom_metrics.py)
  (CE3 citation integrity) — so eval and runtime agree.

### [grounding_check.py](grounding_check.py) — numeric & sentence grounding
The hallucination detector. Finds **numbers** in the answer not present in the retrieved
context (`check_numeric_grounding`), and **factual sentences** whose embedding isn't similar
enough to any retrieved passage (`check_sentence_grounding`, threshold `GUARD_GROUNDING_MIN_SIM`).
`strip_ungrounded` removes the unsupported sentences when in strip mode.
- **Functions:** `check_numeric_grounding`, `check_sentence_grounding`, `strip_ungrounded`,
  `_factual_sentences`, `_normalize_number`, `_cosine_sim`.
- **Depends on:** `graph_rag.embeddings`. **Reused by:** eval CE1/CE2.

### [pii_out.py](pii_out.py) — output PII redaction
Redacts any PII from the answer before return, reusing the input redactor.
- **Function:** `redact_output(answer)`. **Depends on:** `guardrails.input.pii`.

### [safety.py](safety.py) — toxicity / profanity
Blocks toxic/profane output; a positive hit returns the canonical generic refusal.
- **Function:** `check_toxicity(answer)`.

### [__init__.py](__init__.py)
Package marker (L4 output guard modules).

---

## Why two anti-hallucination layers (L2 + L4)?

- **L2** stops the LLM from being asked at all when evidence is missing.
- **L4** checks the produced answer against the evidence it *was* given — catching the case
  where evidence existed but the LLM still drifted (invented a resolution, swapped a unit,
  added an uncited claim). Together they make ungrounded facts very hard to reach the user.

## Dependencies at a glance
- **Internal:** `graph_rag.embeddings` (grounding), `guardrails.input.pii` (redaction),
  `guardrails.config` (thresholds + action).
