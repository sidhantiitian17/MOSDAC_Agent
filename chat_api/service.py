"""Chat service - pure business logic, decoupled from FastAPI.

This layer can be unit-tested without spinning up the HTTP server, and reused
by any transport (FastAPI, gRPC, CLI, etc.).

Guardrails integration (guardplan.md):
    L1  check_input()            - before any retrieval/LLM spend
    L2  check_retrieval_groundable() - after retrieval, before LLM
    L4  check_output()           - after LLM, before return/store
    L5  log_request()            - audit every request
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from chat_api.config import chat_api_settings
from chat_api.session import SessionStore
from graph_rag.config import settings as graph_settings

logger = logging.getLogger(__name__)


class ChatService:
    """Coordinates retriever, chain, and LLM around a session store."""

    def __init__(
        self,
        retriever,
        chain,
        llm,
        sessions: SessionStore,
        max_history: Optional[int] = None,
    ) -> None:
        self._retriever = retriever
        self._chain = chain
        self._llm = llm
        self._sessions = sessions
        self._max_history = max_history if max_history is not None else chat_api_settings.max_history_turns
        self._contextualizer = None
        self._summarizer = None

    def _contextualize(self, message: str, history_prefix: str) -> str:
        try:
            if self._contextualizer is None:
                from graph_rag.retrieval.query_contextualizer import QueryContextualizer
                self._contextualizer = QueryContextualizer()
            return self._contextualizer.contextualize(message, history_prefix).search_query
        except Exception:
            return message

    def _get_summarizer(self):
        if self._summarizer is None:
            from graph_rag.chat.summarizer import ConversationSummarizer
            self._summarizer = ConversationSummarizer()
        return self._summarizer

    def _remember_overflow(self, session_id: str) -> None:
        if not graph_settings.enable_conversation_summary:
            self._sessions.trim(session_id, self._max_history)
            return
        keep = graph_settings.summary_keep_recent_turns
        turns = self._sessions.get(session_id)
        if len(turns) <= keep * 2:
            return
        overflow = turns[: len(turns) - keep * 2]
        summary = self._get_summarizer().update(self._sessions.get_summary(session_id), overflow)
        self._sessions.set_summary(session_id, summary)
        self._sessions.trim(session_id, keep)

    def _build_history_prefix(self, session_id: str) -> str:
        turns = self._sessions.get(session_id)
        summary = ""
        if graph_settings.enable_conversation_summary:
            summary = self._sessions.get_summary(session_id)
        if not turns and not summary:
            return ""
        lines: List[str] = []
        if summary:
            lines.append(f"Summary of earlier conversation: {summary}")
        for t in turns:
            role = "User" if t["role"] == "user" else "Assistant"
            content = t["content"] if isinstance(t["content"], str) else "[image]"
            lines.append(f"{role}: {content}")
        return "Conversation so far:\n" + "\n".join(lines) + "\n\nNew question: "

    def _validate_screenshot(self, screenshot_b64: str) -> None:
        if not chat_api_settings.enable_screenshot:
            raise ValueError("Screenshot uploads are disabled in this deployment.")
        try:
            raw_size = (len(screenshot_b64) * 3) // 4
        except Exception:
            raw_size = 0
        if raw_size > chat_api_settings.max_screenshot_bytes:
            raise ValueError(
                f"Screenshot too large: {raw_size} bytes > "
                f"{chat_api_settings.max_screenshot_bytes} byte limit."
            )
        try:
            base64.b64decode(screenshot_b64[:256], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"Invalid base64 screenshot data: {exc}") from exc

    def _answer_with_image(
        self,
        message: str,
        screenshot_b64: str,
        mime: str,
        session_id: str,
    ) -> Tuple[str, List[dict]]:
        self._validate_screenshot(screenshot_b64)
        history_prefix = self._build_history_prefix(session_id)
        search_query = self._contextualize(message, history_prefix)
        ctx = self._retriever.retrieve(search_query)
        rag_preamble = (
            f"KNOWLEDGE GRAPH:\n{ctx['graph_context']}\n\n"
            f"DOCUMENT PASSAGES:\n{ctx['vector_context']}\n\n"
            f"User question about the attached screenshot: {message}"
        )
        content: List[Dict[str, Any]] = []
        if history_prefix:
            content.append({"type": "text", "text": history_prefix})
        content.append({"type": "text", "text": rag_preamble})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{screenshot_b64}"},
        })
        response = self._llm.invoke([HumanMessage(content=content)])
        answer = response.content if hasattr(response, "content") else str(response)
        return answer, []

    def _answer_text_only(
        self,
        message: str,
        session_id: str,
    ) -> Tuple[str, List[dict], bool, float]:
        """
        Returns (answer, citations, grounded, top_score).
        Performs pre-flight retrieval for grounding gate + citation registry,
        then invokes the chain (second retrieval inside chain is acceptable overhead).
        """
        from guardrails import get_pipeline
        from guardrails.templates import REFUSAL_NO_CONTEXT
        from graph_rag.config import settings as gs

        pipeline = get_pipeline()
        manifest_path = gs.ingest_manifest_path

        history_prefix = self._build_history_prefix(session_id)
        search_query = self._contextualize(message, history_prefix)

        # Pre-flight retrieval for grounding gate (L2)
        try:
            ctx = self._retriever.retrieve(search_query)
            hits = ctx.get("_hits", [])
        except Exception as exc:
            logger.warning("Pre-flight retrieval failed: %s", exc)
            return REFUSAL_NO_CONTEXT, [], False, 0.0

        passes, registry, top_score = pipeline.check_retrieval_groundable(hits, manifest_path)
        if not passes:
            return REFUSAL_NO_CONTEXT, [], False, top_score

        # Invoke the chain (handles prompt formatting + second retrieval internally)
        answer = self._chain.invoke({"question": message, "history": history_prefix})

        # L4 output guard
        passages = [h.text for h in hits]
        context = ctx.get("vector_context", "") + "\n" + ctx.get("graph_context", "")
        clean_answer, citations, _reasons = pipeline.check_output(answer, registry, passages, context)

        return clean_answer, citations, True, top_score

    def chat(
        self,
        session_id: str,
        message: str,
        screenshot_b64: Optional[str] = None,
        screenshot_mime: Optional[str] = "image/png",
    ) -> Tuple[str, List[dict], bool, bool]:
        """
        Entry point.
        Returns (answer, citations, grounded, refused).
        """
        from guardrails import get_pipeline
        from guardrails.audit.logger import log_request
        from guardrails.config import guardrail_settings as gcfg
        from guardrails.templates import REFUSAL_NO_CONTEXT

        pipeline = get_pipeline()
        start = time.monotonic()

        self._remember_overflow(session_id)

        # L1 Input guard
        decision = pipeline.check_input(message, session_id)
        if decision.is_refused:
            latency = (time.monotonic() - start) * 1000
            if gcfg.audit:
                log_request(
                    session_id=session_id,
                    action="refuse",
                    reason_codes=decision.reasons,
                    grounded=False,
                    refused=True,
                    top_score=0.0,
                    latency_ms=latency,
                    has_citations=False,
                )
            return decision.cleaned_text, [], False, True

        safe_message = decision.cleaned_text

        if screenshot_b64:
            answer, citations = self._answer_with_image(
                message=safe_message,
                screenshot_b64=screenshot_b64,
                mime=screenshot_mime or "image/png",
                session_id=session_id,
            )
            grounded, refused = True, False
            top_score = 1.0
        else:
            answer, citations, grounded, top_score = self._answer_text_only(safe_message, session_id)
            refused = (answer == REFUSAL_NO_CONTEXT)

        # Store only the PII-redacted message (L5)
        self._sessions.append(session_id, "user", safe_message)
        self._sessions.append(session_id, "assistant", answer)

        latency = (time.monotonic() - start) * 1000
        if gcfg.audit:
            log_request(
                session_id=session_id,
                action="refuse" if refused else "allow",
                reason_codes=[],
                grounded=grounded,
                refused=refused,
                top_score=top_score,
                latency_ms=latency,
                has_citations=bool(citations),
            )

        return answer, citations, grounded, refused

    def clear_session(self, session_id: str) -> None:
        self._sessions.clear(session_id)
