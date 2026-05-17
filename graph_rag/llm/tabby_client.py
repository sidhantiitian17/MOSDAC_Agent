"""LangChain client for Tabby ML — an OpenAI-compatible LLM endpoint.

Tabby ML REQUIRES streaming=True; non-streaming calls time out silently
(confirmed by test_tabby.py, which uses .stream() exclusively).

Every connection setting comes from .env — never hardcode the token:
    TABBY_BASE_URL=http://localhost:8080/v1     # home development
    TABBY_API_TOKEN=<your token>
    TABBY_MODEL=Qwen2-1.5B-Instruct

    # ISRO production — swap the values above (no code change needed):
    # TABBY_BASE_URL=http://192.168.100.101:8080/v1
    # TABBY_API_TOKEN=<isro token>
"""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from graph_rag.config import settings


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.1, max_tokens: int = 2048) -> ChatOpenAI:
    """Return a ChatOpenAI instance pointed at the local Tabby ML endpoint.

    Reads TABBY_BASE_URL / TABBY_API_TOKEN / TABBY_MODEL from .env.
    """
    if not settings.tabby_api_token:
        raise ValueError(
            "TABBY_API_TOKEN is not set. Add it to .env — "
            "the Tabby token must never be hardcoded in source."
        )
    return ChatOpenAI(
        model=settings.tabby_model,
        api_key=settings.tabby_api_token,
        base_url=settings.tabby_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=True,  # Tabby ML requires streaming or calls time out
    )
