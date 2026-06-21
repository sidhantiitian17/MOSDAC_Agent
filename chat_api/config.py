"""Domain-agnostic configuration for the chat API gateway.

All values come from environment variables so the same container image can be
deployed to MOSDAC, a sandbox portal, or any other host without code changes.
"""
from __future__ import annotations

from typing import List

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatAPISettings(BaseSettings):
    """Per-deployment settings — overridable via .env without code changes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CHAT_API_",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
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

    # ── SSO JWT field mapping (adapter pattern) ──────────────────────────────
    # Claim KEYS are NEVER hardcoded in the codebase: normalize_user_data() reads
    # the user id / username / email out of a decoded Keycloak token using these
    # names. A government portal that issues custom claims only edits .env.
    # Both the bare names (JWT_FIELD_ID) and the prefixed form
    # (CHAT_API_JWT_FIELD_ID) are accepted so the documented SSO block works as-is.
    jwt_field_id: str = Field(
        default="sub",
        validation_alias=AliasChoices("CHAT_API_JWT_FIELD_ID", "JWT_FIELD_ID"),
    )
    jwt_field_username: str = Field(
        default="preferred_username",
        validation_alias=AliasChoices("CHAT_API_JWT_FIELD_USERNAME", "JWT_FIELD_USERNAME"),
    )
    jwt_field_email: str = Field(
        default="email",
        validation_alias=AliasChoices("CHAT_API_JWT_FIELD_EMAIL", "JWT_FIELD_EMAIL"),
    )

    # ── Keycloak / OIDC (per-user auth via JWKS) ─────────────────────────────
    # Master switch. When false (default) the API behaves exactly as before:
    # /chat is anonymous/ephemeral and the /conversations endpoints 503. Turn on
    # only once an issuer/JWKS source is configured.
    auth_enabled: bool = False
    # Realm issuer, e.g. https://keycloak.example.org/realms/mosdac . The JWKS
    # URL is derived from it unless keycloak_jwks_url is set explicitly.
    keycloak_issuer: str = ""
    keycloak_jwks_url: str = ""
    # Audience(s) the access token must carry (comma-separated). Empty disables
    # the aud check (some Keycloak setups don't pin an audience on access tokens).
    keycloak_audience: str = ""
    # Allowed signing algorithms (comma-separated). RS256 only by default — an
    # explicit allow-list blocks the `alg:none` and HS/RS confusion attacks.
    jwt_algorithms: str = "RS256"
    # How long signing keys are cached before the JWKS endpoint is re-fetched.
    jwks_cache_seconds: int = 3600

    # ── Conversation store (per-user chat history) ───────────────────────────
    # "sqlite" persists conversations/messages for authenticated users so each
    # user gets their own history sidebar. "none" disables persistence entirely
    # (every request behaves like an anonymous, ephemeral session).
    conv_store: str = "sqlite"            # "sqlite" | "none"
    sqlite_path: str = "./conversations.db"

    # Networking
    host: str = "0.0.0.0"
    port: int = 8000

    def origins_list(self) -> List[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    def jwt_algorithms_list(self) -> List[str]:
        return [a.strip() for a in self.jwt_algorithms.split(",") if a.strip()]

    def keycloak_audiences_list(self) -> List[str]:
        return [a.strip() for a in self.keycloak_audience.split(",") if a.strip()]

    def effective_jwks_url(self) -> str:
        """Explicit JWKS URL, else derive the standard one from the issuer."""
        if self.keycloak_jwks_url:
            return self.keycloak_jwks_url
        if self.keycloak_issuer:
            return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"
        return ""

    def methods_list(self) -> List[str]:
        return [m.strip() for m in self.allowed_methods.split(",") if m.strip()]

    def headers_list(self) -> List[str]:
        items = [h.strip() for h in self.allowed_headers.split(",") if h.strip()]
        return items or ["*"]


chat_api_settings = ChatAPISettings()
