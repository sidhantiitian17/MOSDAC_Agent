"""LLM query decomposition for guided multi-hop retrieval (Phase 6).

A complex MOSDAC question often bundles several hops ("which sensors on
Oceansat-2 measure sea-surface parameters, and at what resolution?"). Answering
it well means retrieving for each hop, not embedding the whole sentence as one
vector. This planner asks the chat LLM to split the question into atomic
sub-questions and to name the anchor entities to traverse from.

Decomposition is opt-in (ENABLE_QUERY_DECOMPOSITION) because it adds an LLM call
before retrieval; when disabled, callers fall back to single-question retrieval.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from graph_rag.config import settings

logger = logging.getLogger(__name__)

_PLANNER_SYSTEM = """You analyze a user's question about satellites, sensors, and \
Earth-observation data (ISRO / MOSDAC domain).

Return ONLY minified JSON, no prose, no markdown:
{"subquestions":["..."],"anchors":["..."],"multihop":true}

Rules:
- subquestions: 1-N atomic questions that together answer the original. If the
  question is already atomic, return it unchanged as the single subquestion.
- anchors: named entities to look up (satellites, sensors, products, parameters). [] if none.
- multihop: true if answering needs linking 2 or more facts/entities."""


def _loads_obj(raw: str) -> dict | None:
    """Parse the first balanced JSON object from a model response."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


@dataclass
class QueryPlan:
    original: str
    sub_questions: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    multihop: bool = False


class QueryPlanner:
    """Breaks a question into sub-questions + anchor entities via the chat LLM."""

    def __init__(self, llm=None):
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            from graph_rag.llm.tabby_client import get_llm

            self._llm = get_llm()
        return self._llm

    def decompose(self, question: str) -> QueryPlan:
        q = (question or "").strip()
        if not q:
            return QueryPlan(original=question or "", sub_questions=[], anchors=[], multihop=False)

        data = None
        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            resp = self._get_llm().invoke(
                [
                    SystemMessage(content=_PLANNER_SYSTEM),
                    HumanMessage(content=f"Question: {q}\nJSON:"),
                ]
            )
            raw = getattr(resp, "content", str(resp))
            data = _loads_obj(raw)
        except Exception as exc:
            logger.info("Query decomposition failed (%s); using question as-is.", exc)

        if not data:
            return QueryPlan(original=q, sub_questions=[q], anchors=[], multihop=False)

        subs = [str(s).strip() for s in data.get("subquestions", []) if str(s).strip()]
        subs = subs[: settings.max_subquestions] or [q]
        anchors = [str(a).strip() for a in data.get("anchors", []) if str(a).strip()]
        multihop = bool(data.get("multihop", len(subs) > 1))
        return QueryPlan(original=q, sub_questions=subs, anchors=anchors, multihop=multihop)
