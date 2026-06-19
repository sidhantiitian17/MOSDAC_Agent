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

Security (L0 + L5 from guardplan.md):
    - Tightened CORS (explicit origin allowlist via env)
    - Security headers middleware (X-Content-Type-Options, X-Frame-Options, CSP, HSTS)
    - Rate limiting via slowapi (per IP + per session)
    - Request body size cap
    - UUID session-id validation
"""
from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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


# ── Rate limiting (slowapi) ───────────────────────────────────────────────────

def _setup_rate_limiter(app: FastAPI) -> None:
    """Attach slowapi rate limiter if the package is installed."""
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore
        from slowapi.errors import RateLimitExceeded  # type: ignore
        from slowapi.util import get_remote_address  # type: ignore

        from guardrails.config import guardrail_settings as gcfg

        rate = f"{gcfg.rate_limit_per_min}/minute"
        limiter = Limiter(key_func=get_remote_address, default_limits=[rate])
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        logger.info("Rate limiter enabled: %s per IP", rate)
    except ImportError:
        logger.warning(
            "slowapi not installed — rate limiting disabled. "
            "Run: pip install slowapi"
        )


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
    app = FastAPI(
        title=chat_api_settings.title,
        version=chat_api_settings.version,
    )

    # L0: Security headers
    app.add_middleware(SecurityHeadersMiddleware)

    # L0: CORS - must never be wildcard in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=chat_api_settings.origins_list(),
        allow_methods=chat_api_settings.methods_list(),
        allow_headers=chat_api_settings.headers_list(),
        allow_credentials=False,
    )

    # L0: Rate limiting
    _setup_rate_limiter(app)

    if service is None:
        from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
        from graph_rag.llm.tabby_client import get_llm
        from graph_rag.retrieval.hybrid_retriever import HybridRetriever

        retriever = retriever or HybridRetriever()
        chain = chain or build_graph_rag_chain(retriever=retriever)
        llm = llm or get_llm()
        sessions = sessions or build_session_store()
        service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)

    app.include_router(build_router(service))
    logger.info(
        "ChatAPI booted: title=%r origins=%s screenshot=%s",
        chat_api_settings.title,
        chat_api_settings.origins_list(),
        chat_api_settings.enable_screenshot,
    )
    return app


app = create_app()
