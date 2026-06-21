"""Optional answer cache for the chat service.

An FAQ-heavy public portal re-runs the full pipeline (embeddings + retrieval +
LLM) for popular questions every time. This cache short-circuits repeats.

Design choices that keep it *correct*:
  * Key = (normalized message, history hash, corpus version). Including the history
    hash means a follow-up that depends on the conversation is never served a
    cache entry from a different context; popular first-turn questions (empty
    history) still hit. Including the corpus version means a re-ingest + /reload
    invalidates stale answers.
  * Only GROUNDED, non-refused answers are stored (never cache a refusal).
  * TTL + LRU bounded; thread-safe; OFF by default (CHAT_API_ENABLE_ANSWER_CACHE).
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from typing import List, Optional, Tuple

_WS = re.compile(r"\s+")

# Bumped by /reload (or a detected re-ingest) so cached answers don't outlive the
# corpus they were grounded in.
_corpus_version = 0
_version_lock = threading.Lock()


def bump_corpus_version() -> int:
    global _corpus_version
    with _version_lock:
        _corpus_version += 1
        return _corpus_version


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


class AnswerCache:
    """Bounded TTL+LRU cache of (answer, citations) keyed by query+history+corpus."""

    def __init__(self, max_entries: int = 1000, ttl_seconds: int = 3600) -> None:
        self._max = max(1, max_entries)
        self._ttl = max(0, ttl_seconds)
        self._store: "OrderedDict[str, Tuple[float, str, List[dict]]]" = OrderedDict()
        self._lock = threading.Lock()

    def _key(self, message: str, history_prefix: str) -> str:
        h = hashlib.sha256(_norm(history_prefix).encode()).hexdigest()[:16]
        with _version_lock:
            ver = _corpus_version
        return f"v{ver}|{h}|{_norm(message)}"

    def get(self, message: str, history_prefix: str) -> Optional[Tuple[str, List[dict]]]:
        key = self._key(message, history_prefix)
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            ts, answer, citations = entry
            if self._ttl and (now - ts) > self._ttl:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return answer, list(citations)

    def put(self, message: str, history_prefix: str, answer: str, citations: List[dict]) -> None:
        key = self._key(message, history_prefix)
        with self._lock:
            self._store[key] = (time.monotonic(), answer, list(citations))
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
