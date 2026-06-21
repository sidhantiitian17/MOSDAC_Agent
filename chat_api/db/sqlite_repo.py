"""SQLite-backed conversation store (stdlib ``sqlite3`` — no extra dependency).

The whole chat service is synchronous (FastAPI runs the sync route handlers in a
threadpool), so a synchronous repository is the natural fit. A single connection
opened with ``check_same_thread=False`` is shared across worker threads and guarded
by a re-entrant lock; SQLite's own locking plus WAL mode handle durability. This is
ample for the "small database" of per-user chat history.

Ownership is enforced in SQL: every statement that touches a specific conversation
carries ``WHERE ... user_id = ?`` (or joins through it for messages), so no query
can ever read or mutate another user's data.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from chat_api.db.repository import (
    Conversation,
    ConversationNotFoundError,
    Message,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user_updated
    ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_created
    ON messages (conversation_id, created_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class SQLiteConversationRepository:
    """Synchronous, thread-safe conversation/message store."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        # ":memory:" stays in-process (used by tests); a file path gets its parent
        # directory created so a fresh deployment "just works".
        if path not in (":memory:", "") and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── reads ────────────────────────────────────────────────────────────────
    def list_conversations(self, user_id: str, limit: int = 50) -> List[Conversation]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM conversations WHERE user_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._to_conversation(r) for r in rows]

    def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> Optional[Conversation]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
        return self._to_conversation(row) if row else None

    def list_messages(self, user_id: str, conversation_id: str) -> List[Message]:
        # Join through conversations so messages are only visible to the owner.
        with self._lock:
            rows = self._conn.execute(
                "SELECT m.* FROM messages m "
                "JOIN conversations c ON m.conversation_id = c.id "
                "WHERE c.id = ? AND c.user_id = ? "
                "ORDER BY m.created_at ASC, m.rowid ASC",
                (conversation_id, user_id),
            ).fetchall()
        return [self._to_message(r) for r in rows]

    # ── writes ───────────────────────────────────────────────────────────────
    def create_conversation(self, user_id: str, title: str = "New chat") -> Conversation:
        now = _now()
        conv = Conversation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conv.id, conv.user_id, conv.title, now.isoformat(), now.isoformat()),
            )
            self._conn.commit()
        return conv

    def append_message(
        self, user_id: str, conversation_id: str, role: str, content: str
    ) -> Message:
        now = _now()
        msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=now,
        )
        with self._lock:
            # Ownership gate: refuse to write into a conversation the user does not own.
            owned = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ).fetchone()
            if owned is None:
                raise ConversationNotFoundError(conversation_id)
            self._conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (msg.id, conversation_id, role, content, now.isoformat()),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
                (now.isoformat(), conversation_id, user_id),
            )
            self._conn.commit()
        return msg

    def update_title(self, user_id: str, conversation_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?",
                (title, conversation_id, user_id),
            )
            self._conn.commit()

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── row mappers ──────────────────────────────────────────────────────────
    @staticmethod
    def _to_conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
        )

    @staticmethod
    def _to_message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            conversation_id=row["conversation_id"],
            role=row["role"],
            content=row["content"],
            created_at=_parse(row["created_at"]),
        )
