"""Bounded iterative retrieve→reason→re-retrieve answering (Phase 7).

A single retrieval pass cannot follow a chain it never fetched. This reasoner
lets the LLM ask for more: after each attempt it may emit a line
"NEED_MORE: <entities>"; we re-retrieve seeded by those entities and try again,
up to MAX_REASONING_ITERATIONS. A final faithfulness pass checks that every
number in the answer appears in the retrieved context.

It exposes the same `invoke({"question","history"})` interface as the LCEL
chain, so GraphRagChatbot can use it as a drop-in replacement.
"""
from __future__ import annotations

import logging
import re

from graph_rag.config import settings

logger = logging.getLogger(__name__)

_NEED_MORE_RE = re.compile(r"NEED_MORE:\s*(.+)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d")
_CONTEXT_CAP = 8000  # keep accumulated context bounded across iterations

_PROTOCOL = (
    "\n\nMULTI-HOP PROTOCOL:\n"
    "- The KNOWLEDGE GRAPH lines are typed directed facts: (A) -[REL]-> (B). "
    "Chain them step by step for multi-hop questions and show the reasoning path.\n"
    "- If the context is missing a link you need to answer, reply with EXACTLY one line:\n"
    "  NEED_MORE: <comma-separated entity or term names to look up>\n"
    "  and output nothing else.\n"
    "- Otherwise, answer fully and do NOT output NEED_MORE."
)


class IterativeReasoner:
    """Multi-pass grounded answering over the hybrid retriever."""

    def __init__(self, retriever=None, llm=None, max_iterations: int | None = None, contextualizer=None):
        self._retriever = retriever
        self._llm = llm
        self._max = max_iterations or settings.max_reasoning_iterations
        self._contextualizer = contextualizer

    def _get_retriever(self):
        if self._retriever is None:
            from graph_rag.retrieval.hybrid_retriever import HybridRetriever

            self._retriever = HybridRetriever()
        return self._retriever

    def _get_llm(self):
        if self._llm is None:
            from graph_rag.llm.tabby_client import get_llm

            self._llm = get_llm()
        return self._llm

    def _contextualize(self, question: str, history: str) -> str:
        """Rewrite a follow-up into a standalone search query (failure-safe)."""
        try:
            if self._contextualizer is None:
                from graph_rag.retrieval.query_contextualizer import QueryContextualizer

                self._contextualizer = QueryContextualizer()
            return self._contextualizer.contextualize(question, history).search_query
        except Exception:
            return question

    # Drop-in chain interface.
    def invoke(self, payload: dict) -> str:
        return self.answer(payload.get("question", ""), payload.get("history", ""))

    def answer(self, question: str, history: str = "") -> str:
        retriever = self._get_retriever()
        search_query = self._contextualize(question, history)
        ctx = retriever.retrieve(search_query)
        graph_ctx = ctx.get("graph_context", "")
        vector_ctx = ctx.get("vector_context", "")
        seen_terms: set[str] = set()

        answer = ""
        iterations = max(1, self._max)
        for i in range(iterations):
            answer = self._ask(question, history, graph_ctx, vector_ctx)
            match = _NEED_MORE_RE.search(answer)
            if not match or i == iterations - 1:
                answer = _NEED_MORE_RE.sub("", answer).strip()
                break
            terms = [t.strip() for t in re.split(r"[,;]", match.group(1)) if t.strip()]
            new_terms = [t for t in terms if t.lower() not in seen_terms]
            if not new_terms:
                answer = _NEED_MORE_RE.sub("", answer).strip()
                break
            for t in new_terms:
                seen_terms.add(t.lower())
            logger.info("Iterative reasoning: re-retrieving for %s", new_terms)
            extra = retriever.retrieve(" ".join(new_terms))
            graph_ctx = self._merge(graph_ctx, extra.get("graph_context", ""))
            vector_ctx = self._merge(vector_ctx, extra.get("vector_context", ""))

        if settings.enable_faithfulness_check and _NUMBER_RE.search(answer):
            answer = self._self_check(answer, graph_ctx, vector_ctx)
        return answer or (
            "I do not have enough information in my knowledge base to answer that."
        )

    def _ask(self, question: str, history: str, graph_ctx: str, vector_ctx: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        from graph_rag.chain.graph_rag_chain import _load_system_prompt

        system = _load_system_prompt()
        try:
            system = system.format(graph_context=graph_ctx, vector_context=vector_ctx)
        except (KeyError, IndexError, ValueError):
            system = f"{system}\n\nKNOWLEDGE GRAPH:\n{graph_ctx}\n\nDOCUMENT PASSAGES:\n{vector_ctx}"
        system = system + _PROTOCOL
        human = f"{history}{question}" if history else question
        resp = self._get_llm().invoke(
            [SystemMessage(content=system), HumanMessage(content=human)]
        )
        return getattr(resp, "content", str(resp)).strip()

    def _self_check(self, answer: str, graph_ctx: str, vector_ctx: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        sys = (
            "You verify factual grounding. Given CONTEXT and a DRAFT answer, ensure every "
            "number in the DRAFT appears in the CONTEXT. Remove or correct any number not "
            "supported by the CONTEXT. Return ONLY the corrected answer, no commentary."
        )
        context = f"CONTEXT:\n{graph_ctx}\n{vector_ctx}"[:_CONTEXT_CAP]
        try:
            resp = self._get_llm().invoke(
                [
                    SystemMessage(content=sys),
                    HumanMessage(content=f"{context}\n\nDRAFT:\n{answer}\n\nCorrected answer:"),
                ]
            )
            corrected = getattr(resp, "content", str(resp)).strip()
            # Guard: a weak verifier model can mangle a good answer (e.g. return a
            # bare entity name). Only accept the correction if it is substantive —
            # non-empty and not a drastic truncation of the draft.
            if not corrected or len(corrected) < 0.5 * len(answer):
                return answer
            return corrected
        except Exception as exc:
            logger.debug("Faithfulness check failed: %s", exc)
            return answer

    @staticmethod
    def _merge(a: str, b: str) -> str:
        if not b or b in a:
            return a
        return f"{a}\n{b}".strip()[:_CONTEXT_CAP]


def build_iterative_chain(retriever=None, llm=None) -> IterativeReasoner:
    """Factory mirroring build_graph_rag_chain — returns an .invoke-able reasoner."""
    return IterativeReasoner(retriever=retriever, llm=llm)
