"""History-aware query contextualization for follow-up questions.

A follow-up like "what's its resolution?" is meaningless to a retriever on its
own — the entity it refers to lives in a previous turn. Before retrieval we
rewrite such a question into a standalone, self-contained search query using the
recent conversation, so the embedding and keyword search actually target the
right entity (Oceansat-2, OCM, …) instead of noise.

The rewrite is GATED: a cheap heuristic first decides whether the question even
looks like a follow-up. Only then do we spend one small LLM call. Self-contained
questions pass straight through unchanged. Any parse/LLM failure falls back to
the original question — contextualization never blocks answering.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from graph_rag.config import settings
from graph_rag.retrieval.query_planner import _loads_obj

logger = logging.getLogger(__name__)

_CONTEXTUALIZER_SYSTEM = """You rewrite a user's follow-up question into a single \
standalone search query for a satellite / Earth-observation knowledge base \
(ISRO / MOSDAC domain).

Use the conversation to resolve pronouns and ellipsis ("it", "that sensor", \
"there", "what about the resolution?") into explicit entity names.

Return ONLY minified JSON, no prose, no markdown:
{"standalone":"...","entities":["..."]}

Rules:
- standalone: ONE self-contained question answerable without the conversation.
  Keep the user's intent; only add the missing context (entity names, subject).
- entities: named entities the query refers to (satellites, sensors, products,
  parameters). [] if none.
- Do NOT answer the question. Only rewrite it."""

# Pronouns / openers that signal the question depends on earlier turns.
_PRONOUNS = {
    "it", "its", "that", "those", "these", "this", "they", "them",
    "their", "he", "she", "his", "her", "one", "ones", "there",
}
_FOLLOWUP_OPENERS = (
    "what about", "how about", "and ", "what else", "why", "how so",
    "which one", "the same", "compared to", "vs ", "versus",
)
_TOKEN_RE = re.compile(r"[a-zA-Z']+")
# A capitalized token (proper noun / acronym) suggests the question names its
# own subject and is probably self-contained.
_NAMED_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9\-]{1,}\b")
_MAX_SELF_CONTAINED_TOKENS = 6


@dataclass
class ContextualizedQuery:
    search_query: str
    carryover_entities: list[str] = field(default_factory=list)
    rewritten: bool = False


class QueryContextualizer:
    """Rewrites follow-ups into standalone search queries via a gated LLM call."""

    def __init__(self, llm=None):
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from graph_rag.llm.tabby_client import get_llm

            self._llm = get_llm()
        return self._llm

    @staticmethod
    def _looks_like_followup(question: str, history_text: str) -> bool:
        """Cheap gate — True only when the question likely depends on earlier turns."""
        q = (question or "").strip()
        if not q or not (history_text or "").strip():
            return False  # nothing to resolve against
        tokens = _TOKEN_RE.findall(q.lower())
        if not tokens:
            return False
        low = q.lower()
        # Explicit dependency signals: pronouns or follow-up openers.
        if any(t in _PRONOUNS for t in tokens):
            return True
        if low.startswith(_FOLLOWUP_OPENERS) or any(o in low for o in _FOLLOWUP_OPENERS):
            return True
        # Short and naming no entity of its own → likely elliptical follow-up.
        if len(tokens) <= _MAX_SELF_CONTAINED_TOKENS and not _NAMED_ENTITY_RE.search(q):
            return True
        return False

    def contextualize(self, question: str, history_text: str) -> ContextualizedQuery:
        """Return a standalone search query for ``question`` given the conversation."""
        q = (question or "").strip()
        if not settings.enable_query_contextualization:
            return ContextualizedQuery(search_query=q)
        if not self._looks_like_followup(q, history_text):
            return ContextualizedQuery(search_query=q)

        history = (history_text or "")[-settings.contextualizer_max_history_chars:]
        data = None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            resp = self._get_llm().invoke(
                [
                    SystemMessage(content=_CONTEXTUALIZER_SYSTEM),
                    HumanMessage(content=f"Conversation:\n{history}\n\nFollow-up: {q}\nJSON:"),
                ]
            )
            raw = getattr(resp, "content", str(resp))
            data = _loads_obj(raw)
        except Exception as exc:
            logger.info("Query contextualization failed (%s); using question as-is.", exc)

        if not data:
            return ContextualizedQuery(search_query=q)

        standalone = str(data.get("standalone", "")).strip() or q
        entities = [str(e).strip() for e in data.get("entities", []) if str(e).strip()]
        return ContextualizedQuery(
            search_query=standalone,
            carryover_entities=entities,
            rewritten=standalone != q,
        )
