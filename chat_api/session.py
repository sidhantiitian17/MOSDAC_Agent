"""Pluggable session storage for chat history.

Default backend: in-process dict (good for single-replica deployments).
Optional backend: Redis (set CHAT_API_SESSION_BACKEND=redis and CHAT_API_REDIS_URL).

Lifecycle (P0-2):
  * Both backends honour a TTL so idle sessions are dropped — bounding memory on
    a long-running server and giving a retention boundary for stored (redacted)
    turns. In-memory eviction is lazy + LRU-capped; Redis uses native key expiry.
  * ``build_session_store`` can REFUSE the in-memory backend (multi-replica safety)
    when CHAT_API_REQUIRE_PERSISTENT_SESSIONS=true.
"""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, Dict, List, Protocol

from chat_api.config import chat_api_settings

logger = logging.getLogger(__name__)


class SessionStore(Protocol):
    """Interface every session backend must satisfy."""

    def get(self, session_id: str) -> List[Dict[str, Any]]: ...
    def append(self, session_id: str, role: str, content: str) -> None: ...
    def clear(self, session_id: str) -> None: ...
    def trim(self, session_id: str, max_turns: int) -> None: ...
    def get_summary(self, session_id: str) -> str: ...
    def set_summary(self, session_id: str, summary: str) -> None: ...


class InMemorySessionStore:
    """Single-process store with TTL + LRU eviction.

    Lost on restart — fine for dev / single replica. ``ttl_seconds=0`` disables
    expiry and ``max_sessions=0`` disables the LRU cap (the defaults, so existing
    callers that construct ``InMemorySessionStore()`` keep the old unbounded
    behaviour). The factory wires the configured bounds for production.
    """

    def __init__(self, ttl_seconds: int = 0, max_sessions: int = 0) -> None:
        # OrderedDict gives O(1) LRU: most-recently-used moved to the end.
        self._sessions: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
        self._summaries: Dict[str, str] = {}
        self._last_access: Dict[str, float] = {}
        self._ttl = max(0, ttl_seconds)
        self._max = max(0, max_sessions)
        self._lock = Lock()

    # ── internal eviction ───────────────────────────────────────────────────
    def _expired(self, session_id: str, now: float) -> bool:
        if not self._ttl:
            return False
        ts = self._last_access.get(session_id)
        return ts is not None and (now - ts) > self._ttl

    def _evict_expired(self, now: float) -> None:
        if not self._ttl:
            return
        stale = [sid for sid, ts in self._last_access.items() if (now - ts) > self._ttl]
        for sid in stale:
            self._drop(sid)

    def _evict_over_cap(self) -> None:
        if not self._max:
            return
        while len(self._sessions) > self._max:
            oldest, _ = self._sessions.popitem(last=False)  # LRU end
            self._summaries.pop(oldest, None)
            self._last_access.pop(oldest, None)

    def _drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._summaries.pop(session_id, None)
        self._last_access.pop(session_id, None)

    def _touch(self, session_id: str, now: float) -> None:
        self._last_access[session_id] = now
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)

    # ── public API ──────────────────────────────────────────────────────────
    def get(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.monotonic()
            if self._expired(session_id, now):
                self._drop(session_id)
            return list(self._sessions.get(session_id, []))

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._evict_expired(now)
            self._sessions.setdefault(session_id, [])
            self._sessions[session_id].append({"role": role, "content": content})
            self._touch(session_id, now)
            self._evict_over_cap()

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._drop(session_id)

    def trim(self, session_id: str, max_turns: int) -> None:
        with self._lock:
            turns = self._sessions.get(session_id)
            if turns and len(turns) > max_turns * 2:
                self._sessions[session_id] = turns[-max_turns * 2:]

    def get_summary(self, session_id: str) -> str:
        with self._lock:
            return self._summaries.get(session_id, "")

    def set_summary(self, session_id: str, summary: str) -> None:
        with self._lock:
            self._summaries[session_id] = summary
            self._touch(session_id, time.monotonic())


class RedisSessionStore:
    """Redis-backed store — survives restarts and scales across replicas.

    Activated only when redis is installed AND CHAT_API_REDIS_URL is set. Every
    write refreshes the key TTL so idle sessions expire automatically.
    """

    def __init__(self, url: str, ttl_seconds: int = 0) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Redis backend requested but the 'redis' package is not installed. "
                "Run: pip install redis"
            ) from exc
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._key_prefix = "chat_api:session:"
        self._ttl = max(0, ttl_seconds)

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def _summary_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}:summary"

    def _refresh_ttl(self, *keys: str) -> None:
        if not self._ttl:
            return
        for k in keys:
            try:
                self._client.expire(k, self._ttl)
            except Exception:  # pragma: no cover - network best-effort
                pass

    def get(self, session_id: str) -> List[Dict[str, Any]]:
        raw = self._client.lrange(self._key(session_id), 0, -1)
        return [json.loads(r) for r in raw]

    def append(self, session_id: str, role: str, content: str) -> None:
        key = self._key(session_id)
        self._client.rpush(key, json.dumps({"role": role, "content": content}))
        self._refresh_ttl(key)

    def clear(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))
        self._client.delete(self._summary_key(session_id))

    def trim(self, session_id: str, max_turns: int) -> None:
        key = self._key(session_id)
        self._client.ltrim(key, -max_turns * 2, -1)
        self._refresh_ttl(key)

    def get_summary(self, session_id: str) -> str:
        return self._client.get(self._summary_key(session_id)) or ""

    def set_summary(self, session_id: str, summary: str) -> None:
        key = self._summary_key(session_id)
        self._client.set(key, summary)
        self._refresh_ttl(key)


def build_session_store() -> SessionStore:
    """Factory — chooses the backend declared in env config."""
    backend = chat_api_settings.session_backend.lower()
    ttl = chat_api_settings.session_ttl_seconds
    if backend == "redis":
        if not chat_api_settings.redis_url:
            raise RuntimeError(
                "CHAT_API_SESSION_BACKEND=redis but CHAT_API_REDIS_URL is empty"
            )
        logger.info("Using RedisSessionStore at %s (ttl=%ss)", chat_api_settings.redis_url, ttl)
        return RedisSessionStore(chat_api_settings.redis_url, ttl_seconds=ttl)

    if chat_api_settings.require_persistent_sessions:
        raise RuntimeError(
            "CHAT_API_REQUIRE_PERSISTENT_SESSIONS=true but session_backend is "
            "'memory'. Set CHAT_API_SESSION_BACKEND=redis (and CHAT_API_REDIS_URL) "
            "for a multi-replica / multi-worker deployment."
        )
    logger.info(
        "Using InMemorySessionStore (non-persistent; ttl=%ss, max_sessions=%s)",
        ttl, chat_api_settings.max_sessions,
    )
    return InMemorySessionStore(
        ttl_seconds=ttl, max_sessions=chat_api_settings.max_sessions
    )
