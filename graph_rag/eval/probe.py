"""Pipeline capture probe (evaluation_plan.md §5).

The cardinal rule of the runner is **capture, don't re-retrieve**: the contexts we
score must be the exact passages the LLM saw, not a fresh retrieval that may differ.

We achieve this without touching production code by wrapping the real retriever in a
``RecordingRetriever`` that delegates every call but remembers the last context it
returned. Because ``ChatService._answer_text_only`` retrieves exactly once for the
graded question (the chain reuses that context via ``pre_retrieved``), the recorded
context is precisely what fed the LLM.

``capture_turn`` runs an item end-to-end through ``ChatService.chat`` (replaying any
``setup`` turns for follow-ups first) and returns everything the metrics need. It is
written against the duck-typed ``service`` / ``recorder`` interface so tests can drive
it with fakes and no live Neo4j/Tabby.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from graph_rag.eval.dataset import GoldenItem

logger = logging.getLogger(__name__)


class RecordingRetriever:
    """Transparent proxy over a real retriever that records its last result.

    Delegates ``retrieve`` (and any other attribute) to the wrapped retriever so it
    is a drop-in for ``HybridRetriever`` everywhere ChatService/chain use it.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last: dict | None = None

    def retrieve(self, query: str, *args, **kwargs) -> dict:
        ctx = self._inner.retrieve(query, *args, **kwargs)
        self.last = ctx
        return ctx

    def reset(self) -> None:
        self.last = None

    @property
    def last_contexts(self) -> list[str]:
        """Text of the hits from the last retrieval (what the LLM was given)."""
        if not self.last:
            return []
        hits = self.last.get("_hits", []) or []
        return [getattr(h, "text", "") for h in hits if getattr(h, "text", "")]

    def __getattr__(self, name: str) -> Any:
        # Forward anything we don't override (e.g. ``as_context``) to the inner retriever.
        return getattr(self._inner, name)


@dataclass
class CapturedTurn:
    id: str
    stratum: str
    answerable: bool
    user_input: str
    answer: str = ""
    refused: bool = False
    grounded: bool = False
    retrieved_contexts: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def build_probe_service(*, raw_overrides: dict | None = None):
    """Construct a ChatService identical to production but with a RecordingRetriever.

    Returns ``(service, recorder)``. Live — needs Chroma/Neo4j/Tabby. ``raw_overrides``
    is accepted for symmetry but guard-config flipping is handled by the runner's
    ``guard_config_override`` so it also covers the L1/L2 paths.
    """
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore
    from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
    from graph_rag.llm.tabby_client import get_llm
    from graph_rag.retrieval.hybrid_retriever import HybridRetriever

    recorder = RecordingRetriever(HybridRetriever())
    chain = build_graph_rag_chain(retriever=recorder)
    service = ChatService(
        retriever=recorder,
        chain=chain,
        llm=get_llm(),
        sessions=InMemorySessionStore(),
    )
    return service, recorder


def capture_turn(service, recorder, item: GoldenItem) -> CapturedTurn:
    """Run one golden item through the service and capture the graded output.

    Replays ``setup`` turns first (for follow-ups), then resets the recorder so the
    captured context belongs to the *graded* question, not a setup turn.
    """
    session_id = f"eval-{uuid.uuid4().hex[:12]}"
    captured = CapturedTurn(
        id=item.id, stratum=item.stratum, answerable=item.answerable, user_input=item.user_input
    )
    try:
        for prior in item.setup:
            service.chat(session_id=session_id, message=prior)
        # Reset AFTER setup so last_contexts reflects only the graded question.
        if hasattr(recorder, "reset"):
            recorder.reset()

        answer, citations, grounded, refused = service.chat(
            session_id=session_id, message=item.user_input
        )
        captured.answer = answer or ""
        captured.citations = list(citations or [])
        captured.grounded = bool(grounded)
        captured.refused = bool(refused)
        # If L1 refused before retrieval, last_contexts is empty — correct (true refusal).
        captured.retrieved_contexts = list(getattr(recorder, "last_contexts", []) or [])
    except Exception as exc:  # never let one bad item abort the whole run
        logger.warning("capture failed for %s: %s", item.id, exc)
        captured.error = str(exc)
    finally:
        try:
            service.clear_session(session_id)
        except Exception:
            pass
    return captured


def capture_all(service, recorder, items: list[GoldenItem]) -> list[CapturedTurn]:
    return [capture_turn(service, recorder, it) for it in items]
