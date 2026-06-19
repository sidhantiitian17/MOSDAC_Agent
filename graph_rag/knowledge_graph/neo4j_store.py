"""Neo4j driver wrapper: idempotent, canonicalized, provenance-linked KG storage.

Key design points (all aimed at better multi-hop reasoning):
  * Entities MERGE on a canonical `key` (from resolver) instead of raw `name`, so
    "INSAT-3D" / "INSAT 3D" / "the INSAT-3D satellite" collapse to ONE node —
    the prerequisite for chains that pass through shared entities.
  * Semantic edges are `:RELATION {name}` where `name` is a canonical relation
    (CARRIES, MEASURES, …). Multi-hop traversal is restricted to `:RELATION` so
    provenance edges never pollute reasoning paths.
  * Provenance: every entity is `:MENTIONED_IN` a `:Chunk`, and each `:Chunk` is
    `:PART_OF_DOCUMENT` a `:Document`. This lets graph retrieval return the exact
    supporting passage for each fact (grounding).
  * Quantitative facts live as `:Measurement` nodes hung off entities via
    `:HAS_SPEC` (with `:HAS_UNIT` -> `:Unit`), making specs comparable and
    answerable for technical/math queries.
"""
from __future__ import annotations

import logging
from typing import Any

from graph_rag.config import settings
from graph_rag.knowledge_graph.extractor import Triple
from graph_rag.knowledge_graph.resolver import canonical_key, canonical_name

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

    # ── schema ────────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        """Create indexes/constraints for fast lookup and canonical merges."""
        statements = [
            "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE",
            "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
            "CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
            "CREATE FULLTEXT INDEX entity_fulltext IF NOT EXISTS FOR (e:Entity) ON EACH [e.name]",
            "CREATE INDEX chunk_id IF NOT EXISTS FOR (c:Chunk) ON (c.chunk_id)",
            "CREATE INDEX document_source IF NOT EXISTS FOR (d:Document) ON (d.source)",
            "CREATE INDEX measurement_key IF NOT EXISTS FOR (m:Measurement) ON (m.key)",
            "CREATE INDEX measurement_prop IF NOT EXISTS FOR (m:Measurement) ON (m.property)",
        ]
        with self._driver.session(database=self._database) as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                except Exception as exc:
                    logger.warning("Schema statement failed (%s): %s", stmt, exc)

    # ── triple upsert (canonical + provenance) ────────────────────────────
    @staticmethod
    def _triple_row(triple: Triple) -> dict:
        """Augment a triple dict with canonical keys/names for key-based MERGE."""
        d = triple.as_dict()
        d["subject_name"] = canonical_name(triple.subject) or triple.subject
        d["object_name"] = canonical_name(triple.object_) or triple.object_
        d["subject_key"] = canonical_key(triple.subject)
        d["object_key"] = canonical_key(triple.object_)
        d["source_chunk_id"] = d.get("source_chunk_id") or ""
        d["source_path"] = d.get("source_path") or ""
        return d

    _UPSERT_CYPHER = (
        "UNWIND $rows AS t "
        "MERGE (s:Entity {key: t.subject_key}) "
        "  ON CREATE SET s.name = t.subject_name, s.type = t.subject_type, s.aliases = [t.subject] "
        "  ON MATCH  SET s.type = coalesce(s.type, t.subject_type), "
        "                s.aliases = CASE WHEN t.subject IN coalesce(s.aliases, []) "
        "                            THEN s.aliases ELSE coalesce(s.aliases, []) + t.subject END "
        "MERGE (o:Entity {key: t.object_key}) "
        "  ON CREATE SET o.name = t.object_name, o.type = t.object_type, o.aliases = [t.object] "
        "  ON MATCH  SET o.type = coalesce(o.type, t.object_type), "
        "                o.aliases = CASE WHEN t.object IN coalesce(o.aliases, []) "
        "                            THEN o.aliases ELSE coalesce(o.aliases, []) + t.object END "
        "MERGE (s)-[r:RELATION {name: t.relation}]->(o) "
        "  ON CREATE SET r.confidence = t.confidence, r.source_chunk_id = t.source_chunk_id, "
        "                r.source_path = t.source_path "
        "  ON MATCH  SET r.confidence = CASE WHEN t.confidence > coalesce(r.confidence, 0.0) "
        "                                THEN t.confidence ELSE r.confidence END "
        "FOREACH (_ IN CASE WHEN t.source_chunk_id <> '' THEN [1] ELSE [] END | "
        "  MERGE (c:Chunk {chunk_id: t.source_chunk_id}) "
        "    ON CREATE SET c.source = t.source_path "
        "  MERGE (s)-[:MENTIONED_IN]->(c) "
        "  MERGE (o)-[:MENTIONED_IN]->(c) "
        "  FOREACH (__ IN CASE WHEN t.source_path <> '' THEN [1] ELSE [] END | "
        "    MERGE (d:Document {source: t.source_path}) "
        "    MERGE (c)-[:PART_OF_DOCUMENT]->(d) "
        "  ) "
        ")"
    )

    def upsert_triple(self, triple: Triple) -> None:
        """Upsert a single triple (key-merged entities + relationship + provenance)."""
        with self._driver.session(database=self._database) as session:
            session.run(self._UPSERT_CYPHER, rows=[self._triple_row(triple)]).consume()

    def upsert_triples(self, triples: list[Triple], batch_size: int = 200) -> None:
        """Batch-upsert triples using UNWIND — one round-trip per batch."""
        if not triples:
            return
        rows = [self._triple_row(t) for t in triples]
        for start in range(0, len(rows), batch_size):
            with self._driver.session(database=self._database) as session:
                session.run(self._UPSERT_CYPHER, rows=rows[start : start + batch_size]).consume()

    # ── provenance: store chunk text so facts can cite their evidence ──────
    def upsert_chunks(self, chunks: list[dict[str, str]], batch_size: int = 200) -> None:
        """Store/refresh Chunk nodes with their passage text and Document link.

        Each dict: {chunk_id, text, source}.
        """
        rows = [c for c in chunks if c.get("chunk_id")]
        if not rows:
            return
        cypher = (
            "UNWIND $rows AS r "
            "MERGE (c:Chunk {chunk_id: r.chunk_id}) "
            "  SET c.text = r.text, c.source = r.source "
            "FOREACH (_ IN CASE WHEN r.source <> '' THEN [1] ELSE [] END | "
            "  MERGE (d:Document {source: r.source}) "
            "  MERGE (c)-[:PART_OF_DOCUMENT]->(d) "
            ")"
        )
        for start in range(0, len(rows), batch_size):
            with self._driver.session(database=self._database) as session:
                session.run(cypher, rows=rows[start : start + batch_size]).consume()

    # ── quantitative facts: comparable measurements ───────────────────────
    def upsert_measurements(self, measurements: list[dict[str, Any]], batch_size: int = 200) -> None:
        """Store Measurement nodes hung off an anchor entity via HAS_SPEC.

        Each dict: {entity, entity_key, entity_type, property, value, unit, raw,
        base_value, base_unit, chunk_id, source}.
        """
        rows = []
        for m in measurements:
            ek = m.get("entity_key") or canonical_key(m.get("entity", ""))
            if not ek:
                continue
            raw = m.get("raw", "")
            rows.append(
                {
                    "entity_key": ek,
                    "entity_name": m.get("entity") or m.get("entity_name") or ek,
                    "entity_type": m.get("entity_type", "Concept"),
                    "property": m.get("property", ""),
                    "value": float(m.get("value", 0.0)),
                    "unit": m.get("unit", "") or "",
                    "raw": raw,
                    "base_value": float(m.get("base_value", m.get("value", 0.0)) or 0.0),
                    "base_unit": m.get("base_unit", "") or "",
                    "chunk_id": m.get("chunk_id", "") or "",
                    "meas_key": f"{ek}|{m.get('property','')}|{raw}".lower(),
                }
            )
        if not rows:
            return
        cypher = (
            "UNWIND $rows AS m "
            "MERGE (e:Entity {key: m.entity_key}) "
            "  ON CREATE SET e.name = m.entity_name, e.type = m.entity_type "
            "MERGE (meas:Measurement {key: m.meas_key}) "
            "  ON CREATE SET meas.property = m.property, meas.value = m.value, meas.unit = m.unit, "
            "                meas.raw = m.raw, meas.base_value = m.base_value, meas.base_unit = m.base_unit, "
            "                meas.source_chunk_id = m.chunk_id "
            "MERGE (e)-[:HAS_SPEC]->(meas) "
            "FOREACH (_ IN CASE WHEN m.unit <> '' THEN [1] ELSE [] END | "
            "  MERGE (u:Unit {name: m.unit}) "
            "  MERGE (meas)-[:HAS_UNIT]->(u) "
            ") "
            "FOREACH (_ IN CASE WHEN m.chunk_id <> '' THEN [1] ELSE [] END | "
            "  MERGE (c:Chunk {chunk_id: m.chunk_id}) "
            "  MERGE (meas)-[:MENTIONED_IN]->(c) "
            ")"
        )
        for start in range(0, len(rows), batch_size):
            with self._driver.session(database=self._database) as session:
                session.run(cypher, rows=rows[start : start + batch_size]).consume()

    def link_entities_to_source_chunks(self, entity_keys: list[str], source: str) -> None:
        """Create MENTIONED_IN edges from entities to all Chunk nodes of a source document.

        Document-level KG extraction calls extractor.extract() with source_chunk_id=""
        so the triple UPSERT_CYPHER never fires its MENTIONED_IN block. Without this
        method, entities extracted from Drupal articles have no provenance links and
        entity_chunks() retrieval returns empty — the chatbot has no grounding text.

        Called once per source document, after upsert_chunks() stores the Chunk nodes.
        """
        if not entity_keys or not source:
            return
        cypher = (
            "UNWIND $entity_keys AS ek "
            "MATCH (e:Entity {key: ek}) "
            "MATCH (c:Chunk) WHERE c.source = $source "
            "MERGE (e)-[:MENTIONED_IN]->(c)"
        )
        with self._driver.session(database=self._database) as session:
            session.run(cypher, entity_keys=list(entity_keys), source=source).consume()

    # ── retrieval helpers ─────────────────────────────────────────────────
    def query_neighbors(
        self, entity: str, depth: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return semantic paths up to `depth` hops from any entity matching `entity`.

        Traversal is restricted to `:RELATION` edges (provenance edges excluded)
        and shorter paths are returned first so the most direct facts win.
        """
        depth = depth or settings.graph_depth
        cypher = (
            f"MATCH path = (start:Entity)-[:RELATION*1..{depth}]-(end:Entity) "
            "WHERE toLower(start.name) CONTAINS toLower($entity) OR start.key = $key "
            "RETURN path ORDER BY length(path) ASC LIMIT $limit"
        )
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, entity=entity, key=canonical_key(entity), limit=limit)
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
                                "confidence": r.get("confidence", 1.0),
                            }
                            for r in p.relationships
                        ],
                    }
                )
            return paths

    def entity_chunks(self, names: list[str], limit: int = 5) -> list[dict[str, Any]]:
        """Return supporting passage text for entities (grounding for the LLM)."""
        if not names:
            return []
        keys = [canonical_key(n) for n in names]
        lowered = [n.lower() for n in names]
        cypher = (
            "MATCH (e:Entity)-[:MENTIONED_IN]->(c:Chunk) "
            "WHERE e.key IN $keys OR toLower(e.name) IN $names "
            "WITH DISTINCT c WHERE c.text IS NOT NULL "
            "RETURN c.chunk_id AS chunk_id, c.text AS text, c.source AS source LIMIT $limit"
        )
        with self._driver.session(database=self._database) as session:
            result = session.run(cypher, keys=keys, names=lowered, limit=limit)
            return [dict(r) for r in result]

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
            m = session.run("MATCH (m:Measurement) RETURN count(m) AS c").single()["c"]
            ch = session.run("MATCH (c:Chunk) RETURN count(c) AS c").single()["c"]
        return {
            "entities": int(n),
            "relationships": int(r),
            "measurements": int(m),
            "chunks": int(ch),
        }

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
