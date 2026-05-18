"""LangChain ChatOpenAI pointed at the configured base URL.

By default this is LongCat (https://api.longcat.chat/openai).
To use a local Docker model (Ollama/vLLM/etc) set in .env:
    LONGCAT_API_BASE=http://localhost:11434/v1
    LONGCAT_MODEL=llama3
No code changes required.
"""
from __future__ import annotations

import os
from functools import lru_cache

from langchain_openai import ChatOpenAI


@lru_cache(maxsize=1)
def get_llm(
    temperature: float = 0.2,
    max_tokens: int = 2048,
    streaming: bool = False,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("LONGCAT_MODEL", "LongCat-Flash-Chat"),
        api_key=os.getenv("LONGCAT_API_KEY", "missing"),
        base_url=os.getenv("LONGCAT_API_BASE", "https://api.longcat.chat/openai"),
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=streaming,
    )
