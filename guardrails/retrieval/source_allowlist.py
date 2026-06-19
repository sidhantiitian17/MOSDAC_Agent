"""Source allowlist: only chunks from ingested (hash-manifest) files may be used (L2).

The ingest_manifest.json produced by graph_rag/ingestion/manifest.py contains a
mapping of ingested file paths to their SHA-256 hash + metadata.  This module
reads that manifest and exposes is_allowed(source) so retrieval guards can
filter out chunks whose provenance is not recorded — blocking stale, unknown, or
potentially poisoned documents.

Fails OPEN if the manifest cannot be loaded (logs a warning and allows all sources).
Cache is invalidated by calling invalidate_cache() after re-ingestion.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_cached_sources: set[str] | None = None


def _load_manifest(manifest_path: str) -> set[str]:
    global _cached_sources
    if _cached_sources is not None:
        return _cached_sources

    try:
        data: dict = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        sources: set[str] = set()
        for file_path in data.keys():
            sources.add(file_path)
            sources.add(Path(file_path).name)
            # Also add stem (no extension) to match truncated source metadata
            sources.add(Path(file_path).stem)
        _cached_sources = sources
        logger.info("Source allowlist loaded: %d entries from %s", len(sources), manifest_path)
        return _cached_sources
    except Exception as exc:
        logger.warning(
            "Could not load source allowlist from %s (%s) — failing open", manifest_path, exc
        )
        return set()


def is_allowed(source: str, manifest_path: str) -> bool:
    """Return True if the source is in the manifest (or if manifest is unavailable)."""
    allowed = _load_manifest(manifest_path)
    if not allowed:
        return True  # fail-open when manifest unavailable

    src_path = Path(source)
    return (
        source in allowed
        or src_path.name in allowed
        or src_path.stem in allowed
        or any(source.endswith(a) for a in allowed)
    )


def invalidate_cache() -> None:
    """Force reload of the manifest on the next request (call after re-ingestion)."""
    global _cached_sources
    _cached_sources = None
