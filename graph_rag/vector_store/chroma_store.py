"""ChromaDB wrapper — persistent, idempotent (skip already-indexed chunk_ids)."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from graph_rag.config import settings

logger = logging.getLogger(__name__)


class ChromaStore:
    def __init__(
        self,
        embedder: Embeddings | None = None,
        collection_name: str | None = None,
        persist_dir: str | None = None,
    ):
        try:
            from langchain_chroma import Chroma
        except ImportError as exc:
            raise ImportError(
                "langchain-chroma not installed. Run: pip install langchain-chroma chromadb"
            ) from exc

        self._embedder = embedder
        self._collection_name = collection_name or settings.chroma_collection
        self._persist_dir = persist_dir or settings.chroma_persist_dir
        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)

        self._store = Chroma(
            collection_name=self._collection_name,
            embedding_function=embedder,
            persist_directory=self._persist_dir,
        )

    @property
    def store(self):
        return self._store

    def add_documents(self, documents: Iterable[Document]) -> list[str]:
        """Embed and upsert chunks, deduplicating by chunk_id metadata."""
        docs = list(documents)
        if not docs:
            return []

        ids = [d.metadata.get("chunk_id") or str(i) for i, d in enumerate(docs)]
        existing = set()
        try:
            existing_raw = self._store.get(ids=ids)
            existing = set(existing_raw.get("ids", []))
        except Exception:
            existing = set()

        # Deduplicate within the batch (identical content → same SHA1 chunk_id)
        seen: set[str] = set()
        to_add = []
        for i, d in zip(ids, docs):
            if i not in existing and i not in seen:
                to_add.append((i, d))
                seen.add(i)

        if not to_add:
            logger.info("All %d chunks already indexed.", len(docs))
            return []

        new_ids = [i for i, _ in to_add]
        new_docs = [d for _, d in to_add]

        import re as _re
        for attempt in range(10):
            try:
                self._store.add_documents(documents=new_docs, ids=new_ids)
                break
            except Exception as exc:
                exc_str = str(exc)
                if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
                    # Daily quota exhaustion — retrying won't help today
                    if "PerDay" in exc_str or "per_day" in exc_str.lower():
                        raise RuntimeError(
                            "Gemini daily embedding quota exhausted "
                            "(1 000 RPD free tier). "
                            "Re-run ingest after quota resets (midnight Pacific)."
                        ) from exc
                    m = _re.search(r"retry in (\d+)", exc_str)
                    suggested = int(m.group(1)) if m else 30
                    # Exponential buffer on top of API suggestion: 10, 20, 40, 80, 120…
                    extra = min(10 * (2 ** attempt), 120)
                    wait = suggested + extra
                    logger.warning(
                        "Rate limited (attempt %d/10); waiting %ds "
                        "(API suggested %ds + %ds backoff buffer)",
                        attempt + 1, wait, suggested, extra,
                    )
                    time.sleep(wait)
                else:
                    raise
        else:
            raise RuntimeError(
                "Embedding API rate limited after 10 retries. "
                "Daily quota (1 000 RPD) may be exhausted — "
                "re-run ingest after quota resets (midnight Pacific)."
            )

        logger.info("Indexed %d new chunks (skipped %d existing).", len(new_ids), len(docs) - len(new_ids))
        return new_ids

    def similarity_search(self, query: str, k: int | None = None) -> list[Document]:
        return self._store.similarity_search(query, k=k or settings.top_k_vector)

    def similarity_search_with_score(self, query: str, k: int | None = None) -> list[tuple[Document, float]]:
        return self._store.similarity_search_with_score(query, k=k or settings.top_k_vector)

    def count(self) -> int:
        try:
            return self._store._collection.count()
        except Exception:
            return 0

    def reset(self) -> None:
        """Drop the entire collection (destructive)."""
        try:
            self._store.delete_collection()
        except Exception:
            pass
