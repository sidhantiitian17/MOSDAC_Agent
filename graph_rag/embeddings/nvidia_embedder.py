"""LangChain-compatible NVIDIA NIM embedder. Set NVIDIA_EMBEDDING_MODEL in .env."""
from __future__ import annotations

from functools import lru_cache

from langchain_core.embeddings import Embeddings


class _NvidiaEmbedder(Embeddings):
    """Thin wrapper around NVIDIAEmbeddings with lazy import."""

    def __init__(self, **kwargs) -> None:
        try:
            from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings
        except ImportError as exc:
            raise ImportError(
                "langchain-nvidia-ai-endpoints not installed. "
                "Run: pip install langchain-nvidia-ai-endpoints"
            ) from exc
        self._inner = NVIDIAEmbeddings(**kwargs)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(text)


@lru_cache(maxsize=1)
def get_embedder() -> Embeddings:
    """Return a singleton NVIDIA NIM embeddings client."""
    from graph_rag.config import settings as _settings

    if not _settings.nvidia_api_key or _settings.nvidia_api_key == "missing":
        raise ValueError("NVIDIA_API_KEY is not set in .env")

    return _NvidiaEmbedder(
        model=_settings.nvidia_embedding_model,
        api_key=_settings.nvidia_api_key,
        truncate="END",
    )
