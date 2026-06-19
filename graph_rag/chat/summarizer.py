"""Rolling conversation summary for long-term memory (opt-in).

The recent-turn window keeps only the last N turns verbatim. When older turns
are about to be evicted, ConversationSummarizer folds them into a running
summary so the assistant still "remembers" what was discussed earlier without
carrying the full transcript in every prompt. One small LLM call, invoked only
on overflow.

Failure-safe: if the LLM is unreachable, it degrades to appending a compact
transcript of the evicted turns rather than losing them.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_SUMMARIZER_SYSTEM = (
    "You maintain a running summary of a conversation between a user and a "
    "MOSDAC (satellite / Earth-observation) assistant. Given the PREVIOUS "
    "SUMMARY and the NEW TURNS being archived, produce an updated summary that "
    "preserves entities discussed (satellites, sensors, products, parameters), "
    "the user's intent, and any facts already established. Be concise — a few "
    "sentences. Return ONLY the updated summary, no preamble."
)


class ConversationSummarizer:
    """Folds evicted turns into a running summary via one LLM call."""

    def __init__(self, llm=None):
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from graph_rag.llm.tabby_client import get_llm

            self._llm = get_llm()
        return self._llm

    @staticmethod
    def _format_turns(turns: List[Dict[str, Any]]) -> str:
        lines = []
        for t in turns:
            role = "User" if t.get("role") == "user" else "Assistant"
            content = t.get("content")
            content = content if isinstance(content, str) else "[image]"
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def update(self, running_summary: str, evicted_turns: List[Dict[str, Any]]) -> str:
        """Return an updated summary folding ``evicted_turns`` in. Failure-safe."""
        if not evicted_turns:
            return running_summary or ""
        new_block = self._format_turns(evicted_turns)
        prev = (running_summary or "").strip()
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            resp = self._get_llm().invoke(
                [
                    SystemMessage(content=_SUMMARIZER_SYSTEM),
                    HumanMessage(
                        content=f"PREVIOUS SUMMARY:\n{prev or '(none)'}\n\n"
                        f"NEW TURNS:\n{new_block}\n\nUpdated summary:"
                    ),
                ]
            )
            updated = getattr(resp, "content", str(resp)).strip()
            return updated or prev
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, never lose context
            logger.info("Conversation summary update failed (%s); appending transcript.", exc)
            return f"{prev}\n{new_block}".strip() if prev else new_block
