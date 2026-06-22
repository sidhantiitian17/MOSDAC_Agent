"""FastAPI gateway - app factory pattern for multi-domain deployment.

Run:
    uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload

Architecture:
    create_app() composes the application from independently-swappable parts:
        - retriever  (HybridRetriever)
        - chain      (build_graph_rag_chain)
        - llm        (get_llm)
        - sessions   (build_session_store)
        - service    (ChatService)
        - router     (build_router)

Security / ops (L0 + L5 from guardplan.md):
    - Tightened CORS (explicit origin allowlist via env)
    - Security headers middleware (X-Content-Type-Options, X-Frame-Options, CSP, HSTS)
    - Request body size cap middleware (P1-2)
    - Rate limiting via slowapi, real-client-IP aware behind a trusted proxy (P1-1)
    - Optional API-key auth on /chat (P1-1)
    - UUID session-id validation
    - Lifespan warm-up (BM25/embeddings) + graceful driver shutdown (P1-4)
    - /ready deep readiness probe (P0-4), /metrics (Prometheus)
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from chat_api.config import chat_api_settings
from chat_api.db import build_conversation_repository
from chat_api.routes import build_router
from chat_api.service import ChatService
from chat_api.session import build_session_store

# ── Request-ID correlation (L3) ───────────────────────────────────────────────
# Stamped per request and injected into every log record so a single line in
# prod can be traced across the request lifecycle.
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIDLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [%(request_id)s] | %(message)s"
        )
    )
    handler.addFilter(_RequestIDLogFilter())
    root = logging.getLogger()
    # Idempotent: replace handlers so repeated create_app() calls (tests) don't stack.
    root.handlers = [handler]
    root.setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger("chat_api")


# ── Security headers middleware ───────────────────────────────────────────────

# Swagger UI bootstraps itself from an inline <script> and injects inline styles,
# so the strict app CSP (script-src 'self') blanks the page. These paths get a
# docs-scoped CSP that allows 'unsafe-inline' for the SAME-ORIGIN, self-hosted
# Swagger bundle only — every other route keeps the strict policy below.
_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})

_STRICT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "frame-ancestors 'none';"
)
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "frame-ancestors 'none';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds OWASP-recommended security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            _DOCS_CSP if request.url.path in _DOCS_PATHS else _STRICT_CSP
        )
        # Only set HSTS on HTTPS (header is ignored over HTTP)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ── Request-ID middleware (L3) ────────────────────────────────────────────────

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach an X-Request-ID to every request/response and the log context."""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = _request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


# ── Body-size cap middleware (P1-2 / L2) ──────────────────────────────────────

class BodySizeLimitMiddleware:
    """Pure-ASGI request body cap.

    Two layers (L2): a fast reject on a declared ``Content-Length`` over the cap,
    AND an enforced cap on the bytes actually streamed — so a chunked request that
    omits ``Content-Length`` cannot bypass the limit. The body is buffered up to
    the cap (bounded memory) and replayed to the app; oversize bodies get a 413
    before the application ever sees them.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self._max = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._max:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl:
            try:
                if int(cl) > self._max:
                    await self._reject_http(scope, send)
                    return
            except ValueError:
                pass

        # Buffer the body up to the cap; reject if exceeded (covers no-CL/chunked).
        body = bytearray()
        trailing = []
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                trailing.append(message)
                break
            body += message.get("body", b"")
            if len(body) > self._max:
                await self._reject_http(scope, send)
                return
            more = message.get("more_body", False)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            if trailing:
                return trailing.pop(0)
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject_http(scope, send) -> None:
        resp = PlainTextResponse("Request body too large.", status_code=413)
        await resp(scope, _no_receive, send)


async def _no_receive():  # pragma: no cover - a sent response never reads the body
    return {"type": "http.request", "body": b"", "more_body": False}


# ── Rate limiting (slowapi) ───────────────────────────────────────────────────

def _client_ip_key(request: Request) -> str:
    """Real client IP for rate limiting.

    When the deployment declares the proxy trusted (P1-1 / H1) we read the IP the
    *trusted proxy* stamped — NOT a client-supplied value. nginx sets
    ``X-Real-IP $remote_addr`` (a single, overwritten value), so we prefer that;
    failing that we take the RIGHT-MOST ``X-Forwarded-For`` hop, which is the one
    appended by the proxy (``$proxy_add_x_forwarded_for``). Taking the left-most
    entry would let a client forge its own rate-limit key by sending its own XFF.
    With trust disabled we use the socket peer.
    """
    if chat_api_settings.trust_forwarded_for:
        real_ip = request.headers.get("x-real-ip")
        if real_ip and real_ip.strip():
            return real_ip.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            if hops:
                return hops[-1]  # right-most = appended by the trusted proxy
    return request.client.host if request.client else "anonymous"


def _setup_rate_limiter(app: FastAPI):
    """Attach the slowapi rate limiter and return it (or None).

    Fails CLOSED (H2): if the limiter cannot be attached — typically because
    ``slowapi`` is missing — and ``CHAT_API_REQUIRE_RATE_LIMIT`` is set (default),
    startup RAISES rather than silently serving a public endpoint with no
    abuse/DoS control. Returning the limiter lets the router exempt health probes.
    """
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
        from slowapi.errors import RateLimitExceeded  # type: ignore
        from slowapi.middleware import SlowAPIMiddleware  # type: ignore

        from guardrails.config import guardrail_settings as gcfg

        rate = f"{gcfg.rate_limit_per_min}/minute"
        limiter = Limiter(key_func=_client_ip_key, default_limits=[rate])
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
        logger.info(
            "Rate limiter enabled: %s per IP (trust_xff=%s)",
            rate, chat_api_settings.trust_forwarded_for,
        )
        return limiter
    except Exception as exc:  # noqa: BLE001 — import or wiring failure
        if chat_api_settings.require_rate_limit:
            raise RuntimeError(
                "Rate limiting could not be enabled but CHAT_API_REQUIRE_RATE_LIMIT "
                "is true. Install slowapi (`pip install slowapi`) or, for local dev "
                f"only, set CHAT_API_REQUIRE_RATE_LIMIT=false. Cause: {exc}"
            ) from exc
        logger.warning(
            "Rate limiting DISABLED (slowapi unavailable: %s). "
            "This is unsafe for a public deployment.", exc,
        )
        return None


# ── Lifespan: warm caches on startup, release drivers on shutdown ─────────────

def _make_lifespan(retriever, repo=None):
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if chat_api_settings.enable_screenshot and not chat_api_settings.vision_model:
            logger.warning(
                "CHAT_API_ENABLE_SCREENSHOT is on but CHAT_API_VISION_MODEL is unset — "
                "the default chat model is text-only. Set a VLM or disable screenshots."
            )
        # Warm the keyword index (and embedding caches) so the FIRST user request
        # does not pay the cold-start cost (P1-4). Non-fatal: a dependency that is
        # down at boot just leaves /ready reporting not-ready.
        if retriever is not None and hasattr(retriever, "warm"):
            try:
                retriever.warm()
                logger.info("Warm-up complete (BM25 index built).")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Warm-up skipped (dependency unavailable): %s", exc)
        try:
            yield
        finally:
            # Graceful shutdown — close the Neo4j driver if the retriever holds one.
            with contextlib.suppress(Exception):
                graph = getattr(retriever, "_graph", None)
                store = getattr(graph, "_store", None)
                if store is not None and hasattr(store, "close"):
                    store.close()
                    logger.info("Neo4j driver closed on shutdown.")
            # Close the conversation store connection.
            if repo is not None and hasattr(repo, "close"):
                with contextlib.suppress(Exception):
                    repo.close()
                    logger.info("Conversation store closed on shutdown.")
    return lifespan


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(
    *,
    retriever=None,
    chain=None,
    llm=None,
    sessions=None,
    service: ChatService | None = None,
) -> FastAPI:
    """Application factory. Inject test doubles or alternate backends here."""
    if service is None:
        try:
            from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
            from graph_rag.llm.tabby_client import get_llm
            from graph_rag.retrieval.hybrid_retriever import HybridRetriever

            retriever = retriever or HybridRetriever()
            chain = chain or build_graph_rag_chain(retriever=retriever)
            llm = llm or get_llm()
            sessions = sessions or build_session_store()
            repo = build_conversation_repository()
            service = ChatService(
                retriever=retriever, chain=chain, llm=llm, sessions=sessions, repo=repo
            )
        except Exception as exc:
            # Surface a clear, actionable boot error (P2-6) instead of an opaque
            # import-time stack trace deep in a dependency.
            logger.error("ChatAPI failed to compose — check .env / dependencies: %s", exc)
            raise
    else:
        retriever = retriever or getattr(service, "_retriever", None)

    app = FastAPI(
        title=chat_api_settings.title,
        version=chat_api_settings.version,
        lifespan=_make_lifespan(retriever, getattr(service, "_repo", None)),
        # Disable the built-in docs: they pull Swagger UI / ReDoc from a public CDN,
        # which is blocked by our CSP and unreachable in an air-gapped deployment.
        # We re-mount Swagger UI below from self-hosted, vendored assets.
        docs_url=None,
        redoc_url=None,
    )

    # L0: Request-ID correlation + security headers + body-size cap
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=chat_api_settings.max_request_bytes)

    # L0: CORS - must never be wildcard in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=chat_api_settings.origins_list(),
        allow_methods=chat_api_settings.methods_list(),
        allow_headers=chat_api_settings.headers_list(),
        allow_credentials=True,
    )

    # L0: Rate limiting (fails closed unless CHAT_API_REQUIRE_RATE_LIMIT=false).
    limiter = _setup_rate_limiter(app)

    # Health/readiness/metrics probes are exempted from the per-IP budget (M7) so
    # frequent LB/orchestrator polling never trips the limiter and flaps readiness.
    app.include_router(build_router(service, limiter=limiter))

    # Serve the embeddable widget assets (graph-rag-chat-widget.js + shim) so the
    # repo's static/ folder is the single source of truth. Every Drupal site loads
    # the SAME file through its nginx /static/ proxy — no per-site file copies, so
    # a widget fix ships everywhere at once. Path is derived, not hardcoded.
    import os

    static_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static"
    )
    if os.path.isdir(static_dir):
        from fastapi.staticfiles import StaticFiles

        app.mount("/static", StaticFiles(directory=static_dir), name="static")
        logger.info("Serving widget assets from %s at /static", static_dir)

        # Self-hosted, offline-safe API docs. The Swagger UI bundle + CSS and the
        # favicon are vendored under static/vendor/ so /docs renders with NO public
        # CDN and NO outbound network — required for the air-gapped deployment and
        # consistent with the CSP. Only wired when the vendored assets are present.
        _swagger_js = os.path.join(static_dir, "vendor", "swagger", "swagger-ui-bundle.js")
        _favicon = os.path.join(static_dir, "vendor", "favicon.png")
        if os.path.isfile(_swagger_js):
            from fastapi.openapi.docs import (
                get_swagger_ui_html,
                get_swagger_ui_oauth2_redirect_html,
            )

            @app.get("/docs", include_in_schema=False)
            async def custom_swagger_ui_html():  # noqa: D401
                return get_swagger_ui_html(
                    openapi_url=app.openapi_url,
                    title=f"{app.title} — Swagger UI",
                    oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
                    swagger_js_url="/static/vendor/swagger/swagger-ui-bundle.js",
                    swagger_css_url="/static/vendor/swagger/swagger-ui.css",
                    swagger_favicon_url="/static/vendor/favicon.png",
                )

            @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
            async def swagger_ui_redirect():  # noqa: D401
                return get_swagger_ui_oauth2_redirect_html()

        # Serve the browser's implicit /favicon.ico request from the local asset so
        # it is neither a 404 nor blocked by the CSP (img-src 'self').
        if os.path.isfile(_favicon):

            @app.get("/favicon.ico", include_in_schema=False)
            async def favicon():  # noqa: D401
                return FileResponse(_favicon, media_type="image/png")

    logger.info(
        "ChatAPI booted: title=%r origins=%s screenshot=%s auth=%s metrics=%s",
        chat_api_settings.title,
        chat_api_settings.origins_list(),
        chat_api_settings.enable_screenshot,
        bool(chat_api_settings.api_key),
        chat_api_settings.enable_metrics,
    )
    return app


app = create_app()
