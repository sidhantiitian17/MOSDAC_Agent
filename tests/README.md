# `tests/` — The Test Suite

The automated **pytest** suite (44 test modules) plus the fixtures, guardrail attack
corpora, and the golden evaluation dataset. Tests are the safety net: they let a newcomer
change code with confidence, and they gate CI.

```bash
pytest -q                                  # full suite
pytest tests/test_chat_api.py -v           # one module, verbose
pytest tests/test_pipeline_security.py -v  # the guardrails
```

> **Tests that need live services auto-skip.** Anything requiring a running
> Neo4j / Ollama / Tabby is skipped when those services are absent (see
> [conftest.py](conftest.py)), so the suite runs **green in CI** without them. To run those
> paths for real, start the services (see [install.md](../install.md)) and re-run.

---

## Layout

```
tests/
├── conftest.py              # shared fixtures + service-availability skips
├── __init__.py
├── test_*.py                # 44 unit/integration modules (see the map below)
├── verify_features.py       # scripted end-to-end feature verification
├── guardrails/              # guardrail tests + ATTACK CORPORA (data files)
│   ├── test_guardrails.py
│   ├── injection_corpus.txt     # known prompt-injection phrases (used by L1 + tests)
│   ├── pii_corpus.txt           # PII samples
│   ├── offtopic_corpus.txt      # off-topic samples for the scope gate
│   └── hallucination_probes.txt # probes that try to elicit ungrounded answers
└── eval/                    # evaluation data
    ├── multihop_questions.yaml
    └── golden/v1/           # the RAGAS golden dataset (see its own README)
        ├── from_seed.jsonl · formula_numeric.jsonl · negatives.jsonl
        └── README.md        # ← golden-dataset schema & provenance (kept)
```

---

## What each area covers (map to the code it tests)

**Ingestion & preprocessing** — [test_loader.py](test_loader.py),
[test_formats.py](test_formats.py), [test_splitter.py](test_splitter.py),
[test_manifest.py](test_manifest.py), [test_text_features.py](test_text_features.py),
[test_pipeline.py](test_pipeline.py), [test_quality_gate.py](test_quality_gate.py).

**Embeddings & vector store** — [test_embeddings.py](test_embeddings.py),
[test_ollama_embedder.py](test_ollama_embedder.py), [test_chroma.py](test_chroma.py),
[test_chroma_store.py](test_chroma_store.py).

**Knowledge graph** — [test_extractor.py](test_extractor.py),
[test_llm_extractor.py](test_llm_extractor.py), [test_ontology.py](test_ontology.py),
[test_quantity_parser.py](test_quantity_parser.py), [test_resolver.py](test_resolver.py),
[test_neo4j.py](test_neo4j.py), [test_kg_integration.py](test_kg_integration.py).

**Retrieval & chains** — [test_retrieval.py](test_retrieval.py),
[test_retrieval_boost.py](test_retrieval_boost.py),
[test_query_planner.py](test_query_planner.py),
[test_query_contextualizer.py](test_query_contextualizer.py),
[test_chain.py](test_chain.py), [test_iterative_chain.py](test_iterative_chain.py).

**Chat / conversation** — [test_chatbot.py](test_chatbot.py),
[test_conversation_repo.py](test_conversation_repo.py),
[test_conversation_summary.py](test_conversation_summary.py),
[test_titler.py](test_titler.py).

**API & auth** — [test_chat_api.py](test_chat_api.py), [test_auth.py](test_auth.py).

**Guardrails & security** — [test_pipeline_security.py](test_pipeline_security.py),
[test_grounding_enforcement.py](test_grounding_enforcement.py),
[guardrails/test_guardrails.py](guardrails/test_guardrails.py),
[test_production_hardening.py](test_production_hardening.py).

**Evaluation** — [test_eval_dataset.py](test_eval_dataset.py),
[test_eval_harness.py](test_eval_harness.py), [test_eval_runner.py](test_eval_runner.py),
[test_eval_stats.py](test_eval_stats.py), [test_eval_probe.py](test_eval_probe.py),
[test_eval_scorecard.py](test_eval_scorecard.py),
[test_eval_custom_metrics.py](test_eval_custom_metrics.py).

**Drupal** — [test_drupal_pipeline_integration.py](test_drupal_pipeline_integration.py).

---

## Data files double as runtime inputs

The `guardrails/*.txt` corpora are not just test fixtures — `injection_corpus.txt` is also
read **at runtime** by the L1 injection guard
([guardrails/input/injection.py](../guardrails/input/injection.py)) as the known-attack
phrase set (`GUARD_INJECTION_CORPUS_PATH`). So updating that file improves both the tests
and live detection.

The `eval/golden/v1/` dataset is consumed by the RAGAS gate
([graph_rag/eval/dataset.py](../graph_rag/eval/dataset.py)); its schema and provenance are
documented in [eval/golden/v1/README.md](eval/golden/v1/README.md).

---

## Related
- End-to-end one-file ingest smoke test: [../test_main.py](../test_main.py) (run **inside**
  the `chat_api` container — it needs to write Chroma and reach Ollama).
- CI runs this suite + a pip-audit dependency scan + a Trivy image scan:
  [../.github/workflows/ci.yml](../.github/workflows/ci.yml).
- Evaluation methodology: [../evaluation_plan.md](../evaluation_plan.md).
