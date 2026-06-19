"""On-topic scope gate (L1).

Computes a MOSDAC domain centroid from seed phrases using the existing embedder,
caches it to disk, and at request time checks cosine similarity of the query.

Fails OPEN — if the embedder is unavailable, allows the request through so the
pipeline does not break when the embedding server is temporarily down.  The
prompt-level scope restriction still applies as a secondary control.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

_MOSDAC_SEED_PHRASES = [
    "MOSDAC meteorological oceanographic satellite data archival centre",
    "INSAT satellite weather forecast India storm cyclone",
    "Oceansat ocean colour chlorophyll monitoring phytoplankton",
    "SCATSAT wind speed ocean surface scatterometer",
    "satellite image data product download HDF5 NetCDF",
    "cyclone tracking storm prediction alert warning",
    "sea surface temperature ocean salinity depth",
    "rainfall nowcast precipitation forecast monsoon",
    "ISRO satellite mission sensor instrument payload",
    "remote sensing earth observation data product",
    "cloud cover humidity atmospheric temperature profile",
    "significant wave height period ocean state forecast",
    "drought flood monitoring vegetation index NDVI",
    "MOSDAC portal login dataset access API REST",
    "Resourcesat RISAT ScatSat INSAT-3D INSAT-3DR",
    "satellite data processing level L1 L2 L3 product",
    "polar orbiting geostationary orbit sun synchronous",
    "microwave infrared optical sensor band channel",
    "aerosol optical depth land surface temperature",
    "ocean wind wave current tidal forecast model",
]

_centroid_cache: "list[float] | None" = None


def _compute_centroid() -> list[float]:
    import numpy as np
    from graph_rag.embeddings import get_embedder

    embedder = get_embedder()
    vecs = embedder.embed_documents(_MOSDAC_SEED_PHRASES)
    return np.mean(vecs, axis=0).tolist()


def _load_or_compute_centroid(centroid_path: str) -> list[float]:
    global _centroid_cache
    if _centroid_cache is not None:
        return _centroid_cache

    path = Path(centroid_path)
    if path.exists():
        try:
            import numpy as np
            _centroid_cache = np.load(str(path)).tolist()
            logger.info("Scope centroid loaded from %s", centroid_path)
            return _centroid_cache
        except Exception as exc:
            logger.warning("Failed to load cached centroid: %s", exc)

    logger.info("Computing MOSDAC scope centroid from %d seed phrases…", len(_MOSDAC_SEED_PHRASES))
    centroid = _compute_centroid()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        import numpy as np
        np.save(str(path), np.array(centroid))
        logger.info("Scope centroid saved to %s", centroid_path)
    except Exception as exc:
        logger.warning("Could not cache centroid to disk: %s", exc)

    _centroid_cache = centroid
    return centroid


def check(text: str, min_sim: float, centroid_path: str) -> Tuple[bool, float]:
    """
    Returns (in_scope, cosine_similarity).
    Fails open — returns (True, 0.0) if embedder unavailable.
    """
    try:
        import numpy as np
        from graph_rag.embeddings import get_embedder

        centroid = _load_or_compute_centroid(centroid_path)
        embedder = get_embedder()
        q_vec = embedder.embed_query(text)

        q = np.array(q_vec)
        c = np.array(centroid)
        denom = np.linalg.norm(q) * np.linalg.norm(c) + 1e-9
        sim = float(np.dot(q, c) / denom)

        return sim >= min_sim, sim
    except Exception as exc:
        logger.warning("Scope gate unavailable (fail-open): %s", exc)
        return True, 0.0


def invalidate_centroid_cache() -> None:
    """Force recomputation on next request (call after re-ingestion)."""
    global _centroid_cache
    _centroid_cache = None
