"""End-to-end ingestion: load -> split -> embed/store in Chroma + extract/store in Neo4j."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.documents import Document
from tqdm.auto import tqdm

from graph_rag.ingestion.loader import load_all_documents
from graph_rag.ingestion.splitter import split_documents

logger = logging.getLogger(__name__)


@dataclass
class IngestionStats:
    documents_loaded: int = 0
    chunks_created: int = 0
    chunks_indexed: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    measurements_created: int = 0
    extraction_backend: str = ""
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Ingestion summary:\n"
            f"  documents loaded   : {self.documents_loaded}\n"
            f"  chunks created     : {self.chunks_created}\n"
            f"  chunks indexed     : {self.chunks_indexed}\n"
            f"  extraction backend : {self.extraction_backend or 'n/a'}\n"
            f"  entities created   : {self.entities_created}\n"
            f"  relationships made : {self.relationships_created}\n"
            f"  measurements made  : {self.measurements_created}\n"
            f"  errors             : {len(self.errors)}"
        )


class IngestionPipeline:
    """Orchestrates document ingestion across both vector store and knowledge graph.

    Two entry points:
      run()               — full pipeline: discover files from disk → split → vector → KG.
                            Handles the content-hash manifest (skips already-ingested files).
      run_on_documents()  — starts at the split step with pre-loaded Document objects.
                            No file discovery, no manifest — used by Drupal ingestion.

    Both share _process_documents() for steps 2-4, so Drupal and file ingestion execute
    through identical vector/KG code paths.
    """

    def __init__(
        self,
        folders: list[Path] | None = None,
        skip_vector: bool = False,
        skip_graph: bool = False,
        force: bool = False,
        extract_at_document_level: bool = False,
        kg_min_confidence: float = 0.0,
    ):
        self.folders = folders
        self.skip_vector = skip_vector
        self.skip_graph = skip_graph
        # force=True re-ingests every file, ignoring the content-hash manifest.
        self.force = force
        # When True, one LLM extraction call is made per source document (full text)
        # rather than per chunk. Reduces LLM calls for long documents (e.g. Drupal articles).
        self.extract_at_document_level = extract_at_document_level
        # Triples below this confidence score are discarded (0.0 = keep all).
        self.kg_min_confidence = kg_min_confidence

    # Entity types that make the best anchor for a measurement (a spec like
    # "1 km resolution" belongs to the Sensor/Satellite the sentence is about).
    _ANCHOR_PRIORITY = ("Satellite", "Sensor", "Instrument", "Product", "Mission")

    @classmethod
    def _pick_anchor(cls, triples, extractor, text) -> tuple[str, str] | None:
        """Choose the entity a chunk's measurements should attach to."""
        for t in triples:
            if t.subject_type in cls._ANCHOR_PRIORITY:
                return (t.subject, t.subject_type)
        for t in triples:
            if t.object_type in cls._ANCHOR_PRIORITY:
                return (t.object_, t.object_type)
        if triples:
            return (triples[0].subject, triples[0].subject_type)
        try:
            for name, typ in extractor.extract_entities(text):
                return (name, typ)
        except Exception:
            pass
        return None

    # ── Public entry points ───────────────────────────────────────────────────

    def run(self) -> IngestionStats:
        """Full pipeline: file discovery → split → vector → KG → manifest update."""
        from graph_rag.config import settings
        from graph_rag.ingestion.manifest import IngestionManifest

        # Content-hash manifest: skip files already fully ingested on a prior run.
        # force=True bypasses it (re-ingest everything).
        manifest = None if self.force else IngestionManifest.load(settings.ingest_manifest_path)

        logger.info("Step 1/4 — discovering and loading documents")
        documents = load_all_documents(self.folders, manifest=manifest, force=self.force)

        if not documents:
            if manifest is not None and manifest.entries:
                logger.info("Nothing to ingest — all discovered files are already in the manifest.")
            else:
                logger.warning("No documents found. Check DOWNLOADS_DIR/ATLASES_DIR.")
            return IngestionStats()

        stats, chunks = self._process_documents(documents)

        # Record newly-ingested files by content hash — only after a complete, clean
        # run (both stores populated, no errors), so a partial/failed run is safely
        # retried next time instead of being wrongly marked as done.
        if (
            manifest is not None
            and not stats.errors
            and not self.skip_vector
            and not self.skip_graph
        ):
            files: dict[str, dict] = {}
            for chunk in chunks:
                file_hash = chunk.metadata.get("file_hash")
                if not file_hash:
                    continue
                rec = files.setdefault(
                    file_hash,
                    {
                        "source": chunk.metadata.get("source", ""),
                        "file_name": chunk.metadata.get("file_name", ""),
                        "chunk_count": 0,
                    },
                )
                rec["chunk_count"] += 1

            for file_hash, rec in files.items():
                manifest.record(
                    file_hash,
                    source=rec["source"],
                    file_name=rec["file_name"],
                    chunk_count=rec["chunk_count"],
                )
            if files:
                manifest.save()
                logger.info("Recorded %d newly-ingested file(s) in the manifest.", len(files))

        return stats

    def run_on_documents(self, documents: list[Document]) -> IngestionStats:
        """Split → vector → KG for pre-loaded documents (e.g. from Drupal).

        Skips file discovery and the manifest — the caller manages its own delta-sync
        state. Uses the same _process_documents() path as run() so KG extraction,
        quantity parsing, measurements, and Neo4j writes are identical.
        """
        if not documents:
            return IngestionStats()
        stats, _ = self._process_documents(documents)
        return stats

    # ── Shared steps 2-4 ─────────────────────────────────────────────────────

    def _process_documents(
        self, documents: list[Document]
    ) -> tuple[IngestionStats, list[Document]]:
        """Split → embed into Chroma → extract KG into Neo4j.

        Returns (stats, chunks) so run() can use chunks for the manifest update.
        """
        stats = IngestionStats()
        stats.documents_loaded = len(documents)

        logger.info("Step 2/4 — splitting %d document(s) into chunks", len(documents))
        chunks = split_documents(documents)
        stats.chunks_created = len(chunks)
        logger.info("Created %d chunks", len(chunks))

        if not self.skip_vector:
            logger.info("Step 3/4 — embedding & storing in ChromaDB")
            try:
                from graph_rag.embeddings import get_embedder
                from graph_rag.vector_store.chroma_store import ChromaStore

                store = ChromaStore(embedder=get_embedder())
                added = store.add_documents(chunks)
                stats.chunks_indexed = len(added)
                logger.info("Indexed %d chunks into ChromaDB", len(added))
            except Exception as exc:
                logger.exception("Vector indexing failed: %s", exc)
                stats.errors.append(f"vector: {exc}")
        else:
            logger.info("Step 3/4 — skipped (skip_vector=True)")

        if not self.skip_graph:
            logger.info("Step 4/4 — extracting triples + measurements & storing in Neo4j")
            if self.extract_at_document_level:
                self._build_kg_document_level(chunks, stats)
            else:
                self._build_kg_chunk_level(chunks, stats)
        else:
            logger.info("Step 4/4 — skipped (skip_graph=True)")

        return stats, chunks

    # ── KG extraction strategies ──────────────────────────────────────────────

    def _build_kg_chunk_level(
        self, chunks: list[Document], stats: IngestionStats
    ) -> None:
        """Original per-chunk extraction — used by file ingestion (unchanged behaviour)."""
        neo4j = None
        try:
            from graph_rag.knowledge_graph.llm_extractor import get_extractor
            from graph_rag.knowledge_graph.neo4j_store import Neo4jStore
            from graph_rag.knowledge_graph.quantity_parser import parse_quantities
            from graph_rag.knowledge_graph.resolver import canonical_key

            extractor = get_extractor()
            stats.extraction_backend = type(extractor).__name__
            neo4j = Neo4jStore()
            neo4j.ensure_schema()

            rels_total = 0
            meas_total = 0
            entity_keys: set[str] = set()
            chunk_records: list[dict] = []

            for chunk in tqdm(chunks, desc="Building KG"):
                chunk_id = chunk.metadata.get("chunk_id", "")
                source = chunk.metadata.get("source", "")
                text = chunk.page_content

                # 1) Typed relationship triples (LLM or spaCy).
                triples = extractor.extract(text, source_chunk_id=chunk_id, source_path=source)
                if triples:
                    neo4j.upsert_triples(triples)
                    rels_total += len(triples)
                    for t in triples:
                        entity_keys.add(canonical_key(t.subject))
                        entity_keys.add(canonical_key(t.object_))

                # 2) Quantitative specs → comparable Measurement nodes.
                quantities = parse_quantities(text)
                if quantities:
                    anchor = self._pick_anchor(triples, extractor, text)
                    if anchor is not None:
                        anchor_name, anchor_type = anchor
                        neo4j.upsert_measurements(
                            [
                                {
                                    "entity": anchor_name,
                                    "entity_type": anchor_type,
                                    "property": q.property_key,
                                    "value": q.value,
                                    "unit": q.unit,
                                    "raw": q.raw,
                                    "base_value": q.base_value,
                                    "base_unit": q.base_unit,
                                    "chunk_id": chunk_id,
                                    "source": source,
                                }
                                for q in quantities
                            ]
                        )
                        meas_total += len(quantities)
                        entity_keys.add(canonical_key(anchor_name))

                # 3) Provenance: keep chunk text so facts can cite evidence.
                if chunk_id:
                    chunk_records.append({"chunk_id": chunk_id, "text": text, "source": source})

            if chunk_records:
                neo4j.upsert_chunks(chunk_records)

            neo4j.close()
            stats.entities_created = len(entity_keys)
            stats.relationships_created = rels_total
            stats.measurements_created = meas_total

        except Exception as exc:
            if neo4j is not None:
                neo4j.close()
            # KeyboardInterrupt hits a socket recv_into and gets masked by
            # Neo4j's buffer cleanup raising BufferError on session __exit__.
            # Detect via exception chain and re-raise so Ctrl+C aborts.
            if isinstance(exc.__context__, KeyboardInterrupt):
                raise KeyboardInterrupt from exc.__context__
            logger.exception("Knowledge graph build failed: %s", exc)
            stats.errors.append(f"graph: {exc}")

    def _build_kg_document_level(
        self, chunks: list[Document], stats: IngestionStats
    ) -> None:
        """Document-level extraction — one LLM call per source document, not per chunk.

        Groups chunks by their `source` metadata key, concatenates their text up to
        extraction_max_chars, and calls the extractor once per document. This gives
        the model full article context so it can link entities across paragraphs and
        dramatically reduces LLM calls (N chunks → 1 call per article).

        Quantity/measurement parsing still runs per-chunk (deterministic regex, no LLM).

        All Neo4j writes (upsert_triples, upsert_measurements, upsert_chunks) go
        through the same methods as _build_kg_chunk_level — identical storage path.
        """
        neo4j = None
        try:
            from graph_rag.config import settings
            from graph_rag.knowledge_graph.llm_extractor import get_extractor
            from graph_rag.knowledge_graph.neo4j_store import Neo4jStore
            from graph_rag.knowledge_graph.quantity_parser import parse_quantities
            from graph_rag.knowledge_graph.resolver import canonical_key

            extractor = get_extractor()
            stats.extraction_backend = type(extractor).__name__
            neo4j = Neo4jStore()
            neo4j.ensure_schema()

            # Group retrieval chunks by their source document.
            by_source: dict[str, list[Document]] = defaultdict(list)
            for chunk in chunks:
                by_source[chunk.metadata.get("source", "")].append(chunk)

            rels_total = 0
            meas_total = 0
            entity_keys: set[str] = set()

            for source, doc_chunks in tqdm(by_source.items(), desc="Building KG (doc-level)"):
                # ── 1) One LLM call on the full document text ─────────────────
                full_text = "\n\n".join(c.page_content for c in doc_chunks)
                snippet = full_text[: settings.extraction_max_chars]

                triples = extractor.extract(snippet, source_chunk_id="", source_path=source)

                # Quality gate: drop low-confidence and generic catch-all relations.
                if self.kg_min_confidence > 0.0:
                    triples = [
                        t for t in triples
                        if t.confidence >= self.kg_min_confidence
                        and t.relation != "RELATED_TO"
                    ]

                doc_entity_keys: set[str] = set()
                if triples:
                    neo4j.upsert_triples(triples)
                    rels_total += len(triples)
                    for t in triples:
                        doc_entity_keys.add(canonical_key(t.subject))
                        doc_entity_keys.add(canonical_key(t.object_))
                    entity_keys.update(doc_entity_keys)

                # ── 2) Per-chunk quantities + measurements (same as chunk-level) ──
                source_chunk_records: list[dict] = []
                for chunk in doc_chunks:
                    chunk_id = chunk.metadata.get("chunk_id", "")
                    text = chunk.page_content
                    quantities = parse_quantities(text)
                    if quantities:
                        anchor = self._pick_anchor(triples, extractor, text)
                        if anchor is not None:
                            anchor_name, anchor_type = anchor
                            neo4j.upsert_measurements(
                                [
                                    {
                                        "entity": anchor_name,
                                        "entity_type": anchor_type,
                                        "property": q.property_key,
                                        "value": q.value,
                                        "unit": q.unit,
                                        "raw": q.raw,
                                        "base_value": q.base_value,
                                        "base_unit": q.base_unit,
                                        "chunk_id": chunk_id,
                                        "source": source,
                                    }
                                    for q in quantities
                                ]
                            )
                            meas_total += len(quantities)
                            anchor_key = canonical_key(anchor_name)
                            entity_keys.add(anchor_key)
                            doc_entity_keys.add(anchor_key)

                    if chunk_id:
                        source_chunk_records.append({"chunk_id": chunk_id, "text": text, "source": source})

                # ── 3) Store this source's chunks then link entities → chunks ─
                # Chunks must exist before link_entities_to_source_chunks() so
                # the MATCH (c:Chunk) finds them. Doing this per-source lets us
                # wire MENTIONED_IN immediately rather than in a separate pass.
                if source_chunk_records:
                    neo4j.upsert_chunks(source_chunk_records)
                    if doc_entity_keys:
                        neo4j.link_entities_to_source_chunks(
                            list(doc_entity_keys), source
                        )

            neo4j.close()
            stats.entities_created = len(entity_keys)
            stats.relationships_created = rels_total
            stats.measurements_created = meas_total

        except Exception as exc:
            if neo4j is not None:
                neo4j.close()
            if isinstance(exc.__context__, KeyboardInterrupt):
                raise KeyboardInterrupt from exc.__context__
            logger.exception("Document-level KG build failed: %s", exc)
            stats.errors.append(f"graph: {exc}")
