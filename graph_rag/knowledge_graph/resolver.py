"""Entity canonicalization — collapse surface variants onto one canonical node.

The single biggest blocker to multi-hop reasoning is graph fragmentation: when
"INSAT-3D", "INSAT 3D", and "the INSAT-3D satellite" become three separate
nodes, a reasoning chain that should pass through one shared entity dead-ends.

This module turns any surface form into:
  * a stable `key`  — used to MERGE nodes in Neo4j (so variants collapse), and
  * a clean display `name` — what the user sees and what fulltext indexes.

A small curated seed lexicon anchors well-known MOSDAC entities to a canonical
spelling. Everything else is normalized deterministically (case, determiners,
separators, trailing type words). Optional embedding-based dedupe is available
for a heavier offline pass but is off by default.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Leading words that add no identity ("the INSAT-3D satellite" -> "INSAT-3D").
_DETERMINERS = {"the", "a", "an", "this", "that", "these", "those"}
# Trailing nouns that restate the type and fragment names if kept.
_TRAILING_TYPE_WORDS = {
    "satellite", "satellites", "sensor", "sensors", "mission", "missions",
    "payload", "payloads", "instrument", "instruments", "spacecraft", "series",
}

# Curated aliases (normalized key form) -> canonical display name. High-precision
# anchors for the entities MOSDAC users ask about most. Extend freely.
SEED_LEXICON: dict[str, str] = {
    "insat3d": "INSAT-3D",
    "insat3dr": "INSAT-3DR",
    "insat3a": "INSAT-3A",
    "oceansat2": "Oceansat-2",
    "oceansat3": "Oceansat-3",
    "scatsat1": "SCATSAT-1",
    "meghatropiques": "Megha-Tropiques",
    "kalpana1": "Kalpana-1",
    "cartosat": "Cartosat",
    "resourcesat2": "Resourcesat-2",
    "saral": "SARAL",
    "altika": "AltiKa",
    "ocm": "OCM",
    "ocm2": "OCM-2",
    "ocm3": "OCM-3",
    "scatterometer": "Scatterometer",
    "msmr": "MSMR",
    "sar": "SAR",
    "isro": "ISRO",
    "sac": "Space Applications Centre",
    "mosdac": "MOSDAC",
    "sst": "Sea Surface Temperature",
    "ndvi": "NDVI",
    "chlorophyll": "Chlorophyll",
}

_PUNCT_RE = re.compile(r"[^\w\s-]")
_WS_RE = re.compile(r"\s+")


def _strip_affixes(tokens: list[str]) -> list[str]:
    """Remove a leading determiner and a single trailing type word."""
    if tokens and tokens[0] in _DETERMINERS:
        tokens = tokens[1:]
    if len(tokens) > 1 and tokens[-1] in _TRAILING_TYPE_WORDS:
        tokens = tokens[:-1]
    return tokens


def canonical_key(name: str) -> str:
    """Deterministic merge key — case/space/hyphen-insensitive identity.

    "INSAT-3D", "INSAT 3D", "insat3d", "the INSAT-3D satellite" all map to
    "insat3d", so MERGE on this key collapses every variant onto one node.
    """
    if not name:
        return ""
    lowered = _PUNCT_RE.sub(" ", name.lower())
    lowered = _WS_RE.sub(" ", lowered).strip()
    tokens = _strip_affixes(lowered.split())
    joined = "".join(tokens)            # drop spaces AND hyphens for the key
    return joined.replace("-", "")


def canonical_name(name: str) -> str:
    """Clean display name. Uses the seed lexicon when the entity is known."""
    if not name:
        return ""
    key = canonical_key(name)
    if key in SEED_LEXICON:
        return SEED_LEXICON[key]
    lowered = _WS_RE.sub(" ", name.strip())
    tokens = _strip_affixes(lowered.split())
    cleaned = " ".join(tokens).strip(" -")
    return cleaned or name.strip()


@dataclass(frozen=True)
class ResolvedEntity:
    name: str   # canonical display name
    key: str    # canonical merge key
    surface: str  # original surface form (kept as an alias)


def resolve(name: str) -> ResolvedEntity:
    """Resolve a raw surface form into a canonical (name, key) pair."""
    surface = (name or "").strip()
    return ResolvedEntity(name=canonical_name(surface), key=canonical_key(surface), surface=surface)


class EntityResolver:
    """Optional embedding-based near-duplicate merger for an offline cleanup pass.

    Deterministic `resolve()` handles the common cases for free. This class adds
    similarity-based merging (e.g. "Ocean Colour Monitor" ~ "Ocean Color Monitor")
    gated by a high threshold and equal node type, so it only fires when two names
    really are the same thing. It is not used in the hot ingestion path by default.
    """

    def __init__(self, threshold: float = 0.92, embedder=None):
        self._threshold = threshold
        self._embedder = embedder

    def _get_embedder(self):
        if self._embedder is None:
            from graph_rag.embeddings import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        return dot / (na * nb + 1e-12)

    def cluster(self, names: list[str], types: dict[str, str] | None = None) -> dict[str, str]:
        """Return surface_name -> canonical_name for near-duplicate names.

        Names map to themselves unless a higher-confidence canonical exists.
        `types` (name -> node type) gates merges to same-typed entities.
        """
        types = types or {}
        if not names:
            return {}
        uniq = list(dict.fromkeys(names))
        vectors = self._get_embedder().embed_documents([canonical_name(n) for n in uniq])
        mapping: dict[str, str] = {}
        canon_vectors: list[tuple[str, list[float], str]] = []  # (name, vec, type)
        for name, vec in zip(uniq, vectors):
            ntype = types.get(name, "")
            match = None
            for cname, cvec, ctype in canon_vectors:
                if ntype and ctype and ntype != ctype:
                    continue
                if self._cosine(vec, cvec) >= self._threshold:
                    match = cname
                    break
            if match:
                mapping[name] = match
            else:
                mapping[name] = canonical_name(name)
                canon_vectors.append((canonical_name(name), vec, ntype))
        return mapping
