"""Tests that Drupal ingestion is properly wired to IngestionPipeline.

These tests verify four guarantees:
  1. ingest_node() always delegates to IngestionPipeline.run_on_documents() —
     never to separate, duplicated KG or vector code.
  2. extract_at_document_level=True means one extractor call per source document,
     not one per chunk (fixes the N-LLM-calls-per-article bug).
  3. The quality gate (kg_min_confidence + RELATED_TO filter) is applied before
     upsert_triples() so junk triples never reach Neo4j.
  4. link_entities_to_source_chunks() is called after upsert_chunks() so Drupal
     entities get MENTIONED_IN provenance links — required for entity_chunks()
     retrieval to return grounding text to the chatbot.

All external services (Neo4j, ChromaDB, embedder, LLM extractor) are mocked,
so the suite runs without any live infrastructure.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from drupal_ingest import ParsedNode, _to_document, ingest_node
from graph_rag.knowledge_graph.extractor import Triple


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_parsed(uuid: str = "test-uuid-1", title: str = "Test Article") -> ParsedNode:
    long_text = (
        "INSAT-3D is a weather satellite operated by ISRO. "
        "It carries a six-channel imager and a 19-channel sounder. "
        "The satellite was launched in 2013 from Kourou, French Guiana. "
        "MOSDAC is the data archive for INSAT-3D products. "
        "The imager resolution is 1 km in visible channels. "
        "Cyclone tracking uses brightness temperature data from INSAT-3D. "
        "ISRO collaborates with EUMETSAT for algorithm development. "
    ) * 5  # ~1 500 chars — enough to produce multiple chunks after splitting
    return ParsedNode(
        uuid=uuid,
        title=title,
        text=long_text,
        body_html=f"<p>{long_text}</p>",
        content_hash="deadbeef" * 8,
    )


def _make_triples(source: str = "") -> list[Triple]:
    return [
        Triple("INSAT-3D", "Satellite", "OPERATED_BY", "ISRO", "Organisation",
               source_path=source, confidence=0.9),
        Triple("INSAT-3D", "Satellite", "CARRIES", "Imager", "Instrument",
               source_path=source, confidence=0.85),
        Triple("MOSDAC", "System", "RELATED_TO", "INSAT-3D", "Satellite",
               source_path=source, confidence=0.95),   # filtered — generic relation
        Triple("INSAT-3D", "Satellite", "LAUNCHED_FROM", "Kourou", "Location",
               source_path=source, confidence=0.4),    # filtered — below 0.6 threshold
    ]


# ── Patch helpers ─────────────────────────────────────────────────────────────

def _mock_neo4j():
    neo4j = MagicMock()
    neo4j.__enter__ = MagicMock(return_value=neo4j)
    neo4j.__exit__ = MagicMock(return_value=False)
    neo4j.ping.return_value = True
    neo4j.ensure_schema.return_value = None
    neo4j.upsert_triples.return_value = None
    neo4j.upsert_measurements.return_value = None
    neo4j.upsert_chunks.return_value = None
    neo4j.link_entities_to_source_chunks.return_value = None
    return neo4j


def _mock_extractor(triples: list[Triple]):
    ext = MagicMock()
    ext.extract.return_value = triples
    ext.extract_entities.return_value = []
    return ext


def _mock_embedder():
    emb = MagicMock()
    emb.embed_query.return_value = [0.1] * 768
    emb.embed_documents.return_value = [[0.1] * 768]
    return emb


def _mock_chroma():
    store = MagicMock()
    store.add_documents.return_value = ["chunk-1"]
    store.count.return_value = 0
    return store


# ── Tests ─────────────────────────────────────────────────────────────────────

_PIPELINE_CLS = "graph_rag.ingestion.pipeline.IngestionPipeline"


class TestDrupalPipelineDelegation:
    """ingest_node() must delegate entirely to IngestionPipeline.run_on_documents()."""

    @patch(_PIPELINE_CLS)
    def test_ingest_node_calls_run_on_documents(self, MockPipeline):
        """run_on_documents() is called — not run() (file-based entry point)."""
        mock_instance = MagicMock()
        mock_instance.run_on_documents.return_value = MagicMock(errors=[])
        MockPipeline.return_value = mock_instance

        parsed = _make_parsed()
        ingest_node(parsed, is_update=False)

        mock_instance.run_on_documents.assert_called_once()
        mock_instance.run.assert_not_called()

    @patch(_PIPELINE_CLS)
    def test_ingest_node_passes_extract_at_document_level(self, MockPipeline):
        """Pipeline is always constructed with extract_at_document_level=True."""
        mock_instance = MagicMock()
        mock_instance.run_on_documents.return_value = MagicMock(errors=[])
        MockPipeline.return_value = mock_instance

        ingest_node(_make_parsed(), is_update=False)

        _, kwargs = MockPipeline.call_args
        assert kwargs.get("extract_at_document_level") is True, (
            "extract_at_document_level must be True for Drupal so one LLM call is made "
            "per article, not one per chunk."
        )

    @patch(_PIPELINE_CLS)
    def test_ingest_node_document_contains_drupal_uuid_metadata(self, MockPipeline):
        """The Document passed to the pipeline carries drupal_uuid in metadata."""
        mock_instance = MagicMock()
        mock_instance.run_on_documents.return_value = MagicMock(errors=[])
        MockPipeline.return_value = mock_instance

        parsed = _make_parsed(uuid="abc-123")
        ingest_node(parsed, is_update=False)

        args, _ = mock_instance.run_on_documents.call_args
        docs: list[Document] = args[0]
        assert len(docs) == 1
        assert docs[0].metadata.get("drupal_uuid") == "abc-123"
        assert docs[0].metadata.get("source") == "abc-123"

    @patch("drupal_ingest._delete_stale_vector_chunks")
    @patch(_PIPELINE_CLS)
    def test_update_deletes_stale_chunks_before_pipeline(self, MockPipeline, mock_delete):
        """For UPDATED nodes stale Chroma chunks are purged before re-indexing."""
        mock_instance = MagicMock()
        MockPipeline.return_value = mock_instance

        call_order = []
        mock_delete.side_effect = lambda uuid: call_order.append("delete")
        mock_instance.run_on_documents.side_effect = (
            lambda docs: call_order.append("pipeline") or MagicMock(errors=[])
        )

        parsed = _make_parsed(uuid="uuid-upd")
        ingest_node(parsed, is_update=True)

        mock_delete.assert_called_once_with("uuid-upd")
        assert call_order == ["delete", "pipeline"], (
            "Stale chunks must be deleted BEFORE the pipeline adds new ones."
        )

    @patch("drupal_ingest._delete_stale_vector_chunks")
    @patch(_PIPELINE_CLS)
    def test_new_node_does_not_delete_chunks(self, MockPipeline, mock_delete):
        """For NEW nodes _delete_stale_vector_chunks must not be called."""
        mock_instance = MagicMock()
        mock_instance.run_on_documents.return_value = MagicMock(errors=[])
        MockPipeline.return_value = mock_instance

        ingest_node(_make_parsed(), is_update=False)

        mock_delete.assert_not_called()


class TestDocumentLevelExtraction:
    """_build_kg_document_level() must call the extractor exactly once per source document."""

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_extractor_called_once_per_document(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """One article → one extractor.extract() call, regardless of chunk count."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples("test-uuid-1"))
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed(uuid="test-uuid-1"))
        pipeline = IngestionPipeline(
            extract_at_document_level=True,
            kg_min_confidence=0.0,
        )
        stats = pipeline.run_on_documents([doc])

        assert extractor.extract.call_count == 1, (
            f"Expected 1 extractor call (document-level), got {extractor.extract.call_count}. "
            "This means chunk-level extraction is running instead of document-level."
        )
        assert stats.errors == []

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_extractor_receives_full_article_text(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """The text passed to extract() contains content from all chunks."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor([])
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.0)
        pipeline.run_on_documents([doc])

        assert extractor.extract.called
        extracted_text: str = extractor.extract.call_args[0][0]
        assert "INSAT-3D" in extracted_text
        assert "ISRO" in extracted_text

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_two_documents_two_extractor_calls(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """Two distinct source documents → exactly two extractor calls."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor([])
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc1 = _to_document(_make_parsed(uuid="uuid-A", title="Article A"))
        doc2 = _to_document(_make_parsed(uuid="uuid-B", title="Article B"))
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.0)
        pipeline.run_on_documents([doc1, doc2])

        assert extractor.extract.call_count == 2, (
            f"Two documents should produce exactly 2 extractor calls, got {extractor.extract.call_count}."
        )


class TestQualityGate:
    """Low-confidence and RELATED_TO triples must not reach upsert_triples()."""

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_low_confidence_triples_filtered(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """Triples with confidence < kg_min_confidence must not be upserted."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples())
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        pipeline.run_on_documents([doc])

        assert neo4j.upsert_triples.called
        upserted: list[Triple] = neo4j.upsert_triples.call_args[0][0]
        low_conf = [t for t in upserted if t.confidence < 0.6]
        related_to = [t for t in upserted if t.relation == "RELATED_TO"]

        assert not low_conf, (
            f"Triples with confidence < 0.6 reached upsert_triples: {low_conf}"
        )
        assert not related_to, (
            "Generic RELATED_TO triples must be filtered by the quality gate."
        )

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_quality_gate_off_at_zero_confidence(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """kg_min_confidence=0.0 disables the gate — all triples including RELATED_TO pass."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples())
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.0)
        pipeline.run_on_documents([doc])

        assert neo4j.upsert_triples.called
        upserted: list[Triple] = neo4j.upsert_triples.call_args[0][0]
        assert len(upserted) == 4, (
            f"With gate disabled all 4 triples should be upserted, got {len(upserted)}."
        )

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_all_triples_filtered_skips_upsert(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """If every triple is filtered out, upsert_triples() must not be called at all."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        bad_triples = [
            Triple("A", "T", "RELATED_TO", "B", "T", confidence=0.95),
            Triple("C", "T", "RELATED_TO", "D", "T", confidence=0.3),
        ]
        extractor = _mock_extractor(bad_triples)
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        stats = pipeline.run_on_documents([doc])

        neo4j.upsert_triples.assert_not_called()
        assert stats.errors == []


class TestNeo4jWritePaths:
    """After quality gate, the same Neo4j methods are called as in file ingestion."""

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_upsert_triples_and_chunks_called(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """upsert_triples() and upsert_chunks() must both be called."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        triples = [
            Triple("INSAT-3D", "Satellite", "OPERATED_BY", "ISRO", "Organisation", confidence=0.9),
        ]
        extractor = _mock_extractor(triples)
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        stats = pipeline.run_on_documents([doc])

        neo4j.upsert_triples.assert_called_once()
        neo4j.upsert_chunks.assert_called_once()
        assert stats.relationships_created >= 1
        assert stats.errors == []

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_stats_reflect_upserted_triples(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """IngestionStats.relationships_created counts only triples that passed the gate."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples())
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        stats = pipeline.run_on_documents([doc])

        # _make_triples(): OPERATED_BY(0.9) ✓  CARRIES(0.85) ✓  RELATED_TO(0.95) ✗  LAUNCHED_FROM(0.4) ✗
        assert stats.relationships_created == 2, (
            f"Expected 2 triples to pass the quality gate, got {stats.relationships_created}."
        )


class TestMentionedInProvenance:
    """Entities extracted at document level must have MENTIONED_IN links to their chunks.

    Without these links, entity_chunks() retrieval returns empty and the chatbot
    has no grounding text for Drupal articles. link_entities_to_source_chunks()
    was added to neo4j_store.py specifically to close this gap.
    """

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_link_entities_called_after_upsert_chunks(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """link_entities_to_source_chunks() must be called after upsert_chunks()."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples())
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        call_order = []
        neo4j.upsert_chunks.side_effect = lambda r: call_order.append("upsert_chunks")
        neo4j.link_entities_to_source_chunks.side_effect = (
            lambda ks, s: call_order.append("link_entities")
        )

        doc = _to_document(_make_parsed(uuid="test-uuid-1"))
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        pipeline.run_on_documents([doc])

        assert "upsert_chunks" in call_order, "upsert_chunks() was never called."
        assert "link_entities" in call_order, (
            "link_entities_to_source_chunks() was never called. "
            "Drupal entities will have no MENTIONED_IN provenance — retrieval returns empty."
        )
        chunks_idx = call_order.index("upsert_chunks")
        link_idx = call_order.index("link_entities")
        assert chunks_idx < link_idx, (
            "link_entities_to_source_chunks() must be called AFTER upsert_chunks() "
            "so the Chunk nodes exist when the MATCH fires."
        )

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_link_receives_correct_source_uuid(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """link_entities_to_source_chunks() is called with the Drupal UUID as source."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples())
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        drupal_uuid = "drupal-uuid-abc-123"
        doc = _to_document(_make_parsed(uuid=drupal_uuid))
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        pipeline.run_on_documents([doc])

        neo4j.link_entities_to_source_chunks.assert_called_once()
        _, source_arg = neo4j.link_entities_to_source_chunks.call_args[0]
        assert source_arg == drupal_uuid, (
            f"Expected source='{drupal_uuid}', got '{source_arg}'. "
            "The source must be the Drupal UUID so the MATCH (c:Chunk) finds the right chunks."
        )

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_two_documents_two_link_calls(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """Each source document gets its own link_entities_to_source_chunks() call."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        extractor = _mock_extractor(_make_triples())
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc1 = _to_document(_make_parsed(uuid="uuid-A", title="Article A"))
        doc2 = _to_document(_make_parsed(uuid="uuid-B", title="Article B"))
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        pipeline.run_on_documents([doc1, doc2])

        assert neo4j.link_entities_to_source_chunks.call_count == 2, (
            f"Expected 2 link calls (one per source document), "
            f"got {neo4j.link_entities_to_source_chunks.call_count}."
        )
        sources_linked = {
            call[0][1]
            for call in neo4j.link_entities_to_source_chunks.call_args_list
        }
        assert sources_linked == {"uuid-A", "uuid-B"}

    @patch("graph_rag.knowledge_graph.neo4j_store.Neo4jStore")
    @patch("graph_rag.knowledge_graph.llm_extractor.get_extractor")
    @patch("graph_rag.knowledge_graph.quantity_parser.parse_quantities", return_value=[])
    @patch("graph_rag.vector_store.chroma_store.ChromaStore")
    @patch("graph_rag.embeddings.get_embedder")
    def test_no_link_call_when_all_triples_filtered(
        self, mock_get_emb, MockChroma, mock_pq, mock_get_ext, MockNeo4j
    ):
        """When all triples are filtered out, link_entities_to_source_chunks() is not called."""
        from graph_rag.ingestion.pipeline import IngestionPipeline

        neo4j = _mock_neo4j()
        MockNeo4j.return_value = neo4j
        # All triples filtered (RELATED_TO + low confidence)
        extractor = _mock_extractor([
            Triple("A", "T", "RELATED_TO", "B", "T", confidence=0.9),
        ])
        mock_get_ext.return_value = extractor
        mock_get_emb.return_value = _mock_embedder()
        MockChroma.return_value = _mock_chroma()

        doc = _to_document(_make_parsed())
        pipeline = IngestionPipeline(extract_at_document_level=True, kg_min_confidence=0.6)
        pipeline.run_on_documents([doc])

        neo4j.link_entities_to_source_chunks.assert_not_called()
