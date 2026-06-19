"""Content-hash manifest for incremental ingestion.

Tracks which files have already been ingested by their SHA-256 *content* hash so
repeat runs can skip them entirely — no re-loading, re-embedding, or (the expensive
part) re-running per-chunk LLM extraction. Hashing content rather than paths means a
moved/renamed-but-unchanged file is still recognised, and an edited file (new hash)
is treated as new and re-ingested.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# 64 KB read buffer — stream large PDFs without loading the whole file into memory.
_HASH_CHUNK_SIZE = 65536
_MANIFEST_VERSION = 1


def compute_file_hash(path: Path) -> str:
    """SHA-256 of a file's bytes, read in 64 KB chunks."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_HASH_CHUNK_SIZE), b""):
            sha256.update(block)
    return sha256.hexdigest()


@dataclass
class IngestionManifest:
    """A JSON-backed record of `file_hash -> {source, file_name, chunk_count, ingested_at}`.

    Construct via `IngestionManifest.load(path)`. The manifest is held in memory and only
    written to disk when `save()` is called (by the pipeline at the end of a clean full run).
    """

    path: Path
    entries: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "IngestionManifest":
        """Read the manifest from disk. A missing or corrupt file yields an empty manifest."""
        p = Path(path)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                entries = data.get("files", {})
                if isinstance(entries, dict):
                    return cls(path=p, entries=entries)
                logger.warning("Manifest %s has unexpected shape; starting fresh.", p)
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                logger.warning("Manifest %s unreadable (%s); starting fresh.", p, exc)
        return cls(path=p, entries={})

    def is_ingested(self, file_hash: str) -> bool:
        """True if a file with this content hash has already been fully ingested."""
        return file_hash in self.entries

    def record(self, file_hash: str, *, source: str, file_name: str, chunk_count: int) -> None:
        """Mark a file (by content hash) as ingested, with provenance metadata."""
        self.entries[file_hash] = {
            "source": source,
            "file_name": file_name,
            "chunk_count": chunk_count,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

    def save(self) -> None:
        """Persist the manifest to disk as pretty JSON (creates parent dirs as needed)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _MANIFEST_VERSION, "files": self.entries}
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
