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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from chat_api.config import chat_api_settings
from chat_api.routes import build_router
from chat_api.service import ChatService
from chat_api.session import build_session_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chat_api")


# ── Security headers middleware ───────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds OWASP-recommended security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none';"
        )
        # Only set HSTS on HTTPS (header is ignored over HTTP)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ── Body-size cap middleware (P1-2) ───────────────────────────────────────────

class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests on Content-Length BEFORE the body is read."""

    def __init__(self, app, max_bytes: int) -> None:
        super().__init__(app)
        self._max = max_bytes

    async def dispatch(self, request: Request, call_next):
        if self._max:
            cl = request.headers.get("content-length")
            if cl:
                try:
                    if int(cl) > self._max:
                        return PlainTextResponse("Request body too large.", status_code=413)
                except ValueError:
                    pass
        return await call_next(request)


# ── Rate limiting (slowapi) ───────────────────────────────────────────────────

def _client_ip_key(request: Request) -> str:
    """Real client IP for rate limiting. Honours X-Forwarded-For only when the
    deployment declares the proxy trusted (P1-1), else uses the socket peer."""
    if chat_api_settings.trust_forwarded_for:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "anonymous"


def _setup_rate_limiter(app: FastAPI) -> None:
    """Attach slowapi rate limiter if the package is installed."""
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
    except ImportError:
        logger.warning(
            "slowapi not installed — rate limiting disabled. "
            "Run: pip install slowapi"
        )


# ── Lifespan: warm caches on startup, release drivers on shutdown ─────────────

def _make_lifespan(retriever):
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
            service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)
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
        lifespan=_make_lifespan(retriever),
    )

    # L0: Security headers + body-size cap
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

    # L0: Rate limiting
    _setup_rate_limiter(app)

    app.include_router(build_router(service))
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
