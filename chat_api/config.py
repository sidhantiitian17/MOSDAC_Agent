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
    # DELETE is required: the widget calls DELETE /conversations/{id} and
    # DELETE /chat/{session_id}. Omitting it makes cross-origin deletes fail the
    # CORS preflight (only masked when same-origin via the nginx /chatapi proxy).
    allowed_methods: str = "GET,POST,DELETE,OPTIONS"
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
    # OFF by default: the default chat model is text-only, so accepting screenshots
    # without a configured VLM just ships 8 MB uploads to a model that cannot see
    # them (misleading UX + wasted tokens). Turn this ON only together with a real
    # CHAT_API_VISION_MODEL — the image path is hard-gated on it (see service.py).
    enable_screenshot: bool = False
    max_screenshot_bytes: int = 8 * 1024 * 1024  # 8 MB
    # Identifier of the vision-capable model serving the screenshot path. Empty =
    # no VLM wired → the image path refuses with a clear message rather than
    # silently feeding the image to a text-only model (M6).
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
    # Trust the proxy-set client-IP headers (X-Real-IP, then the right-most
    # X-Forwarded-For hop) for the real client IP when running behind a known
    # reverse proxy / load balancer, so per-IP rate limiting keys on the actual
    # client and not the proxy. Only enable when the proxy is trusted AND it
    # overwrites these headers (nginx sets X-Real-IP $remote_addr) — otherwise a
    # client could spoof its rate-limit key. MUST be true behind the bundled
    # nginx config, or every client collapses into one shared rate-limit bucket.
    trust_forwarded_for: bool = False
    # Fail CLOSED if the rate limiter cannot be attached at startup (e.g. slowapi
    # missing). A public LLM endpoint must never boot silently without its primary
    # abuse/DoS control. Set false ONLY for local dev where you accept no limiter.
    require_rate_limit: bool = True

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
    # Public (browser) OIDC client id for the standalone SSO test harness
    # (static/sso-demo.html). Production Drupal gets the token server-side and does
    # NOT use this; it only saves passing ?client= to the demo page. Empty = the demo
    # falls back to its query-param / built-in default.
    keycloak_public_client: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_API_KEYCLOAK_PUBLIC_CLIENT", "KEYCLOAK_PUBLIC_CLIENT"),
    )
    # Audience(s) the access token must carry (comma-separated). Empty disables
    # the aud check (some Keycloak setups don't pin an audience on access tokens).
    keycloak_audience: str = ""
    # Allowed signing algorithms (comma-separated). RS256 only by default — an
    # explicit allow-list blocks the `alg:none` and HS/RS confusion attacks.
    jwt_algorithms: str = "RS256"
    # How long signing keys are cached before the JWKS endpoint is re-fetched.
    jwks_cache_seconds: int = 3600
    # Portal SSO login route the widget's "Sign in" button redirects an anonymous
    # user to (the portal/Drupal OIDC login that establishes a site-wide session and
    # then exposes the token to the page). Empty = no Sign-in button is shown.
    #   e.g. /user/login   (Drupal + OpenID Connect module)
    login_url: str = Field(
        default="",
        validation_alias=AliasChoices("CHAT_API_LOGIN_URL", "LOGIN_URL"),
    )

    # ── Conversation store (per-user chat history) ───────────────────────────
    # "sqlite"   — local file; CORRECT FOR A SINGLE REPLICA ONLY (a multi-replica
    #              deploy would split each user's history across replica files).
    # "postgres" — shared DB; the multi-replica / scalable backend (H4). Needs
    #              CHAT_API_POSTGRES_DSN and `pip install 'psycopg[binary,pool]'`.
    # "none"     — disables persistence (every request is anonymous/ephemeral).
    conv_store: str = "sqlite"            # "sqlite" | "postgres" | "none"
    sqlite_path: str = "./conversations.db"
    # libpq DSN for the postgres backend, e.g.
    #   postgresql://user:pass@db-host:5432/mosdac_chat
    postgres_dsn: str = ""

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

    def keycloak_url_and_realm(self) -> tuple[str, str]:
        """Split the issuer into (base_url, realm) so the SSO harness can self-configure.

        ``http://host:8081/realms/master`` -> (``http://host:8081``, ``master``).
        Returns ("", "") when no issuer is set. This is what stops static/sso-demo.html
        from defaulting to a realm/host that does not match the backend (a 401 trap).
        """
        iss = self.keycloak_issuer.rstrip("/")
        if "/realms/" in iss:
            base, realm = iss.split("/realms/", 1)
            return base, realm.split("/")[0]
        return iss, ""

    def methods_list(self) -> List[str]:
        return [m.strip() for m in self.allowed_methods.split(",") if m.strip()]

    def headers_list(self) -> List[str]:
        items = [h.strip() for h in self.allowed_headers.split(",") if h.strip()]
        return items or ["*"]


chat_api_settings = ChatAPISettings()
