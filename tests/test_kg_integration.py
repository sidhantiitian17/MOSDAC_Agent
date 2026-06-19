"""Live Neo4j integration test for the upgraded knowledge-graph store.

Verifies the full push path: canonical entity merge, typed relationship,
quantitative Measurement node, and chunk/document provenance — then cleans up
its synthetic nodes. Skipped automatically when Neo4j is unreachable.
"""
from __future__ import annotations

import pytest

from tests.conftest import skip_if_no_neo4j

_SRC = "test://kg-integration"
_CHUNK = "zzz-chunk-int-1"
_KEYS = ["zzztestsata", "zzztestsensq"]  # canonical keys of the synthetic names


@pytest.fixture
def store(neo4j_available):
    skip_if_no_neo4j(neo4j_available)
    from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

    s = Neo4jStore()
    s.ensure_schema()
    yield s
    # Tear down only the synthetic nodes this test created.
    with s._driver.session(database=s._database) as sess:
        sess.run("MATCH (m:Measurement) WHERE m.source_chunk_id = $c DETACH DELETE m", c=_CHUNK)
        sess.run("MATCH (c:Chunk {chunk_id: $c}) DETACH DELETE c", c=_CHUNK)
        sess.run("MATCH (d:Document {source: $src}) DETACH DELETE d", src=_SRC)
        sess.run("MATCH (e:Entity) WHERE e.key IN $keys DETACH DELETE e", keys=_KEYS)
    s.close()


def test_push_triple_measurement_and_provenance(store):
    from graph_rag.knowledge_graph.extractor import Triple

    # 1) Typed relationship + provenance.
    store.upsert_triples([
        Triple(
            subject="the ZZZTestSat-A satellite",  # determiner/affix must be stripped
            subject_type="Satellite",
            relation="CARRIES",
            object_="ZZZTestSensQ",
            object_type="Sensor",
            source_chunk_id=_CHUNK,
            source_path=_SRC,
            confidence=0.9,
        )
    ])
    # 2) Chunk text so facts can cite evidence.
    store.upsert_chunks([{
        "chunk_id": _CHUNK,
        "text": "ZZZTestSat-A carries ZZZTestSensQ with 1 km spatial resolution.",
        "source": _SRC,
    }])
    # 3) Comparable measurement.
    store.upsert_measurements([{
        "entity": "ZZZTestSensQ",
        "entity_type": "Sensor",
        "property": "spatial_resolution",
        "value": 1.0, "unit": "km", "raw": "1 km",
        "base_value": 1000.0, "base_unit": "m",
        "chunk_id": _CHUNK, "source": _SRC,
    }])

    # Entity merged on canonical key → "the ZZZTestSat-A satellite" == "zzztestsata".
    paths = store.query_neighbors("ZZZTestSat-A", depth=1)
    rel_names = {r["name"] for p in paths for r in p["relationships"]}
    assert "CARRIES" in rel_names

    # Provenance: the sensor's supporting passage is retrievable.
    chunks = store.entity_chunks(["ZZZTestSensQ"], limit=3)
    assert any("resolution" in (c.get("text") or "") for c in chunks)

    # Measurement landed.
    report = store.schema_report()
    assert report["measurements"] >= 1
    assert report["chunks"] >= 1
