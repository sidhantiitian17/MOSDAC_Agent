"""Per-session abuse counter and temporary lockout (L5).

Counts guard-triggered events (injection, PII, off-topic, refusal) per session
within a rolling time window.  When the count exceeds the configured threshold
the session is locked out (the pipeline returns 429 instead of processing).

Thread-safe for the default single-worker deployment; for multi-process
deployments consider replacing _counters with Redis sorted sets.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 300  # 5-minute rolling window
_counters: dict[str, list[float]] = defaultdict(list)
_lock = Lock()


def record_event(session_id: str) -> None:
    """Record an abuse-relevant event for this session."""
    with _lock:
        _counters[session_id].append(time.monotonic())


def is_locked_out(session_id: str, threshold: int) -> bool:
    """Return True if the session has exceeded *threshold* events in the window."""
    with _lock:
        now = time.monotonic()
        recent = [t for t in _counters.get(session_id, []) if now - t <= _WINDOW_SECONDS]
        _counters[session_id] = recent
        locked = len(recent) >= threshold
        if locked:
            logger.warning("Session locked out after %d events in %ds window", len(recent), _WINDOW_SECONDS)
        return locked


def event_count(session_id: str) -> int:
    """Current number of abuse events in the rolling window for this session."""
    with _lock:
        now = time.monotonic()
        return len([t for t in _counters.get(session_id, []) if now - t <= _WINDOW_SECONDS])


def clear_session(session_id: str) -> None:
    with _lock:
        _counters.pop(session_id, None)
