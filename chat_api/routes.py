"""HTTP route definitions - depends only on the ChatService abstraction."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from chat_api.config import chat_api_settings
from chat_api.models import ChatRequest, ChatResponse, CitationItem
from chat_api.service import ChatService

logger = logging.getLogger(__name__)


def build_router(service: ChatService) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health():
        return {
            "status": "ok",
            "title": chat_api_settings.title,
            "version": chat_api_settings.version,
            "bot_name": chat_api_settings.bot_name,
            "screenshot_enabled": chat_api_settings.enable_screenshot,
        }

    @router.get("/config")
    def widget_config():
        return {
            "title": chat_api_settings.title,
            "bot_name": chat_api_settings.bot_name,
            "screenshot_enabled": chat_api_settings.enable_screenshot,
            "max_screenshot_bytes": chat_api_settings.max_screenshot_bytes,
        }

    @router.post("/chat", response_model=ChatResponse)
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
            raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")

    @router.delete("/chat/{session_id}")
    def clear_session(session_id: str):
        service.clear_session(session_id)
        return {"cleared": session_id}

    return router
