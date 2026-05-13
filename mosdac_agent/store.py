"""Persistence layer for idempotency keys + audit trail.

Default implementation uses SQLite (zero-config, file-backed). To run
multi-replica or share state across machines, implement the `Store` Protocol
with Redis/Postgres/DynamoDB and inject it into the tool layer.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterator, List, Optional, Protocol

from mosdac_agent.config import mosdac_settings


class Store(Protocol):
    """Pluggable persistence — every method MUST be thread-safe."""

    def find_idempotent(self, key: str) -> Optional[str]: ...
    def save_idempotent(self, key: str, order_id: str) -> None: ...
    def record_order(self, user: str, order_id: str, payload: dict) -> None: ...
    def orders_in_last_hour(self, user: str) -> int: ...
    def list_orders(self, user: str, limit: int = 20) -> List[dict]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
    key        TEXT PRIMARY KEY,
    order_id   TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user       TEXT NOT NULL,
    order_id   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_user_ts ON orders_audit(user, created_at);
"""


@dataclass
class SqliteStore:
    """File-backed Store. Concurrency is serialised by a process-local RLock."""

    path: Path
    _lock: RLock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(str(self.path))
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def find_idempotent(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT order_id FROM idempotency WHERE key=?", (key,)
            ).fetchone()
        return row[0] if row else None

    def save_idempotent(self, key: str, order_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency(key, order_id, created_at) "
                "VALUES (?, ?, ?)",
                (key, order_id, time.time()),
            )

    def record_order(self, user: str, order_id: str, payload: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO orders_audit(user, order_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user, order_id, json.dumps(payload), time.time()),
            )

    def orders_in_last_hour(self, user: str) -> int:
        cutoff = time.time() - 3600
        with self._connect() as conn:
            (n,) = conn.execute(
                "SELECT COUNT(*) FROM orders_audit WHERE user=? AND created_at>?",
                (user, cutoff),
            ).fetchone()
        return int(n)

    def list_orders(self, user: str, limit: int = 20) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT order_id, payload, created_at FROM orders_audit "
                "WHERE user=? ORDER BY created_at DESC LIMIT ?",
                (user, limit),
            ).fetchall()
        return [
            {"order_id": r[0], "payload": json.loads(r[1]), "created_at": r[2]}
            for r in rows
        ]


class InMemoryStore:
    """Volatile Store. Useful for unit tests and single-process dev runs."""

    def __init__(self) -> None:
        self._idem: dict[str, str] = {}
        self._orders: List[dict] = []
        self._lock = RLock()

    def find_idempotent(self, key: str) -> Optional[str]:
        with self._lock:
            return self._idem.get(key)

    def save_idempotent(self, key: str, order_id: str) -> None:
        with self._lock:
            self._idem.setdefault(key, order_id)

    def record_order(self, user: str, order_id: str, payload: dict) -> None:
        with self._lock:
            self._orders.append(
                {
                    "user": user,
                    "order_id": order_id,
                    "payload": dict(payload),
                    "created_at": time.time(),
                }
            )

    def orders_in_last_hour(self, user: str) -> int:
        cutoff = time.time() - 3600
        with self._lock:
            return sum(
                1
                for o in self._orders
                if o["user"] == user and o["created_at"] > cutoff
            )

    def list_orders(self, user: str, limit: int = 20) -> List[dict]:
        with self._lock:
            mine = [o for o in self._orders if o["user"] == user]
        mine.sort(key=lambda o: o["created_at"], reverse=True)
        return [
            {
                "order_id": o["order_id"],
                "payload": o["payload"],
                "created_at": o["created_at"],
            }
            for o in mine[:limit]
        ]


def build_default_store() -> Store:
    """Return the SqliteStore configured via env. Override in tests."""
    return SqliteStore(path=mosdac_settings.db_path())
