"""Multi-turn chatbot. Maintains a rolling buffer of recent turns for context."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
from graph_rag.config import settings
from graph_rag.retrieval.hybrid_retriever import HybridRetriever

logger = logging.getLogger(__name__)


@dataclass
class ChatTurn:
    user: str
    assistant: str


@dataclass
class GraphRagChatbot:
    """Holds the chain and a sliding window of recent turns."""

    window: int = 10
    history: deque = field(default_factory=lambda: deque(maxlen=10))
    chain: object = None
    retriever: HybridRetriever | None = None
    summary: str = ""

    def __post_init__(self):
        self._summarizer = None  # lazy ConversationSummarizer (rolling memory)
        if self.history.maxlen != self.window:
            self.history = deque(maxlen=self.window)
        if self.retriever is None:
            self.retriever = HybridRetriever()
        if self.chain is None:
            if settings.enable_iterative_reasoning:
                # Phase 7: bounded retrieve→reason→re-retrieve loop + faithfulness check.
                from graph_rag.chain.iterative_chain import build_iterative_chain

                self.chain = build_iterative_chain(retriever=self.retriever)
            else:
                self.chain = build_graph_rag_chain(retriever=self.retriever)

    def _get_summarizer(self):
        if self._summarizer is None:
            from graph_rag.chat.summarizer import ConversationSummarizer

            self._summarizer = ConversationSummarizer()
        return self._summarizer

    def _format_history(self) -> str:
        parts: list[str] = []
        if settings.enable_conversation_summary and self.summary:
            parts.append(f"Summary of earlier conversation: {self.summary}")
        for t in self.history:
            parts.append(f"User: {t.user}\nAssistant: {t.assistant}")
        return "\n".join(parts)

    def _append_turn(self, turn: ChatTurn) -> None:
        """Append a turn; when the window is full, fold the evicted turn into the summary."""
        if (
            settings.enable_conversation_summary
            and self.history.maxlen
            and len(self.history) >= self.history.maxlen
        ):
            evicted = self.history[0]
            self.summary = self._get_summarizer().update(
                self.summary,
                [
                    {"role": "user", "content": evicted.user},
                    {"role": "assistant", "content": evicted.assistant},
                ],
            )
        self.history.append(turn)

    def chat(self, user_input: str) -> str:
        """Send a turn through the chain; append to history."""
        history_block = self._format_history()
        history_prefix = (
            f"Conversation so far:\n{history_block}\n\nNew question: "
            if history_block
            else ""
        )
        try:
            answer = self.chain.invoke({"question": user_input, "history": history_prefix})
        except Exception as exc:
            logger.exception("Chain invocation failed: %s", exc)
            answer = f"(error: {exc})"
        self._append_turn(ChatTurn(user=user_input, assistant=answer))
        return answer

    def reset(self) -> None:
        self.history.clear()
        self.summary = ""
