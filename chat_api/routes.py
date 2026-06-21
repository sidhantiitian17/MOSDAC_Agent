"""HTTP route definitions - depends only on the ChatService abstraction."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
)
from fastapi.responses import StreamingResponse

from chat_api.auth import NormalizedUser, get_current_user, get_optional_user
from chat_api.config import chat_api_settings
from chat_api.db.repository import ConversationNotFoundError
from chat_api.models import (
    ChatRequest,
    ChatResponse,
    CitationItem,
    ConversationDetail,
    ConversationOut,
    MessageOut,
)
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
    def chat(
        req: ChatRequest,
        background: BackgroundTasks,
        user: Optional[NormalizedUser] = Depends(get_optional_user),
    ):
        try:
            if user is None:
                # Anonymous / ephemeral — unchanged behaviour, no DB, no history.
                answer, raw_citations, grounded, refused = service.chat(
                    session_id=req.session_id,
                    message=req.message,
                    screenshot_b64=req.screenshot_base64,
                    screenshot_mime=req.screenshot_mime,
                )
                conversation_id = None
            else:
                # Authenticated — persist per-user history, return the conversation id.
                answer, raw_citations, grounded, refused, conversation_id = (
                    service.chat_authenticated(
                        user=user,
                        session_id=req.session_id,
                        message=req.message,
                        conversation_id=req.conversation_id,
                        screenshot_b64=req.screenshot_base64,
                        screenshot_mime=req.screenshot_mime,
                        background=background,
                    )
                )
            citations = [CitationItem(**c) for c in raw_citations] if raw_citations else []
            return ChatResponse(
                answer=answer,
                session_id=req.session_id,
                conversation_id=conversation_id,
                citations=citations,
                grounded=grounded,
                refused=refused,
            )
        except ConversationNotFoundError:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("Chat error: %s", exc)
            _metric_inc("chat_requests_total", {"action": "error"})
            raise HTTPException(status_code=500, detail="An internal error occurred. Please try again.")

    @router.post("/chat/stream", dependencies=[Depends(_require_api_key)])
    def chat_stream(
        req: ChatRequest,
        background: BackgroundTasks,
        user: Optional[NormalizedUser] = Depends(get_optional_user),
    ):
        """Server-Sent Events streaming (P1-6).

        Emits incremental ``token`` events for UX, then a single authoritative
        ``final`` event whose payload has passed the L4 output guard. Clients must
        treat the ``final`` payload — not the concatenated tokens — as the answer.
        """
        try:
            generator = service.chat_stream(
                session_id=req.session_id,
                message=req.message,
                user=user,
                conversation_id=req.conversation_id,
                background=background,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return StreamingResponse(generator, media_type="text/event-stream")

    @router.get("/conversations", response_model=List[ConversationOut])
    def list_conversations(user: NormalizedUser = Depends(get_current_user)):
        """History sidebar — the current user's conversations, most recent first."""
        convs = service.list_conversations(user.id)
        return [
            ConversationOut(
                id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at
            )
            for c in convs
        ]

    @router.get(
        "/conversations/{conversation_id}/messages",
        response_model=ConversationDetail,
    )
    def get_conversation_messages(
        conversation_id: str, user: NormalizedUser = Depends(get_current_user)
    ):
        """Load a past conversation into the chat window (ownership-checked)."""
        result = service.get_conversation_with_messages(user.id, conversation_id)
        if result is None:
            # 404 (not 403) so the API cannot be used to probe for others' ids.
            raise HTTPException(status_code=404, detail="Conversation not found.")
        conv, messages = result
        return ConversationDetail(
            id=conv.id,
            title=conv.title,
            messages=[
                MessageOut(role=m.role, content=m.content, created_at=m.created_at)
                for m in messages
            ],
        )

    @router.delete("/conversations/{conversation_id}")
    def delete_conversation(
        conversation_id: str, user: NormalizedUser = Depends(get_current_user)
    ):
        if not service.delete_conversation(user.id, conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"deleted": conversation_id}

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
