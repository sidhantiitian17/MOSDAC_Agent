# `guardrails/input/` — L1 Input Guard

The **first** checkpoint, run **before any retrieval or LLM spend**. It cleans the user's
text, blocks prompt-injection/jailbreaks, redacts PII, and refuses off-topic questions — so
malicious or irrelevant input is rejected cheaply, before the system pays for anything.

Invoked by [guardrails/pipeline.py](../pipeline.py) `check_input()` → `_check_input_inner()`.

---

## Order of checks (and why)

```
normalize  →  injection  →  pii redact  →  scope gate
(clean &      (regex +       (strip         (embedding centroid:
 charset)      embedding)     PII)           on-topic for MOSDAC?)
   │             │                              │
   ▼             ▼                              ▼
 refuse if    refuse if attack              refuse if off-topic
 empty/bad    (or degraded→fail-closed)     (or degraded→fail-closed)
```

---

## File-by-file

### [normalize.py](normalize.py) — text hygiene
Unicode **NFKC** normalization, control-char stripping, length cap
(`GUARD_MAX_INPUT_LENGTH`), and a charset check that rejects garbage/binary input.
- **Functions:** `normalize(text, max_length)`, `check_charset(text)`.
- *(Begins with a UTF-8 BOM — keep encoding.)*

### [injection.py](injection.py) — prompt-injection / jailbreak detection
Two tiers: a **regex/phrase** tier (fast, deterministic) and an **embedding-similarity**
tier that compares the input against a corpus of known attack phrases
([tests/guardrails/injection_corpus.txt](../../tests/guardrails/injection_corpus.txt)).
Also provides `sanitize_context` — used by the retriever to neutralize **indirect**
injection hidden inside retrieved passages (P1-3).
- **Functions:** `check(text)`, `embedding_similarity_status(text, threshold)`,
  `check_embedding_similarity`, `sanitize_context`, `reset_attack_corpus_cache`.
- **Depends on:** `graph_rag.embeddings`, `guardrails.config`.

### [pii.py](pii.py) — PII detection & redaction
Detects and redacts personal data (emails, phones, ids, etc.), optionally via Presidio when
available. Applied to **input** here and reused by [output/pii_out.py](../output/pii_out.py)
for the answer.
- **Functions:** `redact(text)`, `contains_pii(text)`, `_try_presidio`.

### [scope.py](scope.py) — on-topic scope gate
The **domain firewall**. Embeds the query and compares it to a precomputed **centroid** of
MOSDAC seed phrases; if cosine similarity is below `GUARD_SCOPE_MIN_SIM`, the question is
off-topic and refused. The centroid is cached to disk (`GUARD_SCOPE_CENTROID_PATH`); seed
phrases can be overridden via `GUARD_SCOPE_SEED_PATH` to serve a different domain with no
code change.
- **Functions:** `check_with_status(text, min_sim, path)`, `check`, `_seed_phrases`,
  `_compute_centroid`, `_load_or_compute_centroid`, `invalidate_centroid_cache`.
- **Depends on:** `graph_rag.embeddings`, `guardrails.config`.

### [__init__.py](__init__.py)
Package marker (L1 input guard modules).

---

## Degraded mode (embedder down)
`injection` (embedding tier) and `scope` both need the embedder. If it's unavailable they
run **degraded**: by default fail **open** (preserve availability) but always emit
`guardrail_degraded_total`; set `GUARD_EMBEDDER_REQUIRED=true` to fail **closed** (refuse)
instead — handled centrally in `pipeline._on_degraded`.

## Dependencies at a glance
- **Internal:** `graph_rag.embeddings`, `guardrails.config`.
- **External:** optional `presidio` (PII), numpy (centroid math via the embedder).
