"""LangChain ChatOpenAI pointed at the configured base URL.

By default this is LongCat (https://api.longcat.chat/openai).
To use a local Docker model (Ollama/vLLM/etc) set in .env:
    LONGCAT_API_BASE=http://localhost:11434/v1
    LONGCAT_MODEL=llama3
No code changes required.
"""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from graph_rag.config import settings


@lru_cache(maxsize=1)
def get_llm(
    temperature: float = 0.2,
    max_tokens: int = 2048,
    streaming: bool = False,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.longcat_model,
        api_key=settings.longcat_api_key,
        base_url=settings.longcat_api_base,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
    )
