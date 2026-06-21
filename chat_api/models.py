"""Pydantic request/response models for the chat API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_validator


class ChatRequest(BaseModel):
    session_id: str
    message: str
    # Per-user history: null starts a NEW conversation; an existing id continues
    # one (ownership is enforced server-side). Ignored for anonymous callers.
    conversation_id: Optional[str] = None
    screenshot_base64: Optional[str] = None
    screenshot_mime: Optional[str] = "image/png"

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("session_id must be a valid UUID (e.g. 550e8400-e29b-41d4-a716-446655440000)")
        return v

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("conversation_id must be a valid UUID")
        return v

    @field_validator("message")
    @classmethod
    def _validate_message_length(cls, v: str) -> str:
        """Config-driven length cap (P1-2) — reject oversized messages at the edge."""
        from chat_api.config import chat_api_settings

        limit = chat_api_settings.max_message_chars
        if limit and len(v) > limit:
            raise ValueError(f"message exceeds the {limit}-character limit")
        return v

    @field_validator("screenshot_base64")
    @classmethod
    def _validate_screenshot_length(cls, v: Optional[str]) -> Optional[str]:
        """Reject an oversized base64 image before it is decoded into memory (P1-2)."""
        if v is None:
            return v
        from chat_api.config import chat_api_settings

        # base64 inflates raw bytes by ~4/3; cap the encoded string accordingly.
        max_chars = (chat_api_settings.max_screenshot_bytes * 4) // 3 + 16
        if len(v) > max_chars:
            raise ValueError("screenshot_base64 exceeds the configured size limit")
        return v


class CitationItem(BaseModel):
    id: str
    source: str
    chunk_id: str
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    # Set for authenticated users so the widget can keep sending it on follow-ups
    # and refresh the sidebar. None for anonymous/ephemeral sessions.
    conversation_id: Optional[str] = None
    citations: List[CitationItem] = []
    grounded: bool = True
    refused: bool = False


class ConversationOut(BaseModel):
    """A single row in the history sidebar."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime


class ConversationDetail(BaseModel):
    """A past conversation loaded into the chat window."""

    id: str
    title: str
    messages: List[MessageOut] = []
