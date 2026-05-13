"""Pydantic request/response models for the chat API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str
    screenshot_base64: Optional[str] = None
    screenshot_mime: Optional[str] = "image/png"


class ChatResponse(BaseModel):
    answer: str
    session_id: str
