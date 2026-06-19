"""Pluggable session storage for chat history.

Default backend: in-process dict (good for single-replica deployments).
Optional backend: Redis (set CHAT_API_SESSION_BACKEND=redis and CHAT_API_REDIS_URL).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
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
    """Single-process dict store. Lost on restart — fine for dev / single replica."""

    def __init__(self) -> None:
        self._sessions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._summaries: Dict[str, str] = {}

    def get(self, session_id: str) -> List[Dict[str, Any]]:
        return self._sessions[session_id]

    def append(self, session_id: str, role: str, content: str) -> None:
        self._sessions[session_id].append({"role": role, "content": content})

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._summaries.pop(session_id, None)

    def trim(self, session_id: str, max_turns: int) -> None:
        if len(self._sessions[session_id]) > max_turns * 2:
            self._sessions[session_id] = self._sessions[session_id][-max_turns * 2:]

    def get_summary(self, session_id: str) -> str:
        return self._summaries.get(session_id, "")

    def set_summary(self, session_id: str, summary: str) -> None:
        self._summaries[session_id] = summary


class RedisSessionStore:
    """Redis-backed store — survives restarts and scales across replicas.

    Activated only when redis is installed AND CHAT_API_REDIS_URL is set.
    """

    def __init__(self, url: str) -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "Redis backend requested but the 'redis' package is not installed. "
                "Run: pip install redis"
            ) from exc
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._key_prefix = "chat_api:session:"

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def _summary_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}:summary"

    def get(self, session_id: str) -> List[Dict[str, Any]]:
        raw = self._client.lrange(self._key(session_id), 0, -1)
        return [json.loads(r) for r in raw]

    def append(self, session_id: str, role: str, content: str) -> None:
        self._client.rpush(self._key(session_id), json.dumps({"role": role, "content": content}))

    def clear(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))
        self._client.delete(self._summary_key(session_id))

    def trim(self, session_id: str, max_turns: int) -> None:
        self._client.ltrim(self._key(session_id), -max_turns * 2, -1)

    def get_summary(self, session_id: str) -> str:
        return self._client.get(self._summary_key(session_id)) or ""

    def set_summary(self, session_id: str, summary: str) -> None:
        self._client.set(self._summary_key(session_id), summary)


def build_session_store() -> SessionStore:
    """Factory — chooses the backend declared in env config."""
    backend = chat_api_settings.session_backend.lower()
    if backend == "redis":
        if not chat_api_settings.redis_url:
            raise RuntimeError(
                "CHAT_API_SESSION_BACKEND=redis but CHAT_API_REDIS_URL is empty"
            )
        logger.info("Using RedisSessionStore at %s", chat_api_settings.redis_url)
        return RedisSessionStore(chat_api_settings.redis_url)
    logger.info("Using InMemorySessionStore (non-persistent)")
    return InMemorySessionStore()
