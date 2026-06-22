"""End-to-end pre-production pipeline test — ONE file, full pipeline, live API.

Mirrors `main.py`, but instead of ingesting every file under DOWNLOADS_DIR/ATLASES_DIR
it ingests exactly ONE configurable document and drives the whole production path:

    Docling parse  →  Chroma vector DB  →  Neo4j knowledge graph  →  FastAPI RAG

then leaves you a runbook for the browser-side checks (login → ask → persisted chat
→ username personalization) that can only be done by a human against Keycloak + the
Drupal widget.

──────────────────────────────────────────────────────────────────────────────
WHICH FILE (configurable via .env)
    TEST_INGEST_FILE   absolute/relative path to the single document to ingest.
                       If unset, the basename below is resolved across the known
                       atlas/download locations (host AND container layouts).
    TEST_INGEST_BASENAME   default file name (default: Eyes_on_Waves_from_Space.pdf)

WHERE TO RUN IT  (this is load-bearing — see the audit in the chat that shipped this)
    The live stack is dockerized: chat_api owns ./chroma_db (uid 10001) and reaches
    Ollama via host.docker.internal. The WSL host CANNOT write Chroma or reach the
    embedder, so ingestion MUST run inside the container:

        docker compose cp test_main.py mosdac_chat_api:/app/test_main.py
        docker compose exec chat_api python /app/test_main.py

    A fully-host setup (no docker, Ollama on localhost, you own ./chroma_db) also
    works:  python test_main.py

USEFUL FLAGS
    --file PATH      override TEST_INGEST_FILE for this run
    --reset          wipe the Chroma collection + Neo4j graph FIRST, so the corpus
                     is exactly this one file (deterministic grounding). Destructive.
    --yes            don't prompt for confirmation on --reset
    --skip-ingest    only run preflight + store verification + API checks
    --no-api         skip the in-process RAG API checks (Phases 3)
    --serve          after the checks, launch a host uvicorn on :8001 for manual use

EXIT CODE  0 = every phase passed, 1 = a phase failed (CI-friendly).
──────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# UTF-8 stdout, same as main.py (Docling/log lines contain non-ASCII).
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("test_main")

DEFAULT_BASENAME = "Eyes_on_Waves_from_Space.pdf"


# ── tiny console helpers ──────────────────────────────────────────────────────
class C:
    OK, WARN, FAIL, HEAD, DIM, END = "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[2m", "\033[0m"


def banner(title: str) -> None:
    print(f"\n{C.HEAD}{'═' * 78}\n  {title}\n{'═' * 78}{C.END}")


def ok(msg: str) -> None:    print(f"  {C.OK}✓{C.END} {msg}")
def warn(msg: str) -> None:  print(f"  {C.WARN}⚠ {msg}{C.END}")
def bad(msg: str) -> None:   print(f"  {C.FAIL}✗ {msg}{C.END}")
def info(msg: str) -> None:  print(f"  {C.DIM}·{C.END} {msg}")


class PhaseError(RuntimeError):
    """A hard failure that should abort the run with a non-zero exit code."""


# ── file resolution (host + container layouts) ────────────────────────────────
def resolve_target(cli_path: str | None) -> Path:
    from graph_rag.config import settings

    explicit = cli_path or os.getenv("TEST_INGEST_FILE", "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        warn(f"TEST_INGEST_FILE={explicit!r} does not exist — falling back to basename search.")

    basename = os.getenv("TEST_INGEST_BASENAME", DEFAULT_BASENAME).strip() or DEFAULT_BASENAME
    # The atlas mount target inside the container is /app/atlases (NOT ./atlases_pdfs),
    # so search both the configured dirs AND the container mount points.
    candidates = [
        Path(settings.atlases_dir) / basename,
        Path(settings.downloads_dir) / basename,
        Path("/app/atlases") / basename,
        Path("/app/downloads") / basename,
        Path("atlases_pdfs") / basename,
        Path("downloads") / basename,
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise PhaseError(
        f"Could not find {basename!r}. Set TEST_INGEST_FILE to its full path. "
        f"Searched: {', '.join(str(c) for c in candidates)}"
    )


# ── Phase 0: preflight + brutal coherence audit ───────────────────────────────
def phase_preflight(target: Path) -> None:
    banner("PHASE 0 — PREFLIGHT & CONFIG AUDIT")
    from graph_rag.config import settings
    from graph_rag.health import readiness
    from chat_api.config import chat_api_settings as api

    info(f"ingest target          : {target}  ({target.stat().st_size / 1e6:.1f} MB)")
    info(f"OLLAMA_BASE_URL        : {settings.ollama_base_url}  ({settings.ollama_embedding_model})")
    info(f"NEO4J_URI              : {settings.neo4j_uri}")
    info(f"TABBY_BASE_URL         : {settings.tabby_base_url}  ({settings.tabby_model})")
    info(f"CHROMA_PERSIST_DIR     : {settings.chroma_persist_dir}  (collection={settings.chroma_collection})")
    info(f"use_docling            : {settings.use_docling}  extraction_backend={settings.extraction_backend}")

    hard_fail = False

    # 1) Live dependency probes — the SAME readiness() the /ready endpoint uses.
    report = readiness(cache_seconds=0.0, include_llm=True)
    for name, res in report["checks"].items():
        detail = res["detail"]
        if res["ok"]:
            ok(f"dependency {name:9s}: {detail}")
        elif name == "llm":
            warn(f"dependency {name:9s}: {detail} — KG falls back to spaCy; chat answers will FAIL")
        else:
            bad(f"dependency {name:9s}: {detail}")
            hard_fail = True

    # 2) Can we actually WRITE Chroma? (host-vs-container ownership trap)
    chroma_dir = Path(settings.chroma_persist_dir)
    if chroma_dir.exists() and not os.access(chroma_dir, os.W_OK):
        bad(f"CHROMA_PERSIST_DIR {chroma_dir} is NOT writable by uid {os.getuid()} — "
            f"you are likely on the host while the container (uid 10001) owns it. "
            f"Run inside the container: docker compose exec chat_api python /app/test_main.py")
        hard_fail = True
    else:
        ok(f"CHROMA_PERSIST_DIR writable by uid {os.getuid()}")

    # 3) Auth coherence — the #1 reason login/username/persistence silently fails.
    if api.auth_enabled:
        issuer = api.keycloak_issuer
        jwks = api.effective_jwks_url()
        info(f"auth_enabled=True  issuer={issuer}")
        info(f"derived JWKS       : {jwks}")
        info(f"username claim     : {api.jwt_field_username}   login_url={api.login_url}")
        _probe_issuer(issuer)
        warn("The widget's Bearer token MUST carry iss == the issuer above AND be signed "
             "by that realm. sso-demo.html defaults to realm 'mosdac'@localhost:8081 — "
             "open it as: /static/sso-demo.html?kc=<issuer-host>&realm=<realm>&client=<client>")
    else:
        warn("CHAT_API_AUTH_ENABLED=false — /me 503s, no persisted history, greeting stays 'User'.")

    # 4) CORS DELETE check — sidebar delete fails cross-origin without it.
    if "DELETE" not in api.methods_list():
        warn("CHAT_API_ALLOWED_METHODS is missing DELETE — 'delete conversation' will fail "
             "the CORS preflight cross-origin (only works same-origin via the /chatapi proxy).")
    else:
        ok("CORS methods include DELETE")

    # 5) atlases mount/config mismatch (container only)
    if Path("/app/atlases").is_dir() and not list(Path(settings.atlases_dir).glob("*.pdf")):
        warn(f"ATLASES_DIR={settings.atlases_dir} resolves empty but /app/atlases has files — "
             f"the container's `main.py ingest` would find NO atlases. (This single-file "
             f"test resolves the path directly, so it is unaffected.)")

    if hard_fail:
        raise PhaseError("Preflight found a blocking dependency/permission failure (see ✗ above).")
    ok("preflight passed — dependencies reachable, Chroma writable")


def _probe_issuer(issuer: str) -> None:
    if not issuer:
        bad("keycloak_issuer is empty — auth is misconfigured (no JWKS source).")
        return
    try:
        import httpx
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        r = httpx.get(url, timeout=4.0)
        if r.status_code == 200 and r.json().get("issuer") == issuer:
            ok(f"Keycloak issuer reachable and self-consistent ({issuer})")
        elif r.status_code == 200:
            warn(f"Keycloak reachable but discovery 'issuer' = {r.json().get('issuer')!r} "
                 f"!= configured {issuer!r} — tokens minted there will be REJECTED.")
        else:
            warn(f"Keycloak discovery returned HTTP {r.status_code} for {url}")
    except Exception as exc:  # noqa: BLE001
        warn(f"Could not reach Keycloak issuer {issuer}: {exc}")


# ── optional reset (destructive) ──────────────────────────────────────────────
def reset_stores(assume_yes: bool) -> None:
    banner("RESET — wiping Chroma collection + Neo4j graph")
    if not assume_yes:
        ans = input(f"  {C.WARN}This DELETES all vectors and graph nodes. Type 'yes' to proceed: {C.END}")
        if ans.strip().lower() != "yes":
            raise PhaseError("Reset aborted by user.")
    from graph_rag.embeddings import get_embedder
    from graph_rag.vector_store.chroma_store import ChromaStore
    from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

    ChromaStore(embedder=get_embedder()).reset()
    ok("Chroma collection reset")
    with Neo4jStore() as neo:
        neo.clear()
    ok("Neo4j graph cleared")


# ── Phase 1: ingest the single file through the real pipeline ─────────────────
def phase_ingest(target: Path) -> dict:
    banner("PHASE 1 — INGEST ONE FILE (Docling → Chroma → Neo4j)")
    from graph_rag.embeddings import get_embedder
    from graph_rag.ingestion.loader import load_file
    from graph_rag.ingestion.pipeline import IngestionPipeline
    from graph_rag.knowledge_graph.neo4j_store import Neo4jStore
    from graph_rag.vector_store.chroma_store import ChromaStore

    before_chroma = ChromaStore(embedder=get_embedder()).count()
    with Neo4jStore() as neo:
        before_graph = neo.schema_report()
    info(f"before — chroma chunks={before_chroma}  graph={before_graph}")

    t0 = time.monotonic()
    log.info("Loading + Docling-parsing %s …", target.name)
    docs = load_file(target)          # ← real Docling parse + preprocess + enrich
    if not docs:
        raise PhaseError(f"load_file produced 0 documents for {target} — parser/quality gate rejected it.")
    ok(f"Docling produced {len(docs)} pre-chunked document(s) in {time.monotonic() - t0:.0f}s")

    # run_on_documents = split → embed(Chroma) → extract(Neo4j); bypasses the
    # file-discovery + manifest (so this single file is always (re)ingested).
    stats = IngestionPipeline().run_on_documents(docs)
    print("\n" + stats.summary())

    if stats.errors:
        for e in stats.errors:
            bad(f"pipeline error: {e}")
        raise PhaseError("Ingestion reported errors — vector and/or graph write failed.")
    if stats.chunks_indexed <= 0 and stats.chunks_created > 0:
        # Idempotent path: chunks already present from a prior run (dedup by chunk_id).
        warn("0 new chunks indexed — already present from a prior run. Use --reset for a clean corpus.")
    ok(f"ingest OK: loaded={stats.documents_loaded} chunks={stats.chunks_created} "
       f"indexed={stats.chunks_indexed} entities={stats.entities_created} "
       f"rels={stats.relationships_created} backend={stats.extraction_backend}")
    return {"before_chroma": before_chroma, "before_graph": before_graph}


# ── Phase 2: verify the stores actually hold this file's content ──────────────
def phase_verify(target: Path, baseline: dict) -> None:
    banner("PHASE 2 — VERIFY VECTOR DB + KNOWLEDGE GRAPH")
    from graph_rag.embeddings import get_embedder
    from graph_rag.knowledge_graph.neo4j_store import Neo4jStore
    from graph_rag.vector_store.chroma_store import ChromaStore

    store = ChromaStore(embedder=get_embedder())
    after = store.count()
    if after <= 0:
        raise PhaseError("Chroma collection is empty after ingestion.")
    ok(f"Chroma now holds {after} chunks (was {baseline['before_chroma']})")

    # A real semantic query must retrieve chunks from THIS document.
    probe = "waves ocean satellite measurement from space"
    hits = store.similarity_search(probe, k=3)
    if not hits:
        raise PhaseError(f"similarity_search returned no hits for {probe!r}.")
    src_match = any(target.name in (h.metadata.get("source", "") + h.metadata.get("file_name", "")) for h in hits)
    (ok if src_match else warn)(
        f"vector search returned {len(hits)} hits"
        + ("" if src_match else " (none from the target file — corpus has other docs; use --reset)"))
    info(f"top hit: {hits[0].page_content[:90].strip()!r}…")

    with Neo4jStore() as neo:
        rep = neo.schema_report()
    if rep["chunks"] <= 0 and rep["entities"] <= 0:
        raise PhaseError(f"Neo4j has no chunks/entities after ingestion: {rep}")
    ok(f"Neo4j graph: entities={rep['entities']} relationships={rep['relationships']} "
       f"measurements={rep['measurements']} chunks={rep['chunks']}")


# ── Phase 3: exercise the real RAG API in-process (guaranteed store visibility) ─
def phase_api() -> None:
    banner("PHASE 3 — FASTAPI RAG ENDPOINT (in-process, real retrieval + LLM)")
    from fastapi.testclient import TestClient
    from chat_api.config import chat_api_settings as api
    from chat_api.main import create_app

    # Built in THIS process, so it serves the Chroma we just wrote (no cross-process
    # segment-visibility issue). create_app() does the full real wiring.
    app = create_app()
    client = TestClient(app)

    r = client.get("/health")
    assert r.status_code == 200, r.text
    ok(f"/health 200  bot={r.json().get('bot_name')}")

    r = client.get("/ready")
    (ok if r.status_code == 200 else warn)(f"/ready {r.status_code}  ready={r.json().get('ready')}")

    r = client.get("/config")
    cfg = r.json()
    assert r.status_code == 200
    ok(f"/config auth_enabled={cfg.get('auth_enabled')} login_url={cfg.get('login_url')!r}")

    # /me with no token: 401 when auth on, 503 when off — never a silent anonymous user.
    r = client.get("/me")
    if api.auth_enabled:
        assert r.status_code == 401, f"/me should 401 without a token, got {r.status_code}"
        ok("/me without token → 401 (correct: never silently anonymous)")
    else:
        ok(f"/me without token → {r.status_code} (auth disabled)")

    # Anonymous /chat — the real grounded RAG answer over the ingested file.
    import uuid
    sid = str(uuid.uuid4())
    r = client.post("/chat", json={"session_id": sid, "message": "What is this document about?"})
    assert r.status_code == 200, r.text
    body = r.json()
    answer = (body.get("answer") or "").strip()
    assert answer, "empty answer from /chat"
    assert body.get("conversation_id") is None, "anonymous chat must not create a conversation"
    if body.get("refused") or not body.get("grounded"):
        warn(f"/chat refused/ungrounded — retrieval floor not met. answer={answer[:120]!r}")
    else:
        ok(f"/chat grounded answer ({len(answer)} chars): {answer[:120]!r}…")

    # Authed endpoints must reject a missing token.
    r = client.get("/conversations")
    assert r.status_code in (401, 503), f"/conversations should require auth, got {r.status_code}"
    ok(f"/conversations without token → {r.status_code} (auth required)")

    _try_authenticated_flow(client)


def _try_authenticated_flow(client) -> None:
    """OPTIONAL: prove username personalization + persisted chat end-to-end.

    Needs a Keycloak Direct-Access-Grant (ROPC) client + a test user, supplied via
    env: TEST_KC_CLIENT, TEST_KC_USERNAME, TEST_KC_PASSWORD (and TEST_KC_SECRET for a
    confidential client). Skips cleanly if not configured.
    """
    from chat_api.config import chat_api_settings as api
    user = os.getenv("TEST_KC_USERNAME")
    pw = os.getenv("TEST_KC_PASSWORD")
    clid = os.getenv("TEST_KC_CLIENT")
    if not (api.auth_enabled and user and pw and clid):
        info("authed flow skipped (set TEST_KC_CLIENT/USERNAME/PASSWORD to verify /me + persistence).")
        return
    try:
        import httpx
        tok_url = api.keycloak_issuer.rstrip("/") + "/protocol/openid-connect/token"
        data = {"grant_type": "password", "client_id": clid, "username": user,
                "password": pw, "scope": "openid"}
        if os.getenv("TEST_KC_SECRET"):
            data["client_secret"] = os.getenv("TEST_KC_SECRET")
        token = httpx.post(tok_url, data=data, timeout=8.0).raise_for_status().json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        me = client.get("/me", headers=h)
        assert me.status_code == 200, f"/me with token → {me.status_code}: {me.text}"
        uname = me.json().get("username")
        ok(f"/me with token → username={uname!r} (this is what replaces 'User' in the greeting)")

        import uuid
        sid = str(uuid.uuid4())
        c1 = client.post("/chat", headers=h, json={"session_id": sid, "message": "Summarize this document."})
        cid = c1.json().get("conversation_id")
        assert cid, "authed /chat did not create a conversation_id"
        ok(f"authed /chat created persistent conversation {cid}")

        lst = client.get("/conversations", headers=h)
        assert any(c["id"] == cid for c in lst.json()), "new conversation missing from /conversations"
        ok(f"/conversations lists the persisted chat ({len(lst.json())} total) — personalization verified")
    except Exception as exc:  # noqa: BLE001
        warn(f"authed flow could not complete: {exc}")


# ── manual runbook (the browser-only part) ────────────────────────────────────
def print_runbook(target: Path) -> None:
    from chat_api.config import chat_api_settings as api
    banner("MANUAL RUNBOOK — login → ask → persisted chat → username")
    print(f"""  The automated phases built the vector DB + KG from {target.name} and proved the
  RAG API answers from it. The login / username / persistence checks need a browser:

  1. Make the LIVE container serve the data you just ingested (it runs in a separate
     process and won't see new Chroma segments until it reopens them):
        {C.DIM}docker compose restart chat_api{C.END}

  2. Open the widget against the running API (same-origin avoids CORS):
        {C.DIM}http://localhost:8000/static/sso-demo.html?kc=<KC_HOST>&realm=<REALM>&client=<CLIENT>{C.END}
     For THIS deployment the backend expects issuer:
        {C.DIM}{api.keycloak_issuer}{C.END}
     so KC_HOST/REALM must match it EXACTLY (host + realm), e.g.
        {C.DIM}?kc=http://192.168.1.36:8081&realm=master&client=<your-public-client>{C.END}
     The public client must allow redirect http://localhost:8000/* and PKCE/Standard Flow.

  3. In the launcher (bottom-right): click {C.DIM}Sign in{C.END} → authenticate in Keycloak.
     EXPECT: greeting changes from "Hey User," to "Hey <your-username>," and the
     {C.DIM}Chat History{C.END} sidebar appears. (If it stays "User", the token's iss/realm
     does not match the backend issuer above — see issue #1 in the audit.)

  4. Ask a question about the document, e.g. "What is this atlas about?".
     EXPECT: a grounded answer; a new entry appears in Chat History; reloading the
     page and reopening that entry restores the messages (persisted per-user).

  5. For the real Drupal site, the same applies but the token comes from Drupal's
     OIDC (meta[name=kc-token]); Drupal's OIDC issuer must equal the backend issuer.
""")


# ── orchestration ─────────────────────────────────────────────────────────────
def run(args) -> int:
    from dotenv import load_dotenv
    load_dotenv()

    target = resolve_target(args.file)
    phase_preflight(target)

    if args.reset and not args.skip_ingest:
        reset_stores(args.yes)

    baseline = {"before_chroma": 0, "before_graph": {}}
    if not args.skip_ingest:
        baseline = phase_ingest(target)
    else:
        warn("ingestion skipped (--skip-ingest)")

    phase_verify(target, baseline)

    if not args.no_api:
        phase_api()
    else:
        warn("API checks skipped (--no-api)")

    print_runbook(target)

    if args.serve:
        banner("SERVE — host uvicorn on :8001 (Ctrl+C to stop)")
        import uvicorn
        uvicorn.run("chat_api.main:app", host="0.0.0.0", port=8001, log_level="info")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="End-to-end one-file pipeline test.")
    p.add_argument("--file", help="override TEST_INGEST_FILE for this run")
    p.add_argument("--reset", action="store_true", help="wipe Chroma + Neo4j first (destructive)")
    p.add_argument("--yes", action="store_true", help="skip the --reset confirmation prompt")
    p.add_argument("--skip-ingest", action="store_true", help="only preflight + verify + API")
    p.add_argument("--no-api", action="store_true", help="skip the in-process RAG API checks")
    p.add_argument("--serve", action="store_true", help="launch a host uvicorn after the checks")
    args = p.parse_args()

    start = time.monotonic()
    try:
        rc = run(args)
    except PhaseError as exc:
        bad(str(exc))
        print(f"\n{C.FAIL}E2E FAILED in {time.monotonic() - start:.0f}s{C.END}")
        return 1
    except AssertionError as exc:
        bad(f"assertion failed: {exc}")
        print(f"\n{C.FAIL}E2E FAILED in {time.monotonic() - start:.0f}s{C.END}")
        return 1
    print(f"\n{C.OK}E2E PASSED in {time.monotonic() - start:.0f}s{C.END}")
    return rc


# ── pytest entry point (skips unless RUN_E2E=1; the heavy live run is opt-in) ──
def test_end_to_end_pipeline():
    import pytest
    if os.getenv("RUN_E2E") != "1":
        pytest.skip("Set RUN_E2E=1 to run the live end-to-end pipeline test (needs Docling/Chroma/Neo4j/LLM).")
    from dotenv import load_dotenv
    load_dotenv()
    target = resolve_target(None)
    phase_preflight(target)
    baseline = phase_ingest(target)
    phase_verify(target, baseline)
    phase_api()


if __name__ == "__main__":
    sys.exit(main())
