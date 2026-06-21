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

    # CORS — comma-separated list of allowed origins (exact-match including port).
    # Env var CHAT_API_ALLOWED_ORIGINS overrides this default at runtime.
    allowed_origins: str = (
        "http://localhost,"
        "http://localhost:3000,"
        "http://localhost:5173,"
        "http://localhost:8080,"
        "http://127.0.0.1,"
        "http://127.0.0.1:3000,"
        "https://mosdac.gov.in,"
        "https://www.mosdac.gov.in"
        # Add MOSDAC subdomains here if needed, e.g.:
        # ",https://vedas.mosdac.gov.in"
    )
    allowed_methods: str = "GET,POST,OPTIONS"
    allowed_headers: str = "Content-Type,Authorization,Accept"

    # Session / history
    max_history_turns: int = 10
    session_backend: str = "memory"  # "memory" | "redis"
    redis_url: str = ""
    # ── Session lifecycle (P0-2) ────────────────────────────────────────────
    # TTL after which an idle session's history is dropped (in-memory eviction
    # AND Redis key expiry). 0 disables TTL. Bounds memory on a long-running
    # server and gives a privacy retention boundary for stored (redacted) turns.
    session_ttl_seconds: int = 86400
    # Hard cap on concurrently retained in-memory sessions (LRU-evicted past this).
    # Backstop against unbounded growth from rotating client session_ids. 0 = no cap.
    max_sessions: int = 50000
    # When true, the memory backend is refused at startup — forces Redis so a
    # multi-replica / multi-worker deployment never silently splits session state.
    require_persistent_sessions: bool = False

    # Multimodal
    enable_screenshot: bool = True
    max_screenshot_bytes: int = 8 * 1024 * 1024  # 8 MB
    # Identifier of the vision-capable model serving the screenshot path. Empty =
    # no VLM wired; a startup warning fires and operators should set
    # CHAT_API_ENABLE_SCREENSHOT=false until a real VLM backend is configured
    # (the default chat model is text-only). Informational/guard for P0-3.
    vision_model: str = ""

    # ── Request limits (P1-2) ───────────────────────────────────────────────
    # Hard ceiling on the raw request body (rejected by middleware on
    # Content-Length BEFORE the body is read into memory) and on the message
    # length (rejected at the model layer). Prevents trivial memory-exhaustion.
    max_request_bytes: int = 12 * 1024 * 1024   # ~8 MB image + base64 overhead
    max_message_chars: int = 8000

    # ── Edge / auth (P1-1) ──────────────────────────────────────────────────
    # When set, every /chat request must carry this token (X-API-Key or
    # Authorization: Bearer). Empty = open endpoint (public-portal default).
    api_key: str = ""
    # Trust X-Forwarded-For for the real client IP when running behind a known
    # reverse proxy / load balancer, so per-IP rate limiting keys on the actual
    # client and not the proxy. Only enable when the proxy is trusted.
    trust_forwarded_for: bool = False

    # ── Answer cache (optional) ─────────────────────────────────────────────
    # Short-circuit repeated FAQ questions. Only grounded, non-refused answers are
    # cached, keyed on query + history + corpus version (a /reload invalidates it).
    enable_answer_cache: bool = False
    answer_cache_size: int = 1000
    answer_cache_ttl_seconds: int = 3600

    # ── Observability / ops ─────────────────────────────────────────────────
    enable_metrics: bool = True            # expose GET /metrics
    # Admin token guarding POST /reload (BM25 / corpus hot-reload). Empty disables
    # the endpoint entirely. Keep distinct from api_key (operator vs caller).
    admin_token: str = ""

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
