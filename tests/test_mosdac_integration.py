"""Integration tests — MOSDAC routes coexist with the existing chat_api.

Approach:
  * Build the existing chat_api app with mocked graph-RAG deps.
  * Mount the MOSDAC router with a fake `AgentRunner` that returns a
    deterministic reply (so we don't need Ollama / LangGraph).
  * Hit /chat (graph-RAG) and /mosdac/chat (agent) on the same app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@dataclass
class FakeRunner:
    """Drop-in for mosdac_agent.agent.AgentRunner — no LLM needed."""

    reply: str = "Order has been placed. Check your SFTP account.\nOrder ID: MOCK-TEST-1"
    calls: List[dict] = field(default_factory=list)

    def chat(self, thread_id: str, message: str) -> str:
        self.calls.append({"thread_id": thread_id, "message": message})
        return self.reply


@pytest.fixture
def integrated_client(monkeypatch):
    # Disable auto-mount: we hand-wire the agent service so no Ollama is needed.
    monkeypatch.setenv("MOSDAC_ENABLE_MOSDAC_ENDPOINT", "false")

    from chat_api.main import create_app
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore
    from mosdac_agent.agent import MosdacAgentService
    from mosdac_agent.routes import build_mosdac_router

    retriever = MagicMock()
    retriever.retrieve.return_value = {"graph_context": "g", "vector_context": "v"}
    chain = MagicMock()
    chain.invoke.return_value = "rag-answer"
    llm = MagicMock()
    sessions = InMemorySessionStore()
    rag_service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)

    runner = FakeRunner()
    mosdac_service = MosdacAgentService(runner=runner, sessions=sessions)

    app = create_app(service=rag_service)
    app.include_router(build_mosdac_router(mosdac_service))
    return TestClient(app), sessions, runner


def test_graph_rag_endpoint_still_works(integrated_client):
    client, _, _ = integrated_client
    r = client.post("/chat", json={"session_id": "u1", "message": "hi"})
    assert r.status_code == 200
    assert r.json()["answer"] == "rag-answer"


def test_mosdac_health_endpoint(integrated_client):
    client, _, _ = integrated_client
    r = client.get("/mosdac/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_mosdac_config_endpoint(integrated_client):
    client, _, _ = integrated_client
    r = client.get("/mosdac/config")
    assert r.status_code == 200
    body = r.json()
    assert "bot_name" in body
    assert "sftp_base_url" in body


def test_mosdac_chat_round_trip(integrated_client):
    client, sessions, runner = integrated_client
    r = client.post(
        "/mosdac/chat",
        json={
            "session_id": "u1",
            "message": "Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP",
        },
        headers={"X-MOSDAC-User": "test-user"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "u1"
    assert body["user"] == "test-user"
    assert body["answer"].startswith("Order has been placed.")
    assert runner.calls[0]["thread_id"] == "mosdac:u1"
    assert "INSAT-3D" in runner.calls[0]["message"]
    history = sessions.get("u1")
    assert any(t["role"] == "assistant" for t in history)


def test_mosdac_chat_clear_endpoint(integrated_client):
    client, sessions, _ = integrated_client
    client.post(
        "/mosdac/chat",
        json={"session_id": "u2", "message": "place something"},
    )
    assert sessions.get("u2")
    r = client.delete("/mosdac/chat/u2")
    assert r.status_code == 200
    assert sessions.get("u2") == []


def test_mosdac_chat_validates_input(integrated_client):
    client, _, _ = integrated_client
    r = client.post("/mosdac/chat", json={"session_id": "", "message": "hi"})
    assert r.status_code == 422


def test_mosdac_routes_isolated_from_rag_routes(integrated_client):
    """Both endpoints answer independently."""
    client, _, _ = integrated_client
    r1 = client.post("/chat", json={"session_id": "shared", "message": "tell me about INSAT"})
    r2 = client.post(
        "/mosdac/chat",
        json={"session_id": "shared", "message": "place an order"},
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["answer"] == "rag-answer"
    assert r2.json()["answer"].startswith("Order has been placed.")
