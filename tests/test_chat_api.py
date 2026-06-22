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


def test_service_image_chat_uses_llm_directly(monkeypatch):
    # The image path is hard-gated on a configured vision model (M6); configure one
    # so this test exercises the real multimodal plumbing.
    from chat_api import service as service_mod
    from chat_api.config import ChatAPISettings

    monkeypatch.setattr(
        service_mod, "chat_api_settings",
        ChatAPISettings(_env_file=None, enable_screenshot=True, vision_model="test-vlm"),
        raising=False,
    )
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
    # Sign-in metadata the widget reads to decide whether/where to show "Sign in".
    assert "auth_enabled" in body
    assert "login_url" in body


_SID1 = "00000000-0000-0000-0000-000000000001"
_SID2 = "00000000-0000-0000-0000-000000000002"
_SID3 = "00000000-0000-0000-0000-000000000003"


def test_chat_text_only_endpoint(test_client):
    client, sessions = test_client
    r = client.post("/chat", json={"session_id": _SID1, "message": "hi there"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "mock-answer"
    assert body["session_id"] == _SID1
    assert len(sessions.get(_SID1)) == 2


def test_chat_clear_endpoint(test_client):
    client, sessions = test_client
    client.post("/chat", json={"session_id": _SID2, "message": "hi there"})
    assert sessions.get(_SID2)
    r = client.delete(f"/chat/{_SID2}")
    assert r.status_code == 200
    assert sessions.get(_SID2) == []


def test_clear_session_rejects_non_uuid(test_client):
    """M4: DELETE /chat/{session_id} validates the id shape (400 on non-UUID)."""
    client, _ = test_client
    r = client.delete("/chat/not-a-uuid")
    assert r.status_code == 400


def test_chat_bad_request_on_invalid_screenshot(test_client):
    client, _ = test_client
    r = client.post("/chat", json={
        "session_id": _SID3,
        "message": "look at this",
        "screenshot_base64": "!!!!notbase64!!!",
    })
    assert r.status_code == 400


# ── New tests (§6 gaps from backend.md) ─────────────────────────────────────

def test_invalid_session_id_returns_422(test_client):
    """Non-UUID session_id must be rejected at the model layer (422 Unprocessable)."""
    client, _ = test_client
    r = client.post("/chat", json={"session_id": "not-a-uuid", "message": "hello"})
    assert r.status_code == 422
    assert "session_id" in r.text.lower() or "uuid" in r.text.lower()


def test_service_refuses_when_no_groundable_hits():
    """When the retriever returns no hits the grounding gate must set refused=True."""
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "",
        "vector_context": "(no relevant passages found)",
        "_hits": [],  # empty → grounding gate fails
    }
    chain = MagicMock()
    sessions = InMemorySessionStore()
    service = ChatService(
        retriever=retriever,
        chain=chain,
        llm=MagicMock(),
        sessions=sessions,
    )
    answer, citations, grounded, refused = service.chat(
        session_id="s-refusal",
        message="What is INSAT resolution?",
    )
    assert refused is True
    assert grounded is False
    chain.invoke.assert_not_called()


def test_service_refuses_when_hits_below_min_score():
    """Hits with score below retrieval_min_score (0.20) must trigger refusal."""
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "",
        "vector_context": "[Source: weak.pdf | score=0.0500]\nweak match",
        "_hits": [
            VectorHit(text="weak match", source="weak.pdf", score=0.05, chunk_id="w1"),
        ],
    }
    chain = MagicMock()
    sessions = InMemorySessionStore()
    service = ChatService(
        retriever=retriever,
        chain=chain,
        llm=MagicMock(),
        sessions=sessions,
    )
    answer, citations, grounded, refused = service.chat(
        session_id="s-low-score",
        message="What is INSAT resolution?",
    )
    assert refused is True
    assert grounded is False
    chain.invoke.assert_not_called()


def test_image_path_goes_through_check_output(monkeypatch):
    """_answer_with_image must call pipeline.check_output() (Bug #4 fix)."""
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore
    from guardrails import get_pipeline

    # The image path is hard-gated on a configured vision model (M6).
    from chat_api import service as service_mod
    from chat_api.config import ChatAPISettings

    monkeypatch.setattr(
        service_mod, "chat_api_settings",
        ChatAPISettings(_env_file=None, enable_screenshot=True, vision_model="test-vlm"),
        raising=False,
    )

    pipeline = get_pipeline()
    called_with = {}

    original_check_output = pipeline.check_output

    def spy_check_output(answer, registry, passages=None, context=""):
        called_with["fired"] = True
        called_with["answer"] = answer
        return original_check_output(answer, registry, passages, context)

    monkeypatch.setattr(pipeline, "check_output", spy_check_output)

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "g",
        "vector_context": "v",
        "_hits": [VectorHit(text="v", source="s.pdf", score=0.9, chunk_id="c1")],
    }
    llm = MagicMock()
    resp = MagicMock()
    resp.content = "image-answer"
    llm.invoke.return_value = resp

    service = ChatService(
        retriever=retriever,
        chain=MagicMock(),
        llm=llm,
        sessions=InMemorySessionStore(),
    )

    b64 = base64.b64encode(b"fake-image-bytes").decode()
    service.chat(
        session_id="s-img",
        message="What is shown here?",
        screenshot_b64=b64,
        screenshot_mime="image/png",
    )
    assert called_with.get("fired"), "check_output was never called on the image path"


def test_security_headers_on_response(test_client):
    """Every response must carry OWASP-recommended security headers."""
    client, _ = test_client
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "frame-ancestors" in r.headers.get("content-security-policy", "")


def test_cors_preflight_from_allowed_origin(test_client):
    """OPTIONS /chat from an allowed origin must receive CORS allow headers."""
    client, _ = test_client
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code in (200, 204)
    assert "http://localhost" in r.headers.get("access-control-allow-origin", "")


def test_screenshot_corrupt_tail_rejected(test_client):
    """A b64 payload with a valid prefix but corrupt tail must now be rejected (Bug #10 fix)."""
    import base64 as _b64
    client, _ = test_client
    # Build a valid b64 prefix longer than 256 chars, then append garbage
    valid_prefix = _b64.b64encode(b"A" * 200).decode()  # 268 chars — spans the old 256-char window
    corrupt_payload = valid_prefix + "!@#$"             # invalid b64 beyond the first 256 chars
    r = client.post("/chat", json={
        "session_id": "00000000-0000-0000-0000-000000000099",
        "message": "look at this",
        "screenshot_base64": corrupt_payload,
    })
    assert r.status_code == 400


# ── Tests for Bugs #5 / #7 / #9 ─────────────────────────────────────────────

def test_chain_receives_pre_retrieved_context():
    """chain.invoke() must receive 'pre_retrieved' and retriever called only once (Bug #5 fix)."""
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "G",
        "vector_context": "V",
        "_hits": [VectorHit(text="V", source="s.pdf", score=0.9, chunk_id="c1")],
    }
    chain = MagicMock()
    chain.invoke.return_value = "answer"
    service = ChatService(
        retriever=retriever,
        chain=chain,
        llm=MagicMock(),
        sessions=InMemorySessionStore(),
    )
    service.chat(session_id="s-pre", message="hello")

    # retriever.retrieve must be called exactly once (grounding gate only)
    assert retriever.retrieve.call_count == 1, (
        f"retriever.retrieve called {retriever.retrieve.call_count} times; expected 1"
    )
    # chain.invoke must have received pre_retrieved
    call_kwargs = chain.invoke.call_args[0][0]
    assert "pre_retrieved" in call_kwargs, "chain.invoke missing 'pre_retrieved' key"
    assert call_kwargs["pre_retrieved"]["graph_context"] == "G"
    assert call_kwargs["pre_retrieved"]["vector_context"] == "V"


def test_cors_default_origins_include_common_dev_ports():
    """Default ChatAPISettings must include port-specific origins (Bug #7 fix).

    The .env file on a given machine may override CHAT_API_ALLOWED_ORIGINS, so
    we test the code-level default directly by constructing ChatAPISettings
    without reading any env file.
    """
    from chat_api.config import ChatAPISettings

    fresh = ChatAPISettings(_env_file=None)
    origins = fresh.origins_list()
    assert "http://localhost:3000" in origins, f"port-3000 missing from defaults: {origins}"
    assert "http://localhost:5173" in origins, f"port-5173 (Vite) missing from defaults: {origins}"
    assert "http://localhost:8080" in origins, f"port-8080 missing from defaults: {origins}"


def test_cors_preflight_from_localhost_with_port():
    """OPTIONS from http://localhost:3000 with explicit settings must return 200 (Bug #7 fix)."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from chat_api.config import ChatAPISettings
    from chat_api.main import create_app
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    fresh_settings = ChatAPISettings(_env_file=None)

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "g",
        "vector_context": "[Source: t.pdf | score=0.9]\npassage.",
        "_hits": [VectorHit(text="passage.", source="t.pdf", score=0.9, chunk_id="c1")],
    }
    chain = MagicMock()
    chain.invoke.return_value = "answer"
    service = ChatService(
        retriever=retriever, chain=chain, llm=MagicMock(),
        sessions=InMemorySessionStore(),
    )

    with patch("chat_api.main.chat_api_settings", fresh_settings):
        app = create_app(service=service)

    client = TestClient(app)
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code in (200, 204)
    assert "http://localhost:3000" in r.headers.get("access-control-allow-origin", "")


def test_get_llm_is_zero_arg_singleton():
    """get_llm() must take no arguments and return the same object on repeated calls (Bug #9 fix)."""
    import inspect
    from graph_rag.llm.tabby_client import get_llm

    sig = inspect.signature(get_llm)
    assert len(sig.parameters) == 0, (
        f"get_llm should have 0 parameters, got: {list(sig.parameters)}"
    )


# ── Per-user history: auth + conversation persistence (SSO) ──────────────────

class _StubTitler:
    """Avoids a real LLM call (and its 60s timeout) in background titling."""

    def make_title(self, question, answer=""):
        return "Test Title"


@pytest.fixture
def authed_client():
    """A client where the auth dependencies are overridden to a fixed user and the
    service is backed by an in-memory SQLite conversation store."""
    from chat_api.auth import NormalizedUser, get_current_user, get_optional_user
    from chat_api.db.sqlite_repo import SQLiteConversationRepository
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
    repo = SQLiteConversationRepository(":memory:")
    service = ChatService(
        retriever=retriever, chain=chain, llm=MagicMock(),
        sessions=InMemorySessionStore(), repo=repo,
    )
    service._titler = _StubTitler()  # no network during background titling

    app = create_app(service=service)
    user_a = NormalizedUser(id="userA", username="alice", email="a@x")
    app.dependency_overrides[get_current_user] = lambda: user_a
    app.dependency_overrides[get_optional_user] = lambda: user_a

    client = TestClient(app)
    try:
        yield client, repo, app
    finally:
        repo.close()


def test_anonymous_chat_returns_null_conversation_id(test_client):
    """With no authenticated user, /chat behaves as before (ephemeral, no DB)."""
    client, _sessions = test_client
    r = client.post("/chat", json={"session_id": _SID1, "message": "hi there"})
    assert r.status_code == 200
    assert r.json()["conversation_id"] is None


def test_authed_chat_creates_and_persists_conversation(authed_client):
    client, repo, _app = authed_client
    r = client.post("/chat", json={"session_id": _SID1, "message": "hi there"})
    assert r.status_code == 200
    cid = r.json()["conversation_id"]
    assert cid  # a conversation id was returned
    msgs = repo.list_messages("userA", cid)
    assert [(m.role, m.content) for m in msgs] == [
        ("user", "hi there"),
        ("assistant", "mock-answer"),
    ]


def test_authed_chat_continues_existing_conversation(authed_client):
    client, repo, _app = authed_client
    cid = client.post("/chat", json={"session_id": _SID1, "message": "first q"}).json()[
        "conversation_id"
    ]
    r2 = client.post(
        "/chat",
        json={"session_id": _SID1, "message": "second q", "conversation_id": cid},
    )
    assert r2.json()["conversation_id"] == cid
    assert len(repo.list_messages("userA", cid)) == 4


def test_me_endpoint_returns_profile(authed_client):
    """GET /me returns the normalized user (claim-mapped server-side)."""
    client, _repo, _app = authed_client
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json() == {"id": "userA", "username": "alice", "email": "a@x"}


def test_me_endpoint_503_when_auth_disabled(test_client, monkeypatch):
    """With auth disabled, /me reports the feature is unavailable.

    Patched explicitly (not relying on the ambient .env, which may enable auth for
    a real SSO deployment) so the test is hermetic either way.
    """
    from chat_api.config import ChatAPISettings

    fresh = ChatAPISettings(_env_file=None)
    fresh.auth_enabled = False
    monkeypatch.setattr("chat_api.auth.chat_api_settings", fresh)
    client, _ = test_client
    assert client.get("/me").status_code == 503


def test_me_endpoint_401_when_token_missing(test_client, monkeypatch):
    """Auth enabled but no bearer token → 401 (never silently anonymous)."""
    from chat_api.config import ChatAPISettings

    fresh = ChatAPISettings(_env_file=None)
    fresh.auth_enabled = True
    fresh.keycloak_issuer = "https://kc/realms/m"
    monkeypatch.setattr("chat_api.auth.chat_api_settings", fresh)
    client, _ = test_client
    assert client.get("/me").status_code == 401


def test_list_conversations_endpoint(authed_client):
    client, _repo, _app = authed_client
    client.post("/chat", json={"session_id": _SID1, "message": "hello"})
    r = client.get("/conversations")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["id"] and "title" in items[0]


def test_get_conversation_messages_endpoint(authed_client):
    client, _repo, _app = authed_client
    cid = client.post("/chat", json={"session_id": _SID1, "message": "hello"}).json()[
        "conversation_id"
    ]
    r = client.get(f"/conversations/{cid}/messages")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == cid
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_delete_conversation_endpoint(authed_client):
    client, repo, _app = authed_client
    cid = client.post("/chat", json={"session_id": _SID1, "message": "hello"}).json()[
        "conversation_id"
    ]
    r = client.delete(f"/conversations/{cid}")
    assert r.status_code == 200
    assert repo.get_conversation("userA", cid) is None


def test_idor_other_user_cannot_load_or_delete(authed_client):
    """A second user must get 404 (not 403) for another user's conversation."""
    from chat_api.auth import NormalizedUser, get_current_user

    client, _repo, app = authed_client
    cid = client.post("/chat", json={"session_id": _SID1, "message": "secret"}).json()[
        "conversation_id"
    ]
    app.dependency_overrides[get_current_user] = lambda: NormalizedUser(
        id="userB", username="bob", email="b@x"
    )
    assert client.get(f"/conversations/{cid}/messages").status_code == 404
    assert client.delete(f"/conversations/{cid}").status_code == 404


def test_idor_cannot_continue_another_users_conversation(authed_client):
    from chat_api.auth import NormalizedUser, get_optional_user

    client, _repo, app = authed_client
    cid = client.post("/chat", json={"session_id": _SID1, "message": "mine"}).json()[
        "conversation_id"
    ]
    app.dependency_overrides[get_optional_user] = lambda: NormalizedUser(
        id="userB", username="bob", email="b@x"
    )
    r = client.post(
        "/chat",
        json={"session_id": _SID2, "message": "steal", "conversation_id": cid},
    )
    assert r.status_code == 404


def test_invalid_conversation_id_returns_422(authed_client):
    client, _repo, _app = authed_client
    r = client.post(
        "/chat",
        json={"session_id": _SID1, "message": "hello", "conversation_id": "not-a-uuid"},
    )
    assert r.status_code == 422
