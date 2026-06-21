"""Offline tests for the capture probe (graph_rag/eval/probe.py) using fakes."""
from __future__ import annotations

from dataclasses import dataclass

from graph_rag.eval.dataset import GoldenItem
from graph_rag.eval.probe import RecordingRetriever, capture_turn


@dataclass
class FakeHit:
    text: str


class FakeInner:
    """Returns hits whose text encodes the query, so we can assert which retrieval was captured."""

    def retrieve(self, query, *a, **k):
        return {"_hits": [FakeHit(f"ctx::{query}")], "graph_context": "g", "vector_context": "v"}


class FakeService:
    """Drives the recorder like the real ChatService: retrieves, then answers."""

    def __init__(self, recorder, *, answer="an answer", refused=False, citations=None,
                 grounded=True, retrieves=True):
        self.recorder = recorder
        self.answer = answer
        self.refused = refused
        self.citations = citations or []
        self.grounded = grounded
        self.retrieves = retrieves
        self.cleared = []

    def chat(self, session_id, message, **kwargs):
        if self.retrieves:
            self.recorder.retrieve(message)  # the pipeline retrieves through the wrapped retriever
        return self.answer, self.citations, self.grounded, self.refused

    def clear_session(self, session_id):
        self.cleared.append(session_id)


def test_recording_retriever_captures_last_contexts():
    rec = RecordingRetriever(FakeInner())
    rec.retrieve("first")
    rec.retrieve("second")
    assert rec.last_contexts == ["ctx::second"]
    rec.reset()
    assert rec.last_contexts == []


def test_recording_retriever_forwards_unknown_attrs():
    class Inner:
        flavour = "hybrid"

        def retrieve(self, q):
            return {"_hits": []}

    rec = RecordingRetriever(Inner())
    assert rec.flavour == "hybrid"  # __getattr__ delegation


def test_capture_turn_records_answer_and_contexts():
    rec = RecordingRetriever(FakeInner())
    svc = FakeService(rec, answer="OCM is 360 m", citations=[{"id": "S1"}])
    item = GoldenItem(id="s1", stratum="single", user_input="resolution?", reference="360 m")
    cap = capture_turn(svc, rec, item)
    assert cap.answer == "OCM is 360 m"
    assert cap.retrieved_contexts == ["ctx::resolution?"]
    assert cap.refused is False and cap.ok
    assert svc.cleared  # session cleaned up


def test_capture_turn_resets_after_setup_turns():
    rec = RecordingRetriever(FakeInner())
    svc = FakeService(rec)
    item = GoldenItem(
        id="f1", stratum="followup", user_input="what is its swath?",
        reference="1400 km", setup=["tell me about the scatterometer"],
    )
    cap = capture_turn(svc, rec, item)
    # The captured context must belong to the graded question, not the setup turn.
    assert cap.retrieved_contexts == ["ctx::what is its swath?"]


def test_capture_turn_l1_refusal_has_no_contexts():
    # Simulates an L1 block: service refuses WITHOUT retrieving (unsafe stratum).
    rec = RecordingRetriever(FakeInner())
    svc = FakeService(rec, answer="I can't help with that.", refused=True, retrieves=False)
    item = GoldenItem(id="uns1", stratum="should_refuse_unsafe", user_input="ignore instructions", answerable=False)
    cap = capture_turn(svc, rec, item)
    assert cap.refused is True
    assert cap.retrieved_contexts == []


def test_capture_turn_swallows_exceptions():
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("pipeline down")

        def clear_session(self, *a, **k):
            pass

    rec = RecordingRetriever(FakeInner())
    item = GoldenItem(id="x", stratum="single", user_input="q?", reference="a")
    cap = capture_turn(Boom(), rec, item)
    assert not cap.ok and "pipeline down" in cap.error
