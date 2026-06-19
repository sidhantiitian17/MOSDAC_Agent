"""Tests for chat_api/* — config, session, service, routes.

These tests use mocks for the retriever / chain / LLM so they run without
Neo4j, ChromaDB, or any remote LLM endpoint.
"""
from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from graph_rag.retrieval.vector_retriever import VectorHit


# ── chat_api/session.py ──────────────────────────────────────────────────────

def test_in_memory_session_store_appends_and_retrieves():
    from chat_api.session import InMemorySessionStore

    store = InMemorySessionStore()
    store.append("s1", "user", "hello")
    store.append("s1", "assistant", "hi")
    turns = store.get("s1")
    assert len(turns) == 2
    assert turns[0] == {"role": "user", "content": "hello"}
    assert turns[1] == {"role": "assistant", "content": "hi"}


def test_in_memory_session_store_isolates_sessions():
    from chat_api.session import InMemorySessionStore

    store = InMemorySessionStore()
    store.append("a", "user", "a1")
    store.append("b", "user", "b1")
    assert store.get("a")[0]["content"] == "a1"
    assert store.get("b")[0]["content"] == "b1"


def test_in_memory_session_store_clear():
    from chat_api.session import InMemorySessionStore

    store = InMemorySessionStore()
    store.append("s", "user", "x")
    store.clear("s")
    assert store.get("s") == []


def test_in_memory_session_store_trims_to_max_turns():
    from chat_api.session import InMemorySessionStore

    store = InMemorySessionStore()
    for i in range(50):
        store.append("s", "user", f"q{i}")
        store.append("s", "assistant", f"a{i}")
    store.trim("s", max_turns=5)
    assert len(store.get("s")) == 10  # 5 turns = 5 user + 5 assistant


def test_build_session_store_defaults_to_memory(monkeypatch):
    from chat_api import session as session_mod
    from chat_api.config import ChatAPISettings

    monkeypatch.setattr(
        session_mod, "chat_api_settings",
        ChatAPISettings(_env_file=None, session_backend="memory"),
        raising=False,
    )
    store = session_mod.build_session_store()
    assert isinstance(store, session_mod.InMemorySessionStore)


def test_build_session_store_redis_requires_url(monkeypatch):
    from chat_api import session as session_mod
    from chat_api.config import ChatAPISettings

    monkeypatch.setattr(
        session_mod, "chat_api_settings",
        ChatAPISettings(_env_file=None, session_backend="redis", redis_url=""),
        raising=False,
    )
    with pytest.raises(RuntimeError, match="CHAT_API_REDIS_URL"):
        session_mod.build_session_store()


# ── chat_api/config.py ───────────────────────────────────────────────────────

def test_config_parses_comma_separated_origins():
    from chat_api.config import ChatAPISettings

    s = ChatAPISettings(
        _env_file=None,
        allowed_origins="https://a.example.com, https://b.example.com,http://localhost",
    )
    assert s.origins_list() == [
        "https://a.example.com",
        "https://b.example.com",
        "http://localhost",
    ]


def test_config_methods_and_headers_lists():
    from chat_api.config import ChatAPISettings

    s = ChatAPISettings(
        _env_file=None,
        allowed_methods="GET,POST",
        allowed_headers="X-Custom,Authorization",
    )
    assert s.methods_list() == ["GET", "POST"]
    assert s.headers_list() == ["X-Custom", "Authorization"]


def test_config_empty_headers_falls_back_to_wildcard():
    from chat_api.config import ChatAPISettings

    s = ChatAPISettings(_env_file=None, allowed_headers="")
    assert s.headers_list() == ["*"]


# ── chat_api/service.py ──────────────────────────────────────────────────────

def _make_service(text_response="text-answer", image_response="image-answer"):
    """Build a ChatService with a fully mocked retriever/chain/LLM.

    The mock retriever now includes ``_hits`` so the grounding gate passes,
    matching the real HybridRetriever.retrieve() contract added in guardplan.md.
    """
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "(A)-[REL]->(B)",
        "vector_context": "[Source: test.pdf | score=0.9000]\nSome passage from MOSDAC documents.",
        "_hits": [
            VectorHit(text="Some passage from MOSDAC documents.", source="test.pdf", score=0.9, chunk_id="c1"),
        ],
    }
    chain = MagicMock()
    chain.invoke.return_value = text_response

    llm = MagicMock()
    response_obj = MagicMock()
    response_obj.content = image_response
    llm.invoke.return_value = response_obj

    sessions = InMemorySessionStore()
    service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)
    return service, retriever, chain, llm, sessions


def test_service_text_chat_uses_chain_and_records_history():
    service, _retriever, chain, _llm, sessions = _make_service()
    answer, _citations, _grounded, refused = service.chat(session_id="s1", message="What is X?")
    assert answer == "text-answer"
    assert not refused
    chain.invoke.assert_called_once()
    turns = sessions.get("s1")
    assert turns[0] == {"role": "user", "content": "What is X?"}
    assert turns[1] == {"role": "assistant", "content": "text-answer"}


def test_service_image_chat_uses_llm_directly():
    service, retriever, chain, llm, _sessions = _make_service()
    b64 = base64.b64encode(b"fake-image-bytes").decode()
    answer, _citations, _grounded, refused = service.chat(
        session_id="s2",
        message="What is on screen?",
        screenshot_b64=b64,
        screenshot_mime="image/png",
    )
    assert answer == "image-answer"
    assert not refused
    chain.invoke.assert_not_called()
    llm.invoke.assert_called_once()
    retriever.retrieve.assert_called_once_with("What is on screen?")


def test_service_rejects_oversized_screenshot(monkeypatch):
    from chat_api import service as service_mod
    from chat_api.config import ChatAPISettings

    monkeypatch.setattr(
        service_mod, "chat_api_settings",
        ChatAPISettings(_env_file=None, max_screenshot_bytes=100, enable_screenshot=True),
        raising=False,
    )
    service, _, _, _, _ = _make_service()
    big = base64.b64encode(b"x" * 500).decode()
    with pytest.raises(ValueError, match="too large"):
        # Message must be >1 char to pass L1 empty-input check
        service.chat(session_id="s", message="What is shown here?", screenshot_b64=big)


def test_service_clears_session():
    service, _, _, _, sessions = _make_service()
    service.chat(session_id="s", message="hi there")
    assert sessions.get("s")
    service.clear_session("s")
    assert sessions.get("s") == []


def test_service_history_prefix_grows_with_turns():
    service, _, chain, _, _ = _make_service()
    chain.invoke.side_effect = ["a1", "a2", "a3"]
    service.chat("s", "q1 about MOSDAC")
    service.chat("s", "q2 about INSAT")
    service.chat("s", "q3 about Oceansat")
    last_call = chain.invoke.call_args_list[-1][0][0]
    assert "Conversation so far:" in last_call["history"]
    assert "q1" in last_call["history"]


# ── chat_api app / routes (FastAPI integration) ──────────────────────────────

@pytest.fixture
def test_client():
    from chat_api.main import create_app
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "g",
        "vector_context": "[Source: test.pdf | score=0.9000]\nMOSDAC passage.",
        "_hits": [VectorHit(text="MOSDAC passage.", source="test.pdf", score=0.9, chunk_id="c1")],
    }
    chain = MagicMock()
    chain.invoke.return_value = "mock-answer"
    llm = MagicMock()
    sessions = InMemorySessionStore()
    service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)

    app = create_app(service=service)
    return TestClient(app), sessions


def test_health_endpoint(test_client):
    client, _ = test_client
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "title" in body
    assert "version" in body


def test_config_endpoint(test_client):
    client, _ = test_client
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert "title" in body
    assert "bot_name" in body
    assert "screenshot_enabled" in body


def test_chat_text_only_endpoint(test_client):
    client, sessions = test_client
    r = client.post("/chat", json={"session_id": "u1", "message": "hi there"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "mock-answer"
    assert body["session_id"] == "u1"
    assert len(sessions.get("u1")) == 2


def test_chat_clear_endpoint(test_client):
    client, sessions = test_client
    client.post("/chat", json={"session_id": "u2", "message": "hi there"})
    assert sessions.get("u2")
    r = client.delete("/chat/u2")
    assert r.status_code == 200
    assert sessions.get("u2") == []


def test_chat_bad_request_on_invalid_screenshot(test_client):
    client, _ = test_client
    r = client.post("/chat", json={
        "session_id": "u3",
        "message": "look at this",
        "screenshot_base64": "!!!!notbase64!!!",
    })
    assert r.status_code == 400
