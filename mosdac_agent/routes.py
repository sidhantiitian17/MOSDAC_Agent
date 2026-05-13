"""FastAPI router for /mosdac/* endpoints.

Mount this on the existing chat_api app (or any other FastAPI host) to add
agent-based order placement without touching the graph-RAG flow:

    from mosdac_agent.routes import build_mosdac_router
    app.include_router(build_mosdac_router(service))

The router exposes:
    GET    /mosdac/health           — liveness
    GET    /mosdac/config           — branding for the JS widget
    POST   /mosdac/chat             — primary chat endpoint
    DELETE /mosdac/chat/{session}   — clear conversation history
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from mosdac_agent.agent import MosdacAgentService
from mosdac_agent.config import mosdac_settings

logger = logging.getLogger(__name__)


class MosdacChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)


class MosdacChatResponse(BaseModel):
    answer: str
    session_id: str
    user: str


def _resolve_user(sso_header: Optional[str]) -> str:
    s = mosdac_settings
    if s.require_sso_header:
        if not sso_header:
            raise HTTPException(status_code=401, detail="SSO required")
        return sso_header
    return sso_header or s.sso_dev_user


def build_mosdac_router(service: MosdacAgentService) -> APIRouter:
    """Wire the MOSDAC routes onto a FastAPI router."""
    router = APIRouter(prefix=mosdac_settings.mosdac_route_prefix, tags=["mosdac"])

    @router.get("/health")
    def health():
        return {
            "status": "ok",
            "bot_name": mosdac_settings.bot_name,
            "mock_mode": mosdac_settings.mosdac_use_mock,
            "agent_use_local_tools": mosdac_settings.agent_use_local_tools,
        }

    @router.get("/config")
    def widget_config():
        return {
            "bot_name": mosdac_settings.bot_name,
            "title": f"{mosdac_settings.bot_name} — order assistant",
            "sftp_base_url": mosdac_settings.sftp_base_url,
        }

    @router.post("/chat", response_model=MosdacChatResponse)
    def chat(
        req: MosdacChatRequest,
        x_mosdac_user: Optional[str] = Header(default=None, alias="X-MOSDAC-User"),
    ):
        user = _resolve_user(x_mosdac_user)
        try:
            answer = service.chat(session_id=req.session_id, message=req.message)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("MOSDAC agent failure: %s", exc)
            raise HTTPException(
                status_code=500,
                detail=f"Agent error: {type(exc).__name__}",
            )
        return MosdacChatResponse(
            answer=answer, session_id=req.session_id, user=user
        )

    @router.delete("/chat/{session_id}")
    def clear(session_id: str):
        service.clear(session_id)
        return {"cleared": session_id}

    return router
