"""MOSDAC domain ontology — the controlled vocabulary the knowledge graph speaks.

This module defines:
  * NODE_TYPES      — allowed entity categories (Satellite, Sensor, Parameter, …)
  * RELATION_TYPES  — allowed canonical, directed relationship names
  * VERB_TO_RELATION — maps free-text verbs/phrases onto canonical relations
  * TRIVIAL_RELATIONS — low-information verbs that should be dropped, not stored

Both extractors (spaCy SVO and the LLM) route their raw output through
`canonical_relation()` / `normalize_node_type()` so the graph stays typed and
consistent regardless of which backend produced a triple. Typed, directed edges
are what make precise multi-hop traversal possible
("Satellite-CARRIES->Sensor-MEASURES->Parameter").
"""
from __future__ import annotations

import re

# ── Node categories ─────────────────────────────────────────────────────────
# Stored on each :Entity as the `type` property (and used to gate entity merges).
NODE_TYPES: set[str] = {
    "Mission",
    "Satellite",
    "Sensor",
    "Instrument",
    "Band",
    "Channel",
    "Product",
    "Parameter",
    "Algorithm",
    "Unit",
    "Measurement",
    "Organization",
    "Location",
    "Event",
    "Orbit",
    "DataFormat",
    "Application",
    "Formula",
    "Concept",  # generic fallback
}

# Map common NER labels and freeform type strings onto our node vocabulary.
_TYPE_ALIASES: dict[str, str] = {
    # spaCy NER labels
    "org": "Organization",
    "person": "Organization",
    "gpe": "Location",
    "loc": "Location",
    "fac": "Location",
    "event": "Event",
    "norp": "Organization",
    "work_of_art": "Concept",
    # common freeform synonyms an LLM might emit
    "satellite": "Satellite",
    "spacecraft": "Satellite",
    "mission": "Mission",
    "sensor": "Sensor",
    "instrument": "Instrument",
    "payload": "Instrument",
    "band": "Band",
    "channel": "Channel",
    "product": "Product",
    "dataset": "Product",
    "parameter": "Parameter",
    "variable": "Parameter",
    "geophysical parameter": "Parameter",
    "algorithm": "Algorithm",
    "model": "Algorithm",
    "unit": "Unit",
    "organization": "Organization",
    "agency": "Organization",
    "location": "Location",
    "region": "Location",
    "place": "Location",
    "orbit": "Orbit",
    "format": "DataFormat",
    "file format": "DataFormat",
    "application": "Application",
    "use case": "Application",
    "formula": "Formula",
    "equation": "Formula",
    "concept": "Concept",
}

# ── Canonical relations (directed) ──────────────────────────────────────────
RELATION_TYPES: set[str] = {
    "CARRIES",          # Satellite -> Sensor/Instrument
    "HAS_INSTRUMENT",   # Satellite -> Instrument
    "HAS_BAND",         # Sensor -> Band
    "HAS_CHANNEL",      # Sensor -> Channel
    "PRODUCES",         # Sensor/Algorithm -> Product
    "MEASURES",         # Sensor/Product -> Parameter
    "HAS_SPEC",         # Entity -> Measurement
    "HAS_UNIT",         # Measurement -> Unit
    "LAUNCHED_BY",      # Satellite -> Organization
    "LAUNCHED_ON",      # Satellite -> Event/date
    "OPERATED_BY",      # Satellite/Product -> Organization
    "OPERATES_IN",      # Satellite -> Orbit/Location
    "PART_OF",          # Satellite -> Mission, Sensor -> Satellite
    "DERIVED_FROM",     # Product -> Product/Algorithm
    "USES",             # Algorithm -> Product/Sensor
    "PROVIDES",         # Organization -> Product
    "LOCATED_IN",       # Entity -> Location
    "APPLIES_TO",       # Product/Parameter -> Application
    "RELATED_TO",       # generic fallback (kept, but lowest priority)
    "MENTIONED_IN",     # Entity -> Chunk (provenance)
    "PART_OF_DOCUMENT",  # Chunk -> Document (provenance)
}

# Verbs/phrases (lowercased) -> canonical relation. Longer phrases are matched
# first by canonical_relation() so "launched by" beats "launch".
VERB_TO_RELATION: dict[str, str] = {
    "carries": "CARRIES",
    "carry": "CARRIES",
    "carrying": "CARRIES",
    "onboard": "CARRIES",
    "on board": "CARRIES",
    "aboard": "CARRIES",
    "hosts": "CARRIES",
    "host": "CARRIES",
    "carries sensor": "CARRIES",
    "has instrument": "HAS_INSTRUMENT",
    "equipped with": "HAS_INSTRUMENT",
    "has band": "HAS_BAND",
    "has channel": "HAS_CHANNEL",
    "produces": "PRODUCES",
    "produce": "PRODUCES",
    "generates": "PRODUCES",
    "generate": "PRODUCES",
    "yields": "PRODUCES",
    "measures": "MEASURES",
    "measure": "MEASURES",
    "observes": "MEASURES",
    "observe": "MEASURES",
    "monitors": "MEASURES",
    "monitor": "MEASURES",
    "retrieves": "MEASURES",
    "retrieve": "MEASURES",
    "senses": "MEASURES",
    "estimates": "MEASURES",
    "launched by": "LAUNCHED_BY",
    "launched on": "LAUNCHED_ON",
    "launched": "LAUNCHED_ON",
    "operated by": "OPERATED_BY",
    "operated": "OPERATED_BY",
    "run by": "OPERATED_BY",
    "managed by": "OPERATED_BY",
    "operates in": "OPERATES_IN",
    "orbits": "OPERATES_IN",
    "part of": "PART_OF",
    "belongs to": "PART_OF",
    "belong to": "PART_OF",
    "derived from": "DERIVED_FROM",
    "derive": "DERIVED_FROM",
    "based on": "DERIVED_FROM",
    "uses": "USES",
    "use": "USES",
    "utilizes": "USES",
    "provides": "PROVIDES",
    "provide": "PROVIDES",
    "offers": "PROVIDES",
    "located in": "LOCATED_IN",
    "located": "LOCATED_IN",
    "situated in": "LOCATED_IN",
    "applies to": "APPLIES_TO",
    "used for": "APPLIES_TO",
    "applied to": "APPLIES_TO",
}

# Verbs that carry no relational signal — dropped instead of stored. A graph
# full of (X)-[IS]->(Y) / (X)-[HAS]->(Y) edges drowns real structure in noise.
TRIVIAL_RELATIONS: set[str] = {
    "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "AM",
    "HAS", "HAVE", "HAD", "HAVING",
    "DO", "DOES", "DID", "DONE",
    "S", "GET", "GETS", "GOT",
    "SEEM", "SEEMS", "BECOME", "BECOMES",
}

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]+")
# Phrases sorted longest-first so multi-word mappings win over single words.
_VERB_PHRASES_SORTED = sorted(VERB_TO_RELATION, key=len, reverse=True)


def _sanitize(text: str) -> str:
    cleaned = _SANITIZE_RE.sub("_", text.strip().upper()).strip("_")
    return cleaned


def canonical_relation(verb_or_phrase: str) -> str | None:
    """Map a raw verb/phrase onto a canonical relation.

    Returns None when the verb is trivial (IS/HAS/…) and should be dropped.
    Unknown but non-trivial verbs are kept as a sanitized uppercase relation so
    we never silently lose a real predicate.
    """
    if not verb_or_phrase or not verb_or_phrase.strip():
        return None

    raw = verb_or_phrase.strip().lower()

    # Exact / phrase match against the controlled vocabulary (longest first).
    if raw in VERB_TO_RELATION:
        return VERB_TO_RELATION[raw]
    for phrase in _VERB_PHRASES_SORTED:
        if " " in phrase and phrase in raw:
            return VERB_TO_RELATION[phrase]

    # Already a canonical relation name?
    upper = _sanitize(raw)
    if upper in RELATION_TYPES:
        return upper
    if upper in TRIVIAL_RELATIONS:
        return None

    # Unknown predicate — keep the sanitized verb (still typed, just not curated).
    return upper or None


def normalize_node_type(raw_type: str | None) -> str:
    """Map a raw NER label or freeform type onto a NODE_TYPES member."""
    if not raw_type:
        return "Concept"
    key = raw_type.strip().lower()
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    # Title-case match (e.g. "Satellite" already valid)
    title = raw_type.strip().title().replace(" ", "")
    if title in NODE_TYPES:
        return title
    return "Concept"


def is_trivial_relation(relation: str | None) -> bool:
    """True when a relation should be dropped from the graph."""
    if not relation:
        return True
    return _sanitize(relation) in TRIVIAL_RELATIONS
