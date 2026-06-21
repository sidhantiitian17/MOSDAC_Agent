"""
Feature verification script — runs without external services (Tabby/Ollama/Neo4j).
Each check prints PASS / FAIL and a one-line reason.

Run:
    python tests/verify_features.py
"""
from __future__ import annotations

import base64
import os
import sys

# Ensure repo root is on sys.path when the script is run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock

from graph_rag.retrieval.vector_retriever import VectorHit

PASS_STR = "PASS"
FAIL_STR = "FAIL"
results: list[tuple[str, bool, str]] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    results.append((label, passed, detail))
    icon = PASS_STR if passed else FAIL_STR
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{icon}]  {label}{suffix}")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_test_client():
    """Return (TestClient, sessions) backed by mocked retriever/chain/LLM."""
    from fastapi.testclient import TestClient
    from chat_api.main import create_app
    from chat_api.service import ChatService
    from chat_api.session import InMemorySessionStore

    retriever = MagicMock()
    retriever.retrieve.return_value = {
        "graph_context": "(A)-[REL]->(B)",
        "vector_context": "[Source: test.pdf | score=0.9]\nMOSDAC passage.",
        "_hits": [VectorHit(text="MOSDAC passage.", source="test.pdf", score=0.9, chunk_id="c1")],
    }
    chain = MagicMock()
    chain.invoke.return_value = "mock-answer"
    llm = MagicMock()
    resp = MagicMock()
    resp.content = "image-answer"
    llm.invoke.return_value = resp

    sessions = InMemorySessionStore()
    service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)
    app = create_app(service=service)
    return TestClient(app, raise_server_exceptions=False), sessions


VALID_SID  = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
VALID_SID2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
VALID_SID3 = "cccccccc-cccc-cccc-cccc-cccccccccccc"


# ─────────────────────────────────────────────────────────────────────────────
# Group 1 — Bug #8: UUID session_id validation
# ─────────────────────────────────────────────────────────────────────────────

print("\n== Bug #8: UUID session_id validation ==")
client, _ = _make_test_client()

r = client.post("/chat", json={"session_id": "not-a-uuid", "message": "hello"})
check("Non-UUID session_id rejected with 422", r.status_code == 422,
      f"status={r.status_code}")

r = client.post("/chat", json={"session_id": "abc", "message": "hello"})
check("Short non-UUID session_id rejected with 422", r.status_code == 422,
      f"status={r.status_code}")

r = client.post("/chat", json={"session_id": VALID_SID, "message": "hi there"})
check("Valid UUID session_id accepted (200)", r.status_code == 200,
      f"status={r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# Group 2 — Bug #10: Full base64 validation (not just first 256 chars)
# ─────────────────────────────────────────────────────────────────────────────

print("\n== Bug #10: Full base64 validation ==")
client, _ = _make_test_client()

# Valid prefix > 256 chars + corrupt tail
valid_prefix = base64.b64encode(b"X" * 200).decode()   # 268 chars of valid b64
corrupt_payload = valid_prefix + "!@#$"

r = client.post("/chat", json={
    "session_id": VALID_SID,
    "message": "look at this",
    "screenshot_base64": corrupt_payload,
})
check("Corrupt-tail b64 (valid 268-char prefix + '!@#$') rejected 400",
      r.status_code == 400, f"status={r.status_code}")

r = client.post("/chat", json={
    "session_id": VALID_SID,
    "message": "look at this",
    "screenshot_base64": "!!!!notbase64!!!!",
})
check("Fully invalid b64 still rejected 400", r.status_code == 400,
      f"status={r.status_code}")

valid_b64 = base64.b64encode(b"fake-image-bytes").decode()
r = client.post("/chat", json={
    "session_id": VALID_SID2,
    "message": "what is shown?",
    "screenshot_base64": valid_b64,
})
check("Valid b64 screenshot accepted (200)", r.status_code == 200,
      f"status={r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# Group 3 — Bug #4: Image path runs check_output() guardrails
# ─────────────────────────────────────────────────────────────────────────────

print("\n== Bug #4: Image path runs check_output() ==")
from guardrails import get_pipeline

pipeline = get_pipeline()
fired: dict = {}
_orig_check_output = pipeline.check_output


def _spy(answer, registry, passages=None, context=""):
    fired["called"] = True
    fired["answer"] = answer
    return _orig_check_output(answer, registry, passages, context)


pipeline.check_output = _spy  # type: ignore

from chat_api.service import ChatService
from chat_api.session import InMemorySessionStore

_retriever = MagicMock()
_retriever.retrieve.return_value = {
    "graph_context": "g",
    "vector_context": "v",
    "_hits": [VectorHit(text="v", source="s.pdf", score=0.9, chunk_id="c1")],
}
_llm = MagicMock()
_resp = MagicMock()
_resp.content = "raw-image-answer"
_llm.invoke.return_value = _resp

_svc = ChatService(retriever=_retriever, chain=MagicMock(), llm=_llm,
                   sessions=InMemorySessionStore())
_b64 = base64.b64encode(b"fake").decode()
_svc.chat(session_id="img-session", message="What is shown?",
          screenshot_b64=_b64, screenshot_mime="image/png")

check("check_output() fired during image path", fired.get("called") is True)
check("check_output received the raw LLM answer",
      fired.get("answer") == "raw-image-answer",
      f"got {fired.get('answer')!r}")

pipeline.check_output = _orig_check_output  # restore


# ─────────────────────────────────────────────────────────────────────────────
# Group 4 — Bug #1: SlowAPIMiddleware is present in the middleware stack
# ─────────────────────────────────────────────────────────────────────────────

print("\n== Bug #1: SlowAPIMiddleware wired into app ==")
from chat_api.main import create_app as _create_app

_test_app = _create_app(service=MagicMock())

try:
    from slowapi.middleware import SlowAPIMiddleware  # type: ignore

    # user_middleware holds the pre-build middleware list — reliable across Starlette versions
    mw_names = [m.cls.__name__ for m in _test_app.user_middleware]
    found_mw = "SlowAPIMiddleware" in mw_names

    check("SlowAPIMiddleware registered in user_middleware", found_mw,
          f"registered: {mw_names}")
    check("app.state.limiter is set", hasattr(_test_app.state, "limiter"))

except ImportError:
    check("slowapi not installed — install it to enable rate limiting", False,
          "pip install slowapi")


# ─────────────────────────────────────────────────────────────────────────────
# Group 5 — Security headers on every response
# ─────────────────────────────────────────────────────────────────────────────

print("\n== Security headers on every response ==")
client, _ = _make_test_client()
r = client.get("/health")

check("X-Content-Type-Options: nosniff",
      r.headers.get("x-content-type-options") == "nosniff",
      repr(r.headers.get("x-content-type-options")))
check("X-Frame-Options: DENY",
      r.headers.get("x-frame-options") == "DENY",
      repr(r.headers.get("x-frame-options")))
check("Referrer-Policy: no-referrer",
      r.headers.get("referrer-policy") == "no-referrer",
      repr(r.headers.get("referrer-policy")))
check("Content-Security-Policy contains frame-ancestors",
      "frame-ancestors" in r.headers.get("content-security-policy", ""),
      repr(r.headers.get("content-security-policy", "")[:60]))
check("Permissions-Policy present",
      "permissions-policy" in r.headers,
      repr(r.headers.get("permissions-policy", "")[:40]))


# ─────────────────────────────────────────────────────────────────────────────
# Group 6 — CORS preflight from allowed origin
# ─────────────────────────────────────────────────────────────────────────────

print("\n== CORS preflight ==")
client, _ = _make_test_client()
r = client.options(
    "/chat",
    headers={
        "Origin": "http://localhost",
        "Access-Control-Request-Method": "POST",
    },
)
check("OPTIONS /chat returns 200 or 204", r.status_code in (200, 204),
      f"status={r.status_code}")
check("Access-Control-Allow-Origin contains http://localhost",
      "http://localhost" in r.headers.get("access-control-allow-origin", ""),
      repr(r.headers.get("access-control-allow-origin")))

r_blocked = client.options(
    "/chat",
    headers={
        "Origin": "https://evil.example.com",
        "Access-Control-Request-Method": "POST",
    },
)
check("OPTIONS from disallowed origin does NOT get allow-origin header",
      "evil.example.com" not in r_blocked.headers.get("access-control-allow-origin", ""),
      repr(r_blocked.headers.get("access-control-allow-origin", "(absent)")))


# ─────────────────────────────────────────────────────────────────────────────
# Group 7 — Grounding gate refusal (no-context / low-score)
# ─────────────────────────────────────────────────────────────────────────────

print("\n== Grounding gate (no-context refusal) ==")
from chat_api.service import ChatService
from chat_api.session import InMemorySessionStore

# Empty hits
_r_empty = MagicMock()
_r_empty.retrieve.return_value = {
    "graph_context": "",
    "vector_context": "(no relevant passages found)",
    "_hits": [],
}
_c_empty = MagicMock()
_svc_empty = ChatService(retriever=_r_empty, chain=_c_empty,
                         llm=MagicMock(), sessions=InMemorySessionStore())
_, _, grounded_e, refused_e = _svc_empty.chat(
    session_id="s-empty", message="What is INSAT-3D resolution?"
)
check("Empty _hits => refused=True",  refused_e  is True,  f"refused={refused_e}")
check("Empty _hits => grounded=False", grounded_e is False, f"grounded={grounded_e}")
check("Empty _hits => chain.invoke never called", not _c_empty.invoke.called)

# Low-score hits (0.05 < min_score 0.20)
_r_low = MagicMock()
_r_low.retrieve.return_value = {
    "graph_context": "",
    "vector_context": "[Source: x | score=0.05]\nweak",
    "_hits": [VectorHit(text="weak", source="x", score=0.05, chunk_id="x1")],
}
_c_low = MagicMock()
_svc_low = ChatService(retriever=_r_low, chain=_c_low,
                       llm=MagicMock(), sessions=InMemorySessionStore())
_, _, grounded_l, refused_l = _svc_low.chat(
    session_id="s-low", message="What is INSAT-3D resolution?"
)
check("Score 0.05 < min 0.20 => refused=True",  refused_l  is True,  f"refused={refused_l}")
check("Score 0.05 < min 0.20 => chain never called", not _c_low.invoke.called)


# ─────────────────────────────────────────────────────────────────────────────
# Group 8 — docker-compose.yml fixes (Bug #2 + #6)
# ─────────────────────────────────────────────────────────────────────────────

print("\n== docker-compose.yml fixes (Bug #2 + #6) ==")
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
compose_text = open(os.path.join(_repo_root, "docker-compose.yml"), encoding="utf-8").read()

check("OLLAMA_BASE_URL env var present (Bug #2 fix)",
      "OLLAMA_BASE_URL:" in compose_text and "host.docker.internal:11434" in compose_text)
check("OLLAMA_BASE_URL_DOCKER override documented",
      "OLLAMA_BASE_URL_DOCKER" in compose_text)
check("Stale models_cache bind-mount removed (Bug #6 fix)",
      "models_cache" not in compose_text)
check("Header comment updated to 'Ollama HTTP' (not models_cache)",
      "bge-large via Ollama HTTP" in compose_text)
check("extra_hosts still includes host.docker.internal gateway",
      "host.docker.internal:host-gateway" in compose_text)


# ─────────────────────────────────────────────────────────────────────────────
# Group 9 — API happy path (health + config endpoints)
# ─────────────────────────────────────────────────────────────────────────────

print("\n== API happy path ==")
client, _ = _make_test_client()

r = client.get("/health")
check("GET /health returns 200", r.status_code == 200, f"status={r.status_code}")
body = r.json()
check("health body has status=ok",      body.get("status") == "ok")
check("health body has title field",    "title"   in body)
check("health body has version field",  "version" in body)
check("health body has bot_name field", "bot_name" in body)

r = client.get("/config")
check("GET /config returns 200", r.status_code == 200, f"status={r.status_code}")
cfg = r.json()
check("config has screenshot_enabled", "screenshot_enabled" in cfg)
check("config has max_screenshot_bytes", "max_screenshot_bytes" in cfg)

# Text chat happy path
r = client.post("/chat", json={"session_id": VALID_SID3, "message": "hi there"})
check("POST /chat returns 200", r.status_code == 200, f"status={r.status_code}")
body = r.json()
check("response has answer field",     "answer"     in body)
check("response has session_id field", "session_id" in body)
check("response has grounded field",   "grounded"   in body)
check("response has refused field",    "refused"    in body)
check("response has citations list",   isinstance(body.get("citations"), list))

# DELETE session
r = client.delete(f"/chat/{VALID_SID3}")
check("DELETE /chat/{id} returns 200", r.status_code == 200, f"status={r.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

total  = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed

print(f"\n{'='*60}")
print(f"  TOTAL: {passed}/{total} checks passed", end="")
if failed:
    print(f"  —  {failed} FAILED\n")
    print("  Failed checks:")
    for label, ok, detail in results:
        if not ok:
            print(f"    ✗  {label}  ({detail})")
else:
    print("  —  ALL PASSED")
print(f"{'='*60}\n")

sys.exit(0 if failed == 0 else 1)
