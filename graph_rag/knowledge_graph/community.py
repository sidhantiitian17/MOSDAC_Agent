"""GraphRAG-style community summaries for global/overview questions (Phase 6).

For broad questions ("overview of ISRO ocean-observing sensors") no single
triple or passage suffices — you need a synthesis over a whole region of the
graph. This module clusters the graph around hub entities (high-degree
satellites/sensors/missions), asks the LLM to summarize each hub's neighborhood,
stores the summary as a :Community node, and embeds it in a dedicated Chroma
collection so it can be vector-retrieved at query time.

Build offline (slow — one LLM call per community):
    python main.py build-communities
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from graph_rag.config import settings

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """You write a concise, factual summary of a knowledge-graph \
neighborhood about satellites, sensors, and Earth observation. 2-4 sentences. \
Use ONLY the given facts. Name the key entities and what they carry / measure / \
produce. No preamble."""


@dataclass
class Community:
    id: str
    title: str
    summary: str
    members: list[str]


class CommunitySummarizer:
    """Builds and stores per-hub community summaries (Neo4j + Chroma)."""

    def __init__(self, store=None, llm=None, embedder=None):
        self._store = store
        self._llm = llm
        self._embedder = embedder

    def _get_store(self):
        if self._store is None:
            from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

            self._store = Neo4jStore()
        return self._store

    def _get_llm(self):
        if self._llm is None:
            from graph_rag.llm.tabby_client import get_llm

            self._llm = get_llm()
        return self._llm

    def _get_embedder(self):
        if self._embedder is None:
            from graph_rag.embeddings import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    def _hub_entities(self, limit: int, min_degree: int) -> list[dict]:
        # Join on elementId so this works on graphs built before canonical keys
        # existed (legacy nodes have a null `key`).
        cypher = (
            "MATCH (e:Entity)-[r:RELATION]-() "
            "WITH e, count(r) AS deg WHERE deg >= $min_degree "
            "RETURN elementId(e) AS eid, e.key AS key, e.name AS name, e.type AS type, deg "
            "ORDER BY deg DESC LIMIT $limit"
        )
        store = self._get_store()
        with store._driver.session(database=store._database) as sess:
            return [dict(r) for r in sess.run(cypher, min_degree=min_degree, limit=limit)]

    def _neighborhood(self, eid: str, limit: int = 80) -> tuple[list[str], set[str]]:
        cypher = (
            "MATCH (e:Entity)-[r:RELATION]-(n:Entity) WHERE elementId(e) = $eid "
            "RETURN startNode(r).name AS s, r.name AS rel, endNode(r).name AS o, n.name AS nb "
            "LIMIT $limit"
        )
        store = self._get_store()
        with store._driver.session(database=store._database) as sess:
            rows = [dict(r) for r in sess.run(cypher, eid=eid, limit=limit)]
        lines: list[str] = []
        members: set[str] = set()
        for row in rows:
            lines.append(f"({row['s']}) -[{row['rel']}]-> ({row['o']})")
            if row["nb"]:
                members.add(row["nb"])
        return lines, members

    def _summarize(self, name: str, facts_block: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = self._get_llm().invoke(
            [
                SystemMessage(content=_SUMMARY_SYSTEM),
                HumanMessage(content=f"Entity: {name}\nFacts:\n{facts_block}\n\nSummary:"),
            ]
        )
        return getattr(resp, "content", str(resp)).strip()

    def _store_community(self, cid, title, summary, members, eid) -> None:
        cypher = (
            "MERGE (c:Community {id:$id}) "
            "SET c.title=$title, c.summary=$summary, c.members=$members "
            "WITH c MATCH (e:Entity) WHERE elementId(e) = $eid MERGE (c)-[:SUMMARIZES]->(e)"
        )
        store = self._get_store()
        with store._driver.session(database=store._database) as sess:
            sess.run(
                cypher, id=cid, title=title, summary=summary, members=members, eid=eid
            ).consume()

    def build(self, limit: int | None = None, min_degree: int | None = None) -> int:
        limit = limit or settings.max_communities
        min_degree = min_degree or settings.community_min_degree
        store = self._get_store()
        store.ensure_schema()
        hubs = self._hub_entities(limit, min_degree)
        if not hubs:
            logger.warning("No hub entities found (min_degree=%s).", min_degree)
            return 0

        from langchain_core.documents import Document

        from graph_rag.vector_store.chroma_store import ChromaStore

        chroma = ChromaStore(
            embedder=self._get_embedder(), collection_name=settings.community_collection
        )

        from graph_rag.knowledge_graph.resolver import canonical_key

        docs: list[Document] = []
        built = 0
        for hub in hubs:
            lines, members = self._neighborhood(hub["eid"])
            if not lines:
                continue
            block = "\n".join(lines[:80])
            try:
                summary = self._summarize(hub["name"], block)
            except Exception as exc:
                logger.warning("Summarize failed for %s: %s", hub["name"], exc)
                continue
            if not summary:
                continue
            cid = f"community::{hub.get('key') or canonical_key(hub['name'])}"
            members_list = sorted(m for m in members if m)[:50]
            self._store_community(cid, hub["name"], summary, members_list, hub["eid"])
            docs.append(
                Document(
                    page_content=summary,
                    metadata={"community_id": cid, "title": hub["name"], "chunk_id": cid},
                )
            )
            built += 1

        if docs:
            chroma.add_documents(docs)
        logger.info("Built %d community summaries.", built)
        return built


def community_search(query: str, k: int = 3) -> list[dict]:
    """Vector-search stored community summaries for global/overview questions."""
    try:
        from graph_rag.embeddings import get_embedder
        from graph_rag.vector_store.chroma_store import ChromaStore

        chroma = ChromaStore(
            embedder=get_embedder(), collection_name=settings.community_collection
        )
        hits = chroma.similarity_search_with_score(query, k=k)
    except Exception as exc:
        logger.debug("community_search unavailable: %s", exc)
        return []
    return [
        {"title": d.metadata.get("title", ""), "summary": d.page_content, "score": float(s)}
        for d, s in hits
    ]
