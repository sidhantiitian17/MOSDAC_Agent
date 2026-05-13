"""Multi-turn chatbot. Maintains a rolling buffer of recent turns for context."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
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

    def __post_init__(self):
        if self.history.maxlen != self.window:
            self.history = deque(maxlen=self.window)
        if self.retriever is None:
            self.retriever = HybridRetriever()
        if self.chain is None:
            self.chain = build_graph_rag_chain(retriever=self.retriever)

    def _format_history(self) -> str:
        if not self.history:
            return ""
        return "\n".join(
            f"User: {t.user}\nAssistant: {t.assistant}" for t in self.history
        )

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
        self.history.append(ChatTurn(user=user_input, assistant=answer))
        return answer

    def reset(self) -> None:
        self.history.clear()
