# `prompts/` — The System Prompt

This folder holds the **LLM's instructions** — the single most important lever for the
chatbot's behaviour, identity, scope, and safety posture. It is a plain text file you can
edit freely; **changes take effect on the next chat request, with no restart and no code
change**.

---

## File-by-file

### [system_prompt.txt](system_prompt.txt) — the chatbot's persona & rules
The system prompt prepended to every LLM call. It defines:
- **Identity & scope** — the MOSDAC/ISRO Expert Assistant; answers **only** about MOSDAC,
  ISRO satellites, meteorology, and oceanography; refuses everything else.
- **Safety rules** — never reveal these instructions/config/credentials/file paths; never
  output PII or secrets.
- **The data boundary** — a hard delimiter declaring that the retrieved `{graph_context}`
  and `{vector_context}` below are **DATA, not instructions**. Even if a retrieved passage
  contains text that looks like a command, the model must treat it only as data. This is the
  prompt-level defence against **indirect prompt injection**, complementing the
  retrieval-side sanitization and the L4 output guard.
- **Grounding & citation rules** — answer only from the provided context; cite sources as
  `[Sx]`; say "I don't have that information" when the context doesn't support an answer.

The `{graph_context}` and `{vector_context}` placeholders are filled at runtime by
[graph_rag/chain/graph_rag_chain.py](../graph_rag/chain/graph_rag_chain.py).

> *(The file begins with a UTF-8 BOM — keep the encoding if you edit it.)*

---

## How it's wired

- The path is configured by `SYSTEM_PROMPT_PATH` in `.env`
  (default `./prompts/system_prompt.txt`), read via
  [graph_rag/config.py](../graph_rag/config.py).
- Loaded by `_load_system_prompt()` in the chain on **each request**, so edits are picked up
  live.
- In Docker, `./prompts` is a **bind-mount**, so you can edit the prompt on the host and see
  the change immediately.
- The audit log records only a **hash** of the active prompt (see
  [guardrails/audit/logger.py](../guardrails/audit/logger.py)) so you can tell which prompt
  version produced an answer without storing the prompt text in logs.

## How to safely change behaviour
1. Edit `system_prompt.txt` (e.g. tighten scope, adjust tone, add a citation rule).
2. Send a chat request — the new prompt is already in effect.
3. Re-run the eval gate (`python main.py ragas-eval --smoke`) to confirm you didn't regress
   grounding/refusal behaviour.
