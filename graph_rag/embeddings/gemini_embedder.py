"""LangChain-compatible Gemini embedder. Set GEMINI_EMBEDDING_MODEL in .env."""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any

from langchain_core.embeddings import Embeddings

# gemini-embedding-001 free tier: 100 RPM, 1 500 RPD
# _BATCH_SIZE=50 halves the number of API calls vs the previous 25-text batch,
# preserving daily quota.  _CALL_INTERVAL=1.5 s → ~40 RPM, well inside the limit.
_BATCH_SIZE = 50      # texts per API call (Gemini allows up to 100)
_CALL_INTERVAL = 1.5  # seconds between API calls


class _GeminiEmbedder(Embeddings):
    """Rate-limited Gemini embedder.

    Processes texts in batches of _BATCH_SIZE and sleeps between API calls
    so the pipeline respects the free-tier RPM limit without exhausting
    the daily request quota.
    """

    def __init__(self, **kwargs: Any) -> None:
        try:
            from langchain_google_genai import GoogleGenerativeAIEmbeddings
        except ImportError as exc:
            raise ImportError(
                "langchain-google-genai not installed. "
                "Run: pip install langchain-google-genai"
            ) from exc
        self._inner = GoogleGenerativeAIEmbeddings(**kwargs)
        self._last_call: float = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _CALL_INTERVAL:
            time.sleep(_CALL_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            self._throttle()
            results.extend(self._inner.embed_documents(batch, batch_size=len(batch)))
        return results

    def embed_query(self, text: str) -> list[float]:
        self._throttle()
        return self._inner.embed_query(text)


@lru_cache(maxsize=1)
def get_embedder() -> Embeddings:
    """Return a singleton Gemini embeddings client."""
    # Import lazily so test monkeypatching of graph_rag.config.settings works
    from graph_rag.config import settings as _settings

    if not _settings.gemini_api_key or _settings.gemini_api_key == "missing":
        raise ValueError("GEMINI_API_KEY is not set in .env")

    return _GeminiEmbedder(
        model=_settings.gemini_embedding_model,
        google_api_key=_settings.gemini_api_key,
        task_type="RETRIEVAL_DOCUMENT",
    )
