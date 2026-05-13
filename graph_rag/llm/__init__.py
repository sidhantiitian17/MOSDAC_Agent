"""LLM abstraction — supports LongCat (cloud) and Qwen (local Ollama/vLLM)."""
from graph_rag.llm.qwen_client import get_llm as get_qwen_llm
from graph_rag.llm.longcat_client import get_llm

__all__ = ["get_llm", "get_qwen_llm"]
