"""Conversation persistence interface — backend-agnostic.

Every method that targets a specific conversation takes BOTH ``user_id`` and
``conversation_id`` and filters on both. There is deliberately NO "fetch by
conversation id alone" method: a forged id belonging to another user always
resolves to ``None`` / no-op, which the API turns into a 404. This is the single
most important security invariant of the per-user history feature (anti-IDOR).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol, runtime_checkable


@dataclass
class Conversation:
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Message:
    id: str
    conversation_id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime


class ConversationNotFoundError(Exception):
    """Raised when a conversation does not exist OR is not owned by the user.

    The two cases are intentionally indistinguishable to the caller so the API
    cannot be used to probe for the existence of other users' conversations.
    """


@runtime_checkable
class ConversationRepository(Protocol):
    """Interface every conversation backend must satisfy."""

    def list_conversations(self, user_id: str, limit: int = 50) -> List[Conversation]: ...

    def create_conversation(self, user_id: str, title: str = "New chat") -> Conversation: ...

    def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> Optional[Conversation]: ...

    def list_messages(self, user_id: str, conversation_id: str) -> List[Message]: ...

    def append_message(
        self, user_id: str, conversation_id: str, role: str, content: str
    ) -> Message: ...

    def update_title(self, user_id: str, conversation_id: str, title: str) -> None: ...

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool: ...

    def close(self) -> None: ...
