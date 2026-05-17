"""Cypher-based KG retrieval: entities from query -> 2-hop subgraph -> serialized triples."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

# Matches ALL-CAPS acronyms (≥2 chars: SAR, INSAT, AVHRR) and mixed-case proper
# nouns (Oceansat, Resourcesat, INSAT-3D).  Used as fallback when the LLM
# extractor returns nothing.
_ENTITY_RE = re.compile(r"\b[A-Z][A-Z0-9\-]{1,}\b|\b[A-Z][a-z][a-zA-Z]*\b")

from graph_rag.config import settings
from graph_rag.knowledge_graph.extractor import EntityRelationExtractor
from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

logger = logging.getLogger(__name__)


@dataclass
class GraphPath:
    triples: list[tuple[str, str, str]]
    score: float


class GraphRetriever:
    def __init__(
        self,
        store: Neo4jStore | None = None,
        extractor: EntityRelationExtractor | None = None,
        depth: int | None = None,
        k: int | None = None,
    ):
        self._store = store or Neo4jStore()
        self._extractor = extractor or EntityRelationExtractor()
        self._depth = depth or settings.graph_depth
        self._k = k or settings.top_k_graph

    def _query_entities(self, query: str) -> list[str]:
        ents = self._extractor.extract_entities(query)
        names = [e[0] for e in ents]
        if names:
            return names
        # Fallback: regex-extract acronyms (SAR, INSAT) and proper nouns (Oceansat)
        return list(dict.fromkeys(_ENTITY_RE.findall(query)))

    def retrieve(self, query: str) -> list[GraphPath]:
        try:
            entities = self._query_entities(query) or [query]
        except Exception:
            entities = [query]

        paths: list[GraphPath] = []
        seen: set[tuple] = set()
        for ent in entities[:5]:
            try:
                hits = self._store.fulltext_search(ent, limit=self._k)
            except Exception as exc:
                logger.warning("Fulltext search failed for %s: %s", ent, exc)
                hits = []

            target_names = [h["name"] for h in hits] if hits else [ent]
            for name in target_names:
                try:
                    neighborhoods = self._store.query_neighbors(name, depth=self._depth)
                except Exception as exc:
                    logger.warning("Neighbor query failed for %s: %s", name, exc)
                    continue
                for nb in neighborhoods:
                    triples = []
                    for rel in nb["relationships"]:
                        triple = (rel["start"], rel["name"], rel["end"])
                        if triple in seen:
                            continue
                        seen.add(triple)
                        triples.append(triple)
                    if triples:
                        paths.append(GraphPath(triples=triples, score=1.0 / (1 + len(triples))))
        return paths[: self._k]

    def as_context(self, query: str) -> str:
        paths = self.retrieve(query)
        if not paths:
            return "(no relevant knowledge graph paths found)"
        lines: list[str] = []
        for path in paths:
            for s, r, o in path.triples:
                lines.append(f"({s}) -[{r}]-> ({o})")
        return "\n".join(lines)

    def __call__(self, query: str) -> str:
        return self.as_context(query)
