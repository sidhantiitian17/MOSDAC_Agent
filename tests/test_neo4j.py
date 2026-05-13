"""Tests for knowledge_graph/neo4j_store.py — requires a live Neo4j instance."""
from __future__ import annotations

import pytest

from tests.conftest import skip_if_no_neo4j


@pytest.fixture
def neo_store(neo4j_available):
    skip_if_no_neo4j(neo4j_available)
    from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

    store = Neo4jStore()
    store.ensure_schema()
    yield store
    store.close()


def test_ping(neo_store):
    assert neo_store.ping() is True


def test_upsert_and_query(neo_store):
    from graph_rag.knowledge_graph.extractor import Triple

    triple = Triple(
        subject="TestEntityA",
        subject_type="ORG",
        relation="TEST_RELATES_TO",
        object_="TestEntityB",
        object_type="ORG",
        source_chunk_id="t-001",
        source_path="test://memory",
        confidence=0.9,
    )
    neo_store.upsert_triple(triple)
    paths = neo_store.query_neighbors("TestEntityA", depth=1)
    assert paths, "should find at least one path from TestEntityA"
    rel_names = {r["name"] for p in paths for r in p["relationships"]}
    assert "TEST_RELATES_TO" in rel_names


def test_schema_report_returns_counts(neo_store):
    report = neo_store.schema_report()
    assert "entities" in report and "relationships" in report
    assert report["entities"] >= 0 and report["relationships"] >= 0


def test_fulltext_search_finds_entity(neo_store):
    from graph_rag.knowledge_graph.extractor import Triple

    neo_store.upsert_triple(
        Triple(
            subject="UniqueSearchableName",
            subject_type="CONCEPT",
            relation="MENTIONS",
            object_="Something",
            object_type="CONCEPT",
        )
    )
    hits = neo_store.fulltext_search("UniqueSearchableName", limit=5)
    assert any(h["name"] == "UniqueSearchableName" for h in hits)
