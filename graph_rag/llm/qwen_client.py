"""LangChain client pointing at a local Qwen model served by Ollama or vLLM.

Qwen2.5-VL supports vision — screenshots are passed as base64 image blocks.

Set in .env:
    QWEN_API_BASE=http://localhost:11434/v1      # Ollama
    QWEN_MODEL=qwen2.5vl:7b                     # or qwen2.5:14b for text-only
    QWEN_API_KEY=ollama                          # Ollama ignores this but needs it
"""
from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI

from graph_rag.config import settings


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.2, max_tokens: int = 2048) -> ChatOpenAI:
    """Returns a ChatOpenAI instance pointed at the local Qwen endpoint."""
    return ChatOpenAI(
        model=settings.qwen_model,
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_api_base,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=False,
    )
