"""Sanitize entity names before they enter Neo4j fulltext / Cypher queries (L2)."""
from __future__ import annotations

import re

# Allow alphanumerics, spaces, dot, hyphen, underscore, forward-slash
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9 .\-_/]")
# Lucene specials excluding hyphen (already safe in Neo4j context)
_LUCENE_SPECIAL_RE = re.compile(r"([\+!\(\)\{\}\^\~\*\?\\])")

MAX_ENTITY_LENGTH = 100


def sanitize_entity(name: str) -> str:
    """Strip unsafe characters and escape Lucene specials."""
    name = name[:MAX_ENTITY_LENGTH]
    # Strip SQL/Cypher keywords that got through via semicolons etc.
    name = _UNSAFE_RE.sub("", name)
    # Remove any standalone dangerous keywords that slip through
    for kw in (";", "--", "DROP", "DELETE", "MERGE", "CREATE", "RETURN", "MATCH"):
        name = name.replace(kw, "")
    name = _LUCENE_SPECIAL_RE.sub(lambda m: "\\" + m.group(1), name)
    return " ".join(name.split()).strip()


def sanitize_entities(names: list) -> list:
    """Sanitize a list of entity names, dropping empties after sanitization."""
    return [s for raw in names if (s := sanitize_entity(raw))]
