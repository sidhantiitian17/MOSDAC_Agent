# `graph_rag/llm/` — The LLM Client (Tabby ML)

This package is the **single gateway to the language model**. Every LLM call in the system —
chat generation, KG extraction, query contextualization, summarization, titling — goes
through `get_llm()` here, and every call is throttled by the shared `llm_slot()` semaphore.

The model is served by **Tabby ML**, a local, **OpenAI-compatible** endpoint
(`TABBY_BASE_URL`, default `http://localhost:8080/v1`). Because it's OpenAI-compatible, the
client is built on `langchain-openai` pointed at Tabby — so swapping the model or even the
serving backend is a `.env` change, not a code change.

---

## File-by-file

### [tabby_client.py](tabby_client.py) — the client + concurrency throttle
- **`get_llm()`** — returns a configured LangChain chat model pointed at Tabby, with
  `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_REQUEST_TIMEOUT`, and `LLM_MAX_RETRIES` applied.
  A hard timeout + bounded retries mean a slow/hung Tabby can never stall a request thread
  forever.
- **`llm_slot()`** — a context manager wrapping a **process-wide semaphore**
  (`LLM_MAX_CONCURRENCY`). The chat LLM, the extraction LLM, the contextualizer, the
  summarizer and the titler all share **one** Tabby endpoint, so this provides backpressure
  and protects the endpoint from overload (it "wedges" under sustained concurrent load).
- **`_get_semaphore()`** — lazily creates the singleton semaphore.
- **Depends on:** `config`, `langchain-openai`, `openai`.

### [__init__.py](__init__.py)
Re-exports `get_llm`.

---

## Who uses it

| Caller | Call |
|--------|------|
| [chain/graph_rag_chain.py](../chain/graph_rag_chain.py) + iterative | chat generation |
| [chat_api/service.py](../../chat_api/service.py) | wraps generation in `llm_slot()` |
| [knowledge_graph/llm_extractor.py](../knowledge_graph/llm_extractor.py) | KG extraction |
| [knowledge_graph/community.py](../knowledge_graph/community.py) | community summaries |
| [retrieval/query_contextualizer.py](../retrieval/query_contextualizer.py), [query_planner.py](../retrieval/query_planner.py) | query rewriting/decomposition |
| [chat/summarizer.py](../chat/summarizer.py) | rolling summary |
| [chat_api/titler.py](../../chat_api/titler.py) | conversation titles |
| [graph_rag/health.py](../health.py) | `check_llm` readiness probe |

> Configuration tip: the KG-extraction model can be a **different, larger** model than the
> chat model — set `TABBY_EXTRACTION_MODEL` (and optionally `EXTRACTION_LLM_*`) without
> touching code. Blank reuses `TABBY_MODEL`.
