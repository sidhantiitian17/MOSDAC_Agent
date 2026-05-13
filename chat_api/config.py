"""Domain-agnostic configuration for the chat API gateway.

All values come from environment variables so the same container image can be
deployed to MOSDAC, a sandbox portal, or any other host without code changes.
"""
from __future__ import annotations

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatAPISettings(BaseSettings):
    """Per-deployment settings — overridable via .env without code changes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CHAT_API_",
        extra="ignore",
        case_sensitive=False,
    )

    # Branding
    title: str = "Graph RAG Chatbot API"
    version: str = "1.0.0"
    bot_name: str = "Assistant"

    # CORS — comma-separated list of allowed origins
    allowed_origins: str = "http://localhost,http://127.0.0.1"
    allowed_methods: str = "GET,POST,DELETE,OPTIONS"
    allowed_headers: str = "*"

    # Session / history
    max_history_turns: int = 10
    session_backend: str = "memory"  # "memory" | "redis"
    redis_url: str = ""

    # Multimodal
    enable_screenshot: bool = True
    max_screenshot_bytes: int = 8 * 1024 * 1024  # 8 MB

    # Networking
    host: str = "0.0.0.0"
    port: int = 8000

    def origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def methods_list(self) -> List[str]:
        return [m.strip() for m in self.allowed_methods.split(",") if m.strip()]

    def headers_list(self) -> List[str]:
        items = [h.strip() for h in self.allowed_headers.split(",") if h.strip()]
        return items or ["*"]


chat_api_settings = ChatAPISettings()
