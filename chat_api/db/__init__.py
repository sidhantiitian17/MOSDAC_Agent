"""Conversation persistence package + backend factory."""
from __future__ import annotations

import logging
from typing import Optional

from chat_api.config import chat_api_settings
from chat_api.db.repository import (
    Conversation,
    ConversationNotFoundError,
    ConversationRepository,
    Message,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Conversation",
    "ConversationNotFoundError",
    "ConversationRepository",
    "Message",
    "build_conversation_repository",
]


def build_conversation_repository() -> Optional[ConversationRepository]:
    """Factory — choose the backend declared in env config.

    ``"none"`` (or empty) disables persistence entirely: every request then
    behaves like an anonymous, ephemeral session regardless of auth.
    """
    store = (chat_api_settings.conv_store or "").strip().lower()
    if store in ("", "none"):
        logger.info("Conversation persistence disabled (CHAT_API_CONV_STORE=none).")
        return None
    if store == "sqlite":
        # Multi-replica guard (H4): the same signal that forces a shared session
        # backend (require_persistent_sessions) means a local SQLite file would
        # split per-user history across replicas. Refuse the unsafe combination.
        if chat_api_settings.require_persistent_sessions:
            raise RuntimeError(
                "CHAT_API_CONV_STORE=sqlite is unsafe with "
                "CHAT_API_REQUIRE_PERSISTENT_SESSIONS=true (multi-replica): a local "
                "SQLite file is per-replica and would split each user's history. "
                "Use CHAT_API_CONV_STORE=postgres with CHAT_API_POSTGRES_DSN."
            )
        from chat_api.db.sqlite_repo import SQLiteConversationRepository

        logger.info(
            "Using SQLiteConversationRepository at %s (single-replica only)",
            chat_api_settings.sqlite_path,
        )
        return SQLiteConversationRepository(chat_api_settings.sqlite_path)
    if store == "postgres":
        if not chat_api_settings.postgres_dsn:
            raise RuntimeError(
                "CHAT_API_CONV_STORE=postgres but CHAT_API_POSTGRES_DSN is empty."
            )
        from chat_api.db.postgres_repo import PostgresConversationRepository

        logger.info("Using PostgresConversationRepository (shared, multi-replica safe).")
        return PostgresConversationRepository(chat_api_settings.postgres_dsn)
    raise RuntimeError(
        f"Unknown CHAT_API_CONV_STORE={chat_api_settings.conv_store!r} "
        f"(expected 'sqlite', 'postgres', or 'none')."
    )
