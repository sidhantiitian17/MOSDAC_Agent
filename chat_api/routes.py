"""HTTP route definitions - depends only on the ChatService abstraction."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from chat_api.config import chat_api_settings
from chat_api.models import ChatRequest, ChatResponse, CitationItem
from chat_api.service import ChatService

logger = logging.getLogger(__name__)


def _require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Optional caller auth (P1-1). No-op when CHAT_API_API_KEY is unset."""
    expected = chat_api_settings.api_key
    if not expected:
        return
    provided = x_api_key
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


def _require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Operator auth for /reload. Endpoint is disabled unless CHAT_API_ADMIN_TOKEN is set."""
    expected = chat_api_settings.admin_token
    if not expected:
        raise HTTPException(status_code=404, detail="Not found.")
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Invalid admin token.")


def build_router(service: ChatService) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health():
        """Liveness — cheap, never touches downstream deps (P0-4)."""
        return {
            "status": "ok",
            "title": chat_api_settings.title,
            "version": chat_api_settings.version,
            "bot_name": chat_api_settings.bot_name,
            "screenshot_enabled": chat_api_settings.enable_screenshot,
        }

    @router.get("/ready")
    def ready(response: Response):
        """Readiness — probes embedder / Chroma / Neo4j (P0-4). 503 when not ready
        so a load balancer stops routing to a replica with a dead dependency."""
        from graph_rag.health import readiness

        report = readiness(cache_seconds=5.0, include_llm=False)
        if not report.get("ready"):
            response.status_code = 503
        return report

    @router.get("/config")
    def widget_config():
        return {
            "title": chat_api_settings.title,
            "bot_name": chat_api_settings.bot_name,
            "screenshot_enabled": chat_api_settings.enable_screenshot,
            "max_screenshot_bytes": chat_api_settings.max_screenshot_bytes,
        }

    if chat_api_settings.enable_metrics:
        @router.get("/metrics")
        def metrics():
            from observability import CONTENT_TYPE, render_latest

            return Response(content=render_latest(), media_type=CONTENT_TYPE)

    @router.post("/chat", response_model=ChatResponse, dependencies=[Depends(_require_api_key)])
    def chat(req: ChatRequest):
        try:
            answer, raw_citations, grounded, refused = service.chat(
                session_id=req.session_id,
                message=req.message,
                screenshot_b64=req.screenshot_base64,
                screenshot_mime=req.screenshot_mime,
            )
            citations = [CitationItem(**c) for c in raw_citations] if raw_citations else []
            return ChatResponse(
                answer=answer,
                session_id=req.session_id,
                citations=citations,
                grounded=grounded,
                refused=refused,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("Chat error: %s", exc)
            _metric_inc("chat_requests_total", {"action": "error"})
            raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")

    @router.post("/chat/stream", dependencies=[Depends(_require_api_key)])
    def chat_stream(req: ChatRequest):
        """Server-Sent Events streaming (P1-6).

        Emits incremental ``token`` events for UX, then a single authoritative
        ``final`` event whose payload has passed the L4 output guard. Clients must
        treat the ``final`` payload — not the concatenated tokens — as the answer.
        """
        try:
            generator = service.chat_stream(
                session_id=req.session_id,
                message=req.message,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return StreamingResponse(generator, media_type="text/event-stream")

    @router.delete("/chat/{session_id}")
    def clear_session(session_id: str):
        service.clear_session(session_id)
        return {"cleared": session_id}

    @router.post("/reload", dependencies=[Depends(_require_admin)])
    def reload():
        """Hot-reload the keyword index / caches after a re-ingest (P1-4)."""
        return {"reloaded": service.reload()}

    return router


def _metric_inc(name: str, labels: dict | None = None) -> None:
    try:
        from observability import inc

        inc(name, labels)
    except Exception:
        pass
