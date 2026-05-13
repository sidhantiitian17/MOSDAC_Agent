"""FastAPI gateway — app factory pattern for multi-domain deployment.

Run:
    uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload

Architecture:
    create_app() composes the application from independently-swappable parts:
        - retriever  (HybridRetriever)
        - chain      (build_graph_rag_chain)
        - llm        (get_llm — Qwen by default, swap via env)
        - sessions   (build_session_store — memory or redis)
        - service    (ChatService — wraps the above)
        - router     (build_router — wires HTTP routes)

    All branding (title, CORS, bot_name) flows from chat_api/config.py which
    reads CHAT_API_* env vars. To deploy to a new domain, only change .env.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chat_api.config import chat_api_settings
from chat_api.routes import build_router
from chat_api.service import ChatService
from chat_api.session import build_session_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chat_api")


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=chat_api_settings.origins_list(),
        allow_methods=chat_api_settings.methods_list(),
        allow_headers=chat_api_settings.headers_list(),
    )

    if service is None:
        # Lazy imports so tests can construct create_app() without LLM dependencies.
        from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
        from graph_rag.llm.qwen_client import get_llm
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


# Module-level singleton for uvicorn / Docker entrypoint.
app = create_app()
