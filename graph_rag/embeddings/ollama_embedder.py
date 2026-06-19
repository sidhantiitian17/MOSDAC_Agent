"""Embedder using Ollama's native /api/embeddings endpoint (bge-large).

All configuration comes from .env — nothing is hardcoded here:
    OLLAMA_BASE_URL=http://localhost:11434     # host:port only
    OLLAMA_EMBEDDING_MODEL=bge-large

The /api/embeddings path is appended automatically by the embedder.
bge-large produces 1024-dimensional vectors.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

# Route appended to OLLAMA_BASE_URL. The host:port always comes from settings.
_EMBED_PATH = "/api/embeddings"
_TIMEOUT_SECONDS = 120


class OllamaEmbedder(Embeddings):
    """bge-large (or any Ollama embedding model) via the native embeddings API."""

    def __init__(self, model: str, base_url: str) -> None:
        if not base_url:
            raise ValueError("OLLAMA_BASE_URL is not set — configure it in .env.")
        self._model = model
        self._url = base_url.rstrip("/") + _EMBED_PATH

    def _embed_one(self, text: str) -> list[float]:
        import requests

        resp = requests.post(
            self._url,
            json={"model": self._model, "prompt": text},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        embedding = data.get("embedding")
        if not embedding:
            raise ValueError(
                f"Ollama returned no embedding for model '{self._model}': {str(data)[:200]}"
            )
        return embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


@lru_cache(maxsize=1)
def get_embedder() -> OllamaEmbedder:
    """Return the OllamaEmbedder singleton. Endpoint and model from .env only."""
    from graph_rag.config import settings

    return OllamaEmbedder(
        model=settings.ollama_embedding_model,
        base_url=settings.ollama_base_url,
    )
