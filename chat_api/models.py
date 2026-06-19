"""Pydantic request/response models for the chat API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str
    screenshot_base64: Optional[str] = None
    screenshot_mime: Optional[str] = "image/png"


class CitationItem(BaseModel):
    id: str
    source: str
    chunk_id: str
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    citations: List[CitationItem] = []
    grounded: bool = True
    refused: bool = False
