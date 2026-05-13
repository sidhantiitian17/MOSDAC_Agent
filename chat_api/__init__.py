"""Graph RAG Chat API — domain-agnostic FastAPI gateway.

Public surface:
    create_app   — application factory (use in tests / for custom wiring)
    app          — module-level FastAPI instance (uvicorn / Docker entrypoint)
    ChatService  — pure business logic, transport-agnostic
    ChatRequest, ChatResponse  — Pydantic request/response models
"""
from chat_api.main import app, create_app
from chat_api.models import ChatRequest, ChatResponse
from chat_api.service import ChatService

__all__ = ["app", "create_app", "ChatService", "ChatRequest", "ChatResponse"]
