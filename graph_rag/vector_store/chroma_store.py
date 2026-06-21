"""ChromaDB wrapper — persistent, idempotent (skip already-indexed chunk_ids)."""
from __future__ import annotations

import logging
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

        # Pin the HNSW distance to cosine so distance→relevance conversion is
        # well-defined (relevance = 1 - cosine_distance). This metadata is only
        # honoured when the collection is FIRST created; an existing collection
        # keeps whatever space it was built with (re-ingest to migrate).
        self._store = Chroma(
            collection_name=self._collection_name,
            embedding_function=embedder,
            persist_directory=self._persist_dir,
            collection_metadata={"hnsw:space": "cosine"},
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

        # ChromaDB's Rust backend caps a single upsert at ~5461 items; batch defensively.
        _BATCH = 5000
        for start in range(0, len(new_docs), _BATCH):
            self._store.add_documents(
                documents=new_docs[start : start + _BATCH],
                ids=new_ids[start : start + _BATCH],
            )

        logger.info("Indexed %d new chunks (skipped %d existing).", len(new_ids), len(docs) - len(new_ids))
        return new_ids

    def similarity_search(self, query: str, k: int | None = None) -> list[Document]:
        return self._store.similarity_search(query, k=k or settings.top_k_vector)

    def similarity_search_with_score(self, query: str, k: int | None = None) -> list[tuple[Document, float]]:
        return self._store.similarity_search_with_score(query, k=k or settings.top_k_vector)

    def similarity_search_with_relevance(
        self, query: str, k: int | None = None
    ) -> list[tuple[Document, float]]:
        """Return (doc, relevance) with relevance normalized to [0, 1], higher=better.

        LangChain maps the collection's distance to a relevance score using the
        space-appropriate function (cosine → ``1 - distance``). We clamp to [0, 1]
        because the mapping can fall slightly outside the range for un-normalized
        vectors, and the grounding gate expects a clean [0, 1] floor.
        """
        k = k or settings.top_k_vector
        try:
            results = self._store.similarity_search_with_relevance_scores(query, k=k)
        except Exception:
            # Fall back to raw distances and convert (assumes cosine space).
            results = [
                (doc, 1.0 - float(dist))
                for doc, dist in self._store.similarity_search_with_score(query, k=k)
            ]
        return [(doc, max(0.0, min(1.0, float(rel)))) for doc, rel in results]

    def get_all_chunks(self) -> dict:
        """Return the raw {ids, documents, metadatas} for the whole collection.

        Single accessor used by BM25 index building and by parent-section
        expansion so callers don't reach into the private ``_collection``.
        """
        try:
            return self._store._collection.get(include=["documents", "metadatas"])
        except Exception as exc:
            logger.warning("get_all_chunks failed: %s", exc)
            return {"ids": [], "documents": [], "metadatas": []}

    def get_by_metadata(self, where: dict) -> dict:
        """Fetch chunks matching a metadata filter (e.g. {'parent_id': '…'})."""
        try:
            return self._store._collection.get(
                where=where, include=["documents", "metadatas"]
            )
        except Exception as exc:
            logger.debug("get_by_metadata(%s) failed: %s", where, exc)
            return {"ids": [], "documents": [], "metadatas": []}

    def count(self) -> int:
        try:
            return self._store._collection.count()
        except Exception:
            return 0

    def check_embedding_compat(self) -> None:
        """Raise RuntimeError if stored vector dim differs from the current embedder.

        Skipped when the collection is empty (nothing to compare) or when the
        embedder is unavailable at startup (exception → WARNING, no crash).
        """
        if self.count() == 0 or self._embedder is None:
            return
        try:
            stored = self._store._collection.get(limit=1, include=["embeddings"])
            stored_embs = stored.get("embeddings") or []
            if not stored_embs:
                return
            stored_dim = len(stored_embs[0])
            probe_dim = len(self._embedder.embed_query("test"))
            if stored_dim != probe_dim:
                raise RuntimeError(
                    f"Embedding dimension mismatch: ChromaDB collection "
                    f"'{self._collection_name}' stores {stored_dim}-dim vectors "
                    f"but the current embedder produces {probe_dim}-dim vectors. "
                    f"Re-ingest the corpus with the current embedder or switch "
                    f"back to the embedder that was used at ingest time."
                )
            logger.info(
                "Embedding compat OK: collection '%s' dim=%d",
                self._collection_name,
                stored_dim,
            )
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning("Could not verify embedding compatibility: %s", exc)

    def reset(self) -> None:
        """Drop the entire collection (destructive)."""
        try:
            self._store.delete_collection()
        except Exception:
            pass
