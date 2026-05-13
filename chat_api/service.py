"""Chat service — pure business logic, decoupled from FastAPI.

This layer can be unit-tested without spinning up the HTTP server, and reused
by any transport (FastAPI, gRPC, CLI, etc.).
"""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from chat_api.config import chat_api_settings
from chat_api.session import SessionStore

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

    def _build_history_prefix(self, session_id: str) -> str:
        turns = self._sessions.get(session_id)
        if not turns:
            return ""
        lines: List[str] = []
        for t in turns:
            role = "User" if t["role"] == "user" else "Assistant"
            content = t["content"] if isinstance(t["content"], str) else "[image]"
            lines.append(f"{role}: {content}")
        return "Conversation so far:\n" + "\n".join(lines) + "\n\nNew question: "

    def _validate_screenshot(self, screenshot_b64: str) -> None:
        """Reject screenshots over the size limit before they hit the LLM."""
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
    ) -> str:
        """Multimodal path — retrieve text RAG context, then call VL LLM directly."""
        self._validate_screenshot(screenshot_b64)

        ctx = self._retriever.retrieve(message)
        rag_preamble = (
            f"KNOWLEDGE GRAPH:\n{ctx['graph_context']}\n\n"
            f"DOCUMENT PASSAGES:\n{ctx['vector_context']}\n\n"
            f"User question about the attached screenshot: {message}"
        )

        history_prefix = self._build_history_prefix(session_id)
        content: List[Dict[str, Any]] = []
        if history_prefix:
            content.append({"type": "text", "text": history_prefix})
        content.append({"type": "text", "text": rag_preamble})
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{screenshot_b64}"},
        })

        response = self._llm.invoke([HumanMessage(content=content)])
        return response.content if hasattr(response, "content") else str(response)

    def _answer_text_only(self, message: str, session_id: str) -> str:
        history_prefix = self._build_history_prefix(session_id)
        return self._chain.invoke({"question": message, "history": history_prefix})

    def chat(
        self,
        session_id: str,
        message: str,
        screenshot_b64: Optional[str] = None,
        screenshot_mime: Optional[str] = "image/png",
    ) -> str:
        """Entry point — returns the assistant answer and updates history."""
        self._sessions.trim(session_id, self._max_history)

        if screenshot_b64:
            answer = self._answer_with_image(
                message=message,
                screenshot_b64=screenshot_b64,
                mime=screenshot_mime or "image/png",
                session_id=session_id,
            )
        else:
            answer = self._answer_text_only(message, session_id)

        self._sessions.append(session_id, "user", message)
        self._sessions.append(session_id, "assistant", answer)
        return answer

    def clear_session(self, session_id: str) -> None:
        self._sessions.clear(session_id)
