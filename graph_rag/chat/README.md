# `graph_rag/chat/` — The Stateful Chatbot (CLI) & Memory

This package provides the **multi-turn chatbot** used by the `python main.py chat` REPL,
plus the **rolling conversation summarizer** that keeps long conversations within the LLM's
context window.

> Note: the HTTP API does **not** use `GraphRagChatbot` — the gateway has its own
> orchestration in [chat_api/service.py](../../chat_api/service.py). This package is the
> CLI/embedded path and the home of the summarizer that both paths can use.

---

## File-by-file

### [chatbot.py](chatbot.py) — `GraphRagChatbot`
A stateful wrapper that keeps a **rolling buffer of recent turns** and runs each user
message through the RAG chain with that history as context. Chooses the standard chain or
the iterative reasoner based on config, and folds overflow turns into a summary.
- **Key pieces:** `GraphRagChatbot` with `chat(message)` / `reset()`; `ChatTurn`.
- **Depends on:** `config`, `chain.graph_rag_chain` (`build_graph_rag_chain`),
  `chain.iterative_chain` (`build_iterative_chain`), `chat.summarizer`,
  `retrieval.hybrid_retriever`.
- **Used by:** [main.py](../../main.py) `cmd_chat`, and the eval harness
  ([eval/harness.py](../eval/harness.py)).

### [summarizer.py](summarizer.py) — `ConversationSummarizer`
Maintains a **rolling summary** so older turns aren't lost when they fall out of the recent
window. Opt-in (`ENABLE_CONVERSATION_SUMMARY`); adds one LLM call only on overflow.
- **Key method:** `update(existing_summary, overflow_turns) -> new_summary`.
- **Depends on:** `llm.tabby_client` (`get_llm`).
- **Used by:** `chatbot.py` **and** [chat_api/service.py](../../chat_api/service.py)
  (`_remember_overflow`), so the API and CLI share the same memory logic.

### [__init__.py](__init__.py)
Re-exports `GraphRagChatbot`.

---

## How memory works

```
turns: [t1 t2 t3 ... tN]
   when len(turns) exceeds the recent window:
       overflow = oldest turns
       summary  = summarizer.update(summary, overflow)   ← one LLM call
       keep only the most recent `SUMMARY_KEEP_RECENT_TURNS`
   prompt history = "Summary of earlier conversation: ..." + recent turns
```

This keeps prompts bounded (and cheaper) while preserving long-term context.

## Dependencies at a glance
- **Internal:** `graph_rag.config`, `graph_rag.chain`, `graph_rag.retrieval`, `graph_rag.llm`.
- **External:** `langchain-core`.
