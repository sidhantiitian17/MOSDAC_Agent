"""Short conversation-title generation.

When a user sends the FIRST message of a NEW conversation, the backend generates
the answer and then — off the request path (FastAPI ``BackgroundTasks``) — asks the
LLM for a 4-5 word title and stores it on the conversation, so the history sidebar
shows something meaningful instead of "New chat". One small LLM call; failure-safe
(a hiccup just leaves the default title in place and never fails the request).

Reuses the shared Tabby client (``get_llm`` + ``llm_slot``) so it respects the same
concurrency cap as every other call to the single LLM endpoint.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "New chat"
_MAX_TITLE_CHARS = 60
_MAX_TITLE_WORDS = 8

_TITLE_SYSTEM = (
    "You generate a short title for a conversation list. Summarize the user's "
    "query into a concise 4-5 word title in Title Case. Return ONLY the title — "
    "no quotes, no punctuation at the end, no preamble."
)


def _clean_title(raw: str) -> str:
    """Collapse the LLM output into a single tidy title line."""
    text = (raw or "").strip()
    # Keep only the first line; models sometimes add explanation below.
    text = text.splitlines()[0] if text else ""
    text = text.strip().strip("\"'").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".!?,:;")
    if not text:
        return DEFAULT_TITLE
    words = text.split(" ")
    if len(words) > _MAX_TITLE_WORDS:
        text = " ".join(words[:_MAX_TITLE_WORDS])
    return text[:_MAX_TITLE_CHARS].strip() or DEFAULT_TITLE


class ConversationTitler:
    """Generates a short title from a user query via one LLM call."""

    def __init__(self, llm=None) -> None:
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from graph_rag.llm.tabby_client import get_llm

            self._llm = get_llm()
        return self._llm

    def make_title(self, question: str, answer: str = "") -> str:
        """Return a short title for ``question``. Never raises — falls back to
        :data:`DEFAULT_TITLE` on any error."""
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from graph_rag.llm.tabby_client import llm_slot

            with llm_slot():
                resp = self._get_llm().invoke(
                    [
                        SystemMessage(content=_TITLE_SYSTEM),
                        HumanMessage(
                            content=(
                                "Summarize this user query into a short 4-5 word "
                                f"title:\n'{question}'"
                            )
                        ),
                    ]
                )
            raw = getattr(resp, "content", str(resp))
            return _clean_title(raw)
        except Exception as exc:  # noqa: BLE001 — titling is best-effort
            logger.info("Title generation failed (%s); keeping default title.", exc)
            return DEFAULT_TITLE
