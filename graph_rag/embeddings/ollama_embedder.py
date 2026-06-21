"""Embedder using Ollama's embedding API (bge-large).

All configuration comes from .env — nothing is hardcoded here:
    OLLAMA_BASE_URL=http://localhost:11434     # host:port only
    OLLAMA_EMBEDDING_MODEL=bge-large

Throughput (P0-1):
  * ``embed_documents`` uses Ollama's NATIVE BATCH endpoint (``/api/embed``),
    sending an array of inputs and getting all vectors back in ONE HTTP round-trip
    instead of N sequential calls. Falls back automatically to the legacy
    per-item ``/api/embeddings`` endpoint if the batch endpoint is unavailable
    (older Ollama), so the change is safe on any server version.
  * ``embed_query`` keeps a small process-level LRU cache. The same query is
    embedded several times per chat request (injection check, scope gate, vector
    search, passage rerank, graph rerank); caching makes all but the first free.

The path(s) are appended to OLLAMA_BASE_URL automatically. bge-large → 1024-dim.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from functools import lru_cache

from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

# Legacy single-item route (always present). The batch route is configurable.
_LEGACY_EMBED_PATH = "/api/embeddings"


class OllamaEmbedder(Embeddings):
    """bge-large (or any Ollama embedding model) via the native embeddings API."""

    def __init__(
        self,
        model: str,
        base_url: str,
        query_instruction: str = "",
        *,
        use_native_batch: bool = True,
        batch_path: str = "/api/embed",
        batch_size: int = 64,
        timeout_seconds: int = 120,
        query_cache_size: int = 512,
    ) -> None:
        if not base_url:
            raise ValueError("OLLAMA_BASE_URL is not set — configure it in .env.")
        self._model = model
        base = base_url.rstrip("/")
        # ``_url`` (singular) stays the legacy endpoint for backward compatibility
        # — tests and external callers reference it as the embedder's endpoint.
        self._url = base + _LEGACY_EMBED_PATH
        self._batch_url = base + (batch_path or "/api/embed")
        self._use_native_batch = use_native_batch
        # Once the batch endpoint 404s/errors we stop trying it for this instance.
        self._batch_ok = use_native_batch
        self._batch_size = max(1, batch_size)
        self._timeout = timeout_seconds
        # bge-style retrievers embed the QUERY with a task instruction prefix while
        # passages stay bare (asymmetric). Empty string disables it. Applied only
        # in embed_query so document embeddings are never prefixed.
        self._query_instruction = query_instruction or ""

        # Bounded LRU cache of query embeddings keyed on the final prompt string.
        self._cache_size = max(0, query_cache_size)
        self._query_cache: "OrderedDict[str, list[float]]" = OrderedDict()
        self._cache_lock = threading.Lock()

    # ── low-level HTTP ──────────────────────────────────────────────────────
    def _embed_one(self, text: str) -> list[float]:
        import requests

        resp = requests.post(
            self._url,
            json={"model": self._model, "prompt": text},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        embedding = data.get("embedding")
        if not embedding:
            raise ValueError(
                f"Ollama returned no embedding for model '{self._model}': {str(data)[:200]}"
            )
        return embedding

    def _embed_batch_native(self, texts: list[str]) -> list[list[float]]:
        """One round-trip for a batch via Ollama's /api/embed (input: array).

        Returns the embeddings in input order. Raises on any transport/shape
        error so the caller can fall back to the per-item endpoint.
        """
        import requests

        resp = requests.post(
            self._batch_url,
            json={"model": self._model, "input": texts},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            raise ValueError(
                f"Ollama /api/embed returned {len(embeddings) if embeddings else 0} "
                f"vectors for {len(texts)} inputs (model '{self._model}')."
            )
        return embeddings

    # ── public API ──────────────────────────────────────────────────────────
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        # Fast path: native batch endpoint, chunked defensively.
        if self._batch_ok:
            out: list[list[float]] = []
            try:
                for start in range(0, len(texts), self._batch_size):
                    chunk = texts[start : start + self._batch_size]
                    out.extend(self._embed_batch_native(chunk))
                return out
            except Exception as exc:
                # Disable native batch for this instance and fall through to the
                # legacy per-item path (older Ollama, or endpoint disabled).
                logger.info(
                    "Ollama native batch endpoint unavailable (%s); "
                    "falling back to per-item /api/embeddings.", exc,
                )
                self._batch_ok = False

        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        prompt = f"{self._query_instruction}{text}" if self._query_instruction else text
        if self._cache_size == 0:
            return self._embed_one(prompt)

        with self._cache_lock:
            cached = self._query_cache.get(prompt)
            if cached is not None:
                self._query_cache.move_to_end(prompt)
                return cached

        # Compute outside the lock (network call); tolerate a rare duplicate compute.
        vec = self._embed_one(prompt)
        with self._cache_lock:
            self._query_cache[prompt] = vec
            self._query_cache.move_to_end(prompt)
            while len(self._query_cache) > self._cache_size:
                self._query_cache.popitem(last=False)
        return vec

    def clear_query_cache(self) -> None:
        with self._cache_lock:
            self._query_cache.clear()


@lru_cache(maxsize=1)
def get_embedder() -> OllamaEmbedder:
    """Return the OllamaEmbedder singleton. Endpoint and model from .env only."""
    from graph_rag.config import settings

    return OllamaEmbedder(
        model=settings.ollama_embedding_model,
        base_url=settings.ollama_base_url,
        query_instruction=settings.embed_query_instruction,
        use_native_batch=settings.ollama_use_native_batch,
        batch_path=settings.ollama_embed_batch_path,
        batch_size=settings.ollama_embed_batch_size,
        timeout_seconds=settings.embed_timeout_seconds,
        query_cache_size=settings.embed_query_cache_size,
    )
