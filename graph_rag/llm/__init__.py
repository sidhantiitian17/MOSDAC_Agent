"""LLM abstraction — LongCat by default, any OpenAI-compatible endpoint via .env."""
from graph_rag.llm.longcat_client import get_llm

__all__ = ["get_llm"]
