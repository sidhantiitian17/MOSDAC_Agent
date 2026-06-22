"""PostgreSQL-backed conversation store (psycopg 3 + a connection pool).

Why this exists (H4): SQLite is a single local file. The moment the API runs as
more than one replica — the whole point of the "scalable" deployment — each
replica gets its OWN ``conversations.db`` and a user's history is split across
them. A shared PostgreSQL instance is the correct multi-replica backend.

This mirrors :class:`SQLiteConversationRepository` exactly in behaviour:
  * every statement that targets a specific conversation filters on BOTH
    ``user_id`` AND ``conversation_id`` (anti-IDOR — see repository.py), and
  * all SQL is parameterized (``%s`` placeholders), never string-formatted.

psycopg is imported lazily so a deployment that uses the default SQLite backend
never needs the driver installed (matches the Redis-backend pattern).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from chat_api.db.repository import (
    Conversation,
    ConversationNotFoundError,
    Message,
)

logger = logging.getLogger(__name__)

# `seq BIGSERIAL` gives a stable intra-conversation ordering (Postgres has no
# rowid), replacing the SQLite "ORDER BY created_at, rowid" tie-breaker.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_user_updated
    ON conversations (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    seq             BIGSERIAL,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_seq
    ON messages (conversation_id, seq);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresConversationRepository:
    """Synchronous, pooled, ownership-enforced conversation/message store."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        try:
            from psycopg.rows import dict_row  # noqa: F401  (validated import)
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - exercised only without the driver
            raise RuntimeError(
                "PostgreSQL backend requested but psycopg is not installed. "
                "Run: pip install 'psycopg[binary,pool]'"
            ) from exc

        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        with self._pool.connection() as conn:
            conn.execute(_SCHEMA)

    # ── reads ────────────────────────────────────────────────────────────────
    def list_conversations(self, user_id: str, limit: int = 50) -> List[Conversation]:
        from psycopg.rows import dict_row

        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT * FROM conversations WHERE user_id = %s "
                    "ORDER BY updated_at DESC LIMIT %s",
                    (user_id, limit),
                )
                rows = cur.fetchall()
        return [self._to_conversation(r) for r in rows]

    def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> Optional[Conversation]:
        from psycopg.rows import dict_row

        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT * FROM conversations WHERE id = %s AND user_id = %s",
                    (conversation_id, user_id),
                )
                row = cur.fetchone()
        return self._to_conversation(row) if row else None

    def list_messages(self, user_id: str, conversation_id: str) -> List[Message]:
        from psycopg.rows import dict_row

        # Join through conversations so messages are only visible to the owner.
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT m.* FROM messages m "
                    "JOIN conversations c ON m.conversation_id = c.id "
                    "WHERE c.id = %s AND c.user_id = %s "
                    "ORDER BY m.seq ASC",
                    (conversation_id, user_id),
                )
                rows = cur.fetchall()
        return [self._to_message(r) for r in rows]

    # ── writes ───────────────────────────────────────────────────────────────
    def create_conversation(self, user_id: str, title: str = "New chat") -> Conversation:
        now = _now()
        conv = Conversation(
            id=str(uuid.uuid4()), user_id=user_id, title=title,
            created_at=now, updated_at=now,
        )
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, user_id, title, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (conv.id, conv.user_id, conv.title, now, now),
            )
        return conv

    def append_message(
        self, user_id: str, conversation_id: str, role: str, content: str
    ) -> Message:
        now = _now()
        msg = Message(
            id=str(uuid.uuid4()), conversation_id=conversation_id,
            role=role, content=content, created_at=now,
        )
        with self._pool.connection() as conn:
            # Ownership gate: refuse to write into a conversation the user doesn't own.
            owned = conn.execute(
                "SELECT 1 FROM conversations WHERE id = %s AND user_id = %s",
                (conversation_id, user_id),
            ).fetchone()
            if owned is None:
                raise ConversationNotFoundError(conversation_id)
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (msg.id, conversation_id, role, content, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = %s WHERE id = %s AND user_id = %s",
                (now, conversation_id, user_id),
            )
        return msg

    def update_title(self, user_id: str, conversation_id: str, title: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE conversations SET title = %s WHERE id = %s AND user_id = %s",
                (title, conversation_id, user_id),
            )

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM conversations WHERE id = %s AND user_id = %s",
                (conversation_id, user_id),
            )
            return cur.rowcount > 0

    def close(self) -> None:
        self._pool.close()

    # ── row mappers ──────────────────────────────────────────────────────────
    @staticmethod
    def _to_conversation(row: dict) -> Conversation:
        return Conversation(
            id=row["id"], user_id=row["user_id"], title=row["title"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_message(row: dict) -> Message:
        return Message(
            id=row["id"], conversation_id=row["conversation_id"],
            role=row["role"], content=row["content"], created_at=row["created_at"],
        )
