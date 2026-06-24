# `guardrails/` — Defense-in-Depth Security Pipeline

This package is what makes the chatbot **safe to deploy on a Government of India portal**.
It wraps the RAG core in four deterministic checkpoints that block bad input, refuse
unanswerable questions, sanitize bad output, and audit everything. "Deterministic" is the
key word: these are **rules + embeddings**, not "ask another LLM to behave" — so the
behaviour is predictable, testable, and fails closed.

> Pipeline context: [readme_main.md §8](../readme_main.md). The guardrails are invoked by
> [chat_api/service.py](../chat_api/service.py) at four points in every turn.

---

## The four layers (L1 / L2 / L4 / L5)

```
USER INPUT ──► L1 input/  ──► (retrieval) ──► L2 retrieval/ ──► (LLM) ──► L4 output/ ──► L5 audit/
              normalize        grounding gate                   leakage scrub          PII-safe log
              injection        source allowlist                 citation verify        abuse counter
              PII redact       cypher-safe names                grounding enforce       metrics
              scope gate                                         PII redact + toxicity
```

| Layer | Folder | Runs | Purpose |
|-------|--------|------|---------|
| **L1** | [input/](input/) | before any spend | normalize, block injection/jailbreak, redact PII, refuse off-topic |
| **L2** | [retrieval/](retrieval/) | after retrieval, before LLM | is there enough evidence? build the citation registry; allowlist sources |
| **L4** | [output/](output/) | after the LLM | scrub leaks, verify citations, strip/refuse ungrounded content, redact PII, block toxicity |
| **L5** | [audit/](audit/) | end of turn | PII-safe structured audit log + abuse tracking |

(There is no "L3" here — that label is the request-ID/log correlation in the gateway.)

---

## Top-level files

### [pipeline.py](pipeline.py) — `GuardrailPipeline` (the orchestrator)
A **stateless singleton** (`get_pipeline()`) exposing the four checkpoints the service
calls:
- **`check_input(text, session_id)`** → `GuardDecision` (L1). Runs normalize → injection →
  PII → scope, with abuse-lockout short-circuit and degraded-mode handling.
- **`check_retrieval_groundable(hits, manifest_path)`** → `(passes, CitationRegistry,
  top_score)` (L2).
- **`check_output(answer, registry, passages, context)`** → `(clean_answer, citations,
  reasons)` (L4). Enforces `GUARD_GROUNDING_ACTION` (flag/strip/refuse).
- **Fail-closed by default:** any guard exception → refuse when `GUARD_FAIL_CLOSED=true`.
  Embedder-down handling (`_on_degraded`) always emits a metric and either fails open
  (availability) or refuses (`GUARD_EMBEDDER_REQUIRED`).
- **Depends on:** `guardrails.config`, `guardrails.decisions`, `guardrails.templates`,
  `guardrails.retrieval.grounding_gate`, and lazily every sub-module + `observability`.

### [config.py](config.py) — `GuardrailSettings`
Every control as a `GUARD_*` env flag, fail-closed defaults: master `enable`/`fail_closed`;
L1 (`pii_input`, `injection`, `injection_sim_threshold`, `scope_gate`, `scope_min_sim`,
`max_input_length`, `embedder_required`); L2 (`retrieval_min_score`,
`min_supporting_passages`, `source_allowlist`, `context_injection_scan`); L4
(`citation_verify`, `grounding_min_sim`, `grounding_action`, `grounding_max_ungrounded_ratio`,
`toxicity`, `leakage_check`); L5 (`audit`, `rate_limit_per_min`, `session_ttl_seconds`,
`abuse_lockout_threshold`, `audit_log_path`); plus paths (scope centroid, injection corpus,
optional domain seed file).

### [decisions.py](decisions.py) — decision types
`Action` (ALLOW / REFUSE) and `GuardDecision` (action + cleaned_text + reasons) — the small
value objects passed between the pipeline and the service.

### [templates.py](templates.py) — canonical messages
The exact refusal/redaction strings (`REFUSAL_NO_CONTEXT`, `REFUSAL_OFF_TOPIC`,
`REFUSAL_INJECTION`, `REFUSAL_GENERIC`, `ERROR_GENERIC`, …). Centralized so every refusal
looks identical and is easy to tune/translate. `REFUSAL_NO_CONTEXT` is the canonical "I
don't have that information" used as the refusal sentinel across the service.

### [__init__.py](__init__.py)
Exposes `get_pipeline`. *(Begins with a UTF-8 BOM — keep encoding.)*

---

## Sub-packages
- **[input/](input/)** — L1: `normalize`, `injection`, `pii`, `scope`.
- **[retrieval/](retrieval/)** — L2: `grounding_gate`, `source_allowlist`, `cypher_safe`.
- **[output/](output/)** — L4: `leakage`, `citation_verify`, `grounding_check`, `pii_out`, `safety`.
- **[audit/](audit/)** — L5: `logger`, `abuse`.

## Dependencies at a glance
- **Internal:** `graph_rag.embeddings` (scope + injection + grounding use the embedder),
  `graph_rag.config` (audit), `observability` (metrics).
- **Cross-coupling into RAG:** `graph_rag.retrieval.hybrid_retriever` calls
  `guardrails.input.injection.sanitize_context`, and `graph_rag.retrieval.graph_retriever`
  calls `guardrails.retrieval.cypher_safe` — defence runs *inside* retrieval too.
