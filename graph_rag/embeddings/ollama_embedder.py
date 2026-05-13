"""Offline embedder via Ollama — pull nomic-embed-text once, no NVIDIA key needed."""
from __future__ import annotations

from functools import lru_cache

from langchain_community.embeddings import OllamaEmbeddings

from graph_rag.config import settings


@lru_cache(maxsize=1)
def get_embedder():
    """Offline embedder via Ollama — pull nomic-embed-text once."""
    base = settings.qwen_api_base.replace("/v1", "")
    return OllamaEmbeddings(model="nomic-embed-text", base_url=base)
