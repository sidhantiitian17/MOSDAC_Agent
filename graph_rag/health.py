"""Shared dependency health probes (P0-4).

One implementation of "is each backend actually reachable?", reused by:
  * the CLI smoke test (``python main.py test``), and
  * the API readiness endpoint (``GET /ready``) wired to the LB/compose healthcheck.

Each probe is defensive (never raises) and returns ``(ok, detail)``. ``readiness``
aggregates them with a short result cache so a chatty load balancer can poll
``/ready`` without hammering Neo4j / the embedder on every request.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Tuple

logger = logging.getLogger(__name__)

ProbeResult = Tuple[bool, str]


def check_embedder() -> ProbeResult:
    try:
        from graph_rag.embeddings import get_embedder

        dim = len(get_embedder().embed_query("ping"))
        return True, f"dim={dim}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def check_chroma() -> ProbeResult:
    try:
        from graph_rag.embeddings import get_embedder
        from graph_rag.vector_store.chroma_store import ChromaStore

        store = ChromaStore(embedder=get_embedder())
        return True, f"count={store.count()}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def check_neo4j() -> ProbeResult:
    try:
        from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

        with Neo4jStore() as neo:
            return (True, "ping=ok") if neo.ping() else (False, "ping=failed")
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def check_llm() -> ProbeResult:
    try:
        from graph_rag.llm.tabby_client import get_llm

        resp = get_llm().invoke("Reply with just: OK")
        text = getattr(resp, "content", str(resp))
        return True, str(text)[:60]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


# Probe registry — name → callable. Extendable without touching callers.
PROBES: Dict[str, Callable[[], ProbeResult]] = {
    "embedder": check_embedder,
    "chroma": check_chroma,
    "neo4j": check_neo4j,
    "llm": check_llm,
}

_cache: Dict[str, object] = {}
_cache_lock = threading.Lock()


def readiness(cache_seconds: float = 5.0, include_llm: bool = False) -> dict:
    """Aggregate dependency health into a single readiness report.

    Args:
        cache_seconds: reuse the previous result for this long (LB anti-hammer).
        include_llm:   probe the LLM too (an extra generation call) — off by
                       default so /ready stays cheap; the LLM is best treated as a
                       soft dependency that degrades rather than fails readiness.

    Returns: {"ready": bool, "checks": {name: {"ok": bool, "detail": str}}}
    """
    now = time.monotonic()
    key = f"ready:{include_llm}"
    with _cache_lock:
        cached = _cache.get(key)
        if cached and (now - cached[0]) < cache_seconds:  # type: ignore[index]
            return cached[1]  # type: ignore[index]

    names = [n for n in PROBES if n != "llm" or include_llm]
    checks: Dict[str, dict] = {}
    ready = True
    for name in names:
        ok, detail = PROBES[name]()
        checks[name] = {"ok": ok, "detail": detail}
        # Embedder + vector store + graph are hard dependencies for grounded answers.
        if name in ("embedder", "chroma", "neo4j") and not ok:
            ready = False

    report = {"ready": ready, "checks": checks}
    with _cache_lock:
        _cache[key] = (now, report)
    return report
