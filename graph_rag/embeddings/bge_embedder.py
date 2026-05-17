"""Offline local embedder using BAAI/bge-large-en-v1.5 via sentence-transformers.

Runs 100% offline once the model is cached. BGE_CACHE_DIR (.env) points at the
pre-downloaded model directory. Set TRANSFORMERS_OFFLINE=1 in .env for the
air-gapped ISRO setup so the model loads from cache only and fails fast with a
clear error if it is missing — instead of hanging on a network call.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from langchain_core.embeddings import Embeddings


def _is_model_cached(model_name: str, cache_dir: str | None) -> bool:
    """Return True if the model snapshot exists in the local HF hub cache."""
    slug = "models--" + model_name.replace("/", "--")
    if cache_dir:
        return (Path(cache_dir) / slug).exists()
    hf_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
    return (Path(hf_home) / "hub" / slug).exists()


class BGEEmbedder(Embeddings):
    """Local BGE embedder — no network calls after the first download."""

    def __init__(
        self,
        model_name: str,
        cache_dir: str | None = None,
        offline: bool = False,
    ) -> None:
        if offline:
            # Force huggingface_hub / transformers to load from the local cache only.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers not installed. "
                "Run: pip install sentence-transformers"
            ) from exc

        cached = _is_model_cached(model_name, cache_dir)
        try:
            # If already in the local cache, bypass all network calls entirely.
            self._model = SentenceTransformer(
                model_name,
                cache_folder=cache_dir,
                local_files_only=cached,
            )
        except Exception as exc:
            if not cached:
                cache_arg = f"'{cache_dir}'" if cache_dir else "'./models_cache'"
                raise RuntimeError(
                    f"Model '{model_name}' is not in the local cache and the download failed.\n"
                    f"Pre-download it once (with internet access) by running:\n"
                    f"  python -c \"from sentence_transformers import SentenceTransformer; "
                    f"SentenceTransformer('{model_name}', cache_folder={cache_arg})\"\n"
                    f"Then re-run ingestion."
                ) from exc
            raise

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, convert_to_numpy=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text], convert_to_numpy=True)[0].tolist()


@lru_cache(maxsize=1)
def get_embedder() -> Embeddings:
    """Return the singleton BGE embedder. Config-driven — reads .env."""
    from graph_rag.config import settings

    return BGEEmbedder(
        model_name=settings.bge_model_name,
        cache_dir=settings.bge_cache_dir or None,
        offline=settings.transformers_offline,
    )
