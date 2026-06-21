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
        from chat_api.db.sqlite_repo import SQLiteConversationRepository

        logger.info(
            "Using SQLiteConversationRepository at %s", chat_api_settings.sqlite_path
        )
        return SQLiteConversationRepository(chat_api_settings.sqlite_path)
    raise RuntimeError(
        f"Unknown CHAT_API_CONV_STORE={chat_api_settings.conv_store!r} "
        f"(expected 'sqlite' or 'none')."
    )
