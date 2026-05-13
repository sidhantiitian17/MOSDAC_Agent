"""Neo4j driver wrapper: idempotent triple upsert + neighborhood/full-text queries."""
from __future__ import annotations

import logging
from typing import Any

from graph_rag.config import settings
from graph_rag.knowledge_graph.extractor import Triple

logger = logging.getLogger(__name__)


class Neo4jStore:
    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ):
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise ImportError("neo4j driver not installed. Run: pip install neo4j") from exc

        self._driver = GraphDatabase.driver(
            uri or settings.neo4j_uri,
            auth=(username or settings.neo4j_username, password or settings.neo4j_password),
        )
        self._database = database or settings.neo4j_database

    def ensure_schema(self) -> None:
        """Create indexes & full-text index for fast entity lookup."""
        statements = [
            "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
            "CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS FOR (e:Entity) ON EACH [e.name]",
        ]
        with self._driver.session(database=self._database) as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                except Exception as exc:
                    logger.warning("Schema statement failed (%s): %s", stmt, exc)

    def upsert_triple(self, triple: Triple) -> None:
        """MERGE source and target entities, then MERGE a typed relationship."""
        cypher = (
            "MERGE (s:Entity {name: $subject}) "
            "  ON CREATE SET s.type = $subject_type "
            "  ON MATCH  SET s.type = coalesce(s.type, $subject_type) "
            "MERGE (o:Entity {name: $object}) "
            "  ON CREATE SET o.type = $object_type "
            "  ON MATCH  SET o.type = coalesce(o.type, $object_type) "
            "MERGE (s)-[r:RELATION {name: $relation}]->(o) "
            "  ON CREATE SET r.confidence = $confidence, r.source_chunk_id = $source_chunk_id, r.source_path = $source_path "
            "  ON MATCH  SET r.confidence = CASE WHEN $confidence > coalesce(r.confidence, 0.0) THEN $confidence ELSE r.confidence END"
        )
        with self._driver.session(database=self._database) as session:
            session.run(cypher, **triple.as_dict())

    def query_neighbors(self, entity: str, depth: int | None = None) -> list[dict[str, Any]]:
        """Return paths up to `depth` hops away from any entity matching `entity` (case-insensitive)."""
        depth = depth or settings.graph_depth
        cypher = (
            f"MATCH path = (start:Entity)-[*1..{depth}]-(end:Entity) "
            "WHERE toLower(start.name) CONTAINS toLower($entity) "
            "RETURN path LIMIT 50"
        )
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, entity=entity)
            paths = []
            for record in result:
                p = record["path"]
                paths.append(
                    {
                        "nodes": [dict(n) for n in p.nodes],
                        "relationships": [
                            {
                                "type": r.type,
                                "name": r.get("name", r.type),
                                "start": r.start_node.get("name"),
                                "end": r.end_node.get("name"),
                            }
                            for r in p.relationships
                        ],
                    }
                )
            return paths

    @staticmethod
    def _lucene_phrase(text: str) -> str:
        """Wrap text in Lucene phrase quotes, escaping internal backslashes and quotes."""
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def fulltext_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        cypher = (
            "CALL db.index.fulltext.queryNodes('entity_fulltext', $search_text) YIELD node, score "
            "RETURN node.name AS name, node.type AS type, score "
            "ORDER BY score DESC LIMIT $limit"
        )
        try:
            with self._driver.session(database=self._database) as session:
                result = session.run(
                    cypher,
                    parameters={"search_text": self._lucene_phrase(query), "limit": limit},
                )
                return [dict(r) for r in result]
        except Exception as exc:
            logger.warning("Fulltext search failed (%s); falling back to CONTAINS.", exc)
            cypher = (
                "MATCH (e:Entity) WHERE toLower(e.name) CONTAINS toLower($search_text) "
                "RETURN e.name AS name, e.type AS type, 1.0 AS score LIMIT $limit"
            )
            with self._driver.session(database=self._database) as session:
                result = session.run(cypher, parameters={"search_text": query, "limit": limit})
                return [dict(r) for r in result]

    def schema_report(self) -> dict[str, int]:
        with self._driver.session(database=self._database) as session:
            n = session.run("MATCH (e:Entity) RETURN count(e) AS c").single()["c"]
            r = session.run("MATCH ()-[r:RELATION]->() RETURN count(r) AS c").single()["c"]
        return {"entities": int(n), "relationships": int(r)}

    def ping(self) -> bool:
        try:
            with self._driver.session(database=self._database) as session:
                session.run("RETURN 1").consume()
            return True
        except Exception:
            return False

    def clear(self) -> None:
        with self._driver.session(database=self._database) as session:
            session.run("MATCH (n) DETACH DELETE n")

    def close(self) -> None:
        self._driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
