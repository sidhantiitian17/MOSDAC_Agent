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
from guardrails.retrieval.cypher_safe import sanitize_entities
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
        self._planner = None   # lazy QueryPlanner (Phase 6 decomposition)
        self._embedder = None  # lazy embedder for path reranking

    def _query_entities(self, query: str) -> list[str]:
        ents = self._extractor.extract_entities(query)
        names = [e[0] for e in ents]
        if names:
            return names
        # Fallback: regex-extract acronyms (SAR, INSAT) and proper nouns (Oceansat)
        return list(dict.fromkeys(_ENTITY_RE.findall(query)))

    def retrieve(self, query: str) -> list[GraphPath]:
        try:
            entities = self._plan_entities(query)
        except Exception:
            entities = [query]

        paths: list[GraphPath] = []
        seen: set[tuple] = set()
        for ent in entities[:6]:
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
        return self._rerank_paths(query, paths)

    def as_context(self, query: str) -> str:
        paths = self.retrieve(query)
        lines: list[str] = []
        for path in paths:
            for s, r, o in path.triples:
                lines.append(f"({s}) -[{r}]-> ({o})")
        # Preserve order, drop duplicate edges across overlapping paths.
        triples_block = (
            "\n".join(dict.fromkeys(lines))
            if lines
            else "(no relevant knowledge graph paths found)"
        )

        # Pull the passages that actually mention the matched entities so the LLM
        # gets each fact AND its supporting evidence together (grounding).
        blocks = [triples_block]
        community = self._community_block(query)
        if community:
            blocks.append(f"COMMUNITY OVERVIEWS (graph-wide synthesis):\n{community}")
        evidence = self._supporting_passages(query)
        if evidence:
            blocks.append(f"SUPPORTING PASSAGES (linked to graph entities):\n{evidence}")
        return "\n\n".join(blocks)

    def _supporting_passages(self, query: str, limit: int = 3) -> str:
        try:
            entities = self._query_entities(query)
            chunks = self._store.entity_chunks(entities, limit=limit) if entities else []
        except Exception as exc:
            logger.debug("entity_chunks lookup failed: %s", exc)
            return ""
        snippets = []
        for c in chunks:
            text = (c.get("text") or "").strip()
            if not text:
                continue
            snippets.append(f"[{c.get('source', 'graph')}]\n{text[:500]}")
        return "\n\n".join(snippets)

    # ── Phase 6: query decomposition, path reranking, community overviews ──
    def _get_planner(self):
        if self._planner is None:
            from graph_rag.retrieval.query_planner import QueryPlanner

            self._planner = QueryPlanner()
        return self._planner

    def _get_embedder(self):
        if self._embedder is None:
            from graph_rag.embeddings import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    def _plan_entities(self, query: str) -> list[str]:
        """Seed entities for traversal — optionally via LLM query decomposition."""
        if settings.enable_query_decomposition:
            try:
                plan = self._get_planner().decompose(query)
                seeds = list(plan.anchors)
                for sub in plan.sub_questions:
                    seeds.extend(self._query_entities(sub))
                seeds = [s for s in dict.fromkeys(seeds) if s]
                if seeds:
                    return seeds
            except Exception as exc:
                logger.debug("Query planner failed: %s", exc)
        return self._query_entities(query) or [query]

    @staticmethod
    def _serialize_path(path: "GraphPath") -> str:
        return " ; ".join(f"{s} {r} {o}" for s, r, o in path.triples)

    def _rerank_paths(self, query: str, paths: list["GraphPath"]) -> list["GraphPath"]:
        """Rank candidate paths by embedding similarity to the question."""
        top = settings.top_k_paths or self._k
        if not settings.graph_rerank or len(paths) <= 1:
            return paths[:top]
        try:
            embedder = self._get_embedder()
        except Exception as exc:
            logger.debug("Path rerank embedder unavailable: %s", exc)
            return paths[:top]

        from graph_rag.retrieval._rank_utils import rerank_by_embedding

        return rerank_by_embedding(query, paths, self._serialize_path, embedder, top)

    def _community_block(self, query: str, limit: int = 2) -> str:
        if not settings.enable_community_summaries:
            return ""
        try:
            from graph_rag.knowledge_graph.community import community_search

            hits = community_search(query, k=limit)
        except Exception as exc:
            logger.debug("Community block unavailable: %s", exc)
            return ""
        return "\n".join(f"- {h['title']}: {h['summary']}" for h in hits if h.get("summary"))

    def __call__(self, query: str) -> str:
        return self.as_context(query)
