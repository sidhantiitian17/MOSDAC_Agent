"""Embedder using Nomic Embed Text via Tabby ML's OpenAI-compatible endpoint.

Connection settings are read from .env — never hardcode credentials here:
    NOMIC_MODEL_NAME=nomic-embed-text       # model name as loaded in Tabby ML
    NOMIC_BASE_URL=http://localhost:8080/v1  # defaults to TABBY_BASE_URL
    NOMIC_API_TOKEN=<your token>             # defaults to TABBY_API_TOKEN
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.embeddings import Embeddings

_EMBED_BATCH = 256  # Tabby ML embedding batch ceiling


class NomicEmbedder(Embeddings):
    """Nomic Embed Text served by Tabby ML via OpenAI-compatible /v1/embeddings."""

    def __init__(self, model: str, base_url: str, api_key: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai not installed. Run: pip install openai"
            ) from exc
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=texts)
        return [item.embedding for item in response.data]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for start in range(0, len(texts), _EMBED_BATCH):
            results.extend(self._embed_batch(texts[start : start + _EMBED_BATCH]))
        return results

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embeddings:
    """Return the singleton Nomic embedder. Config-driven — reads .env."""
    from graph_rag.config import settings

    if not settings.nomic_api_token:
        raise ValueError(
            "NOMIC_API_TOKEN (or TABBY_API_TOKEN) is not set. "
            "Add it to .env — the token must never be hardcoded in source."
        )

    return NomicEmbedder(
        model=settings.nomic_model_name,
        base_url=settings.nomic_base_url,
        api_key=settings.nomic_api_token,
    )
