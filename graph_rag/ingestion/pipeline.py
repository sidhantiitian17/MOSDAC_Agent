"""End-to-end ingestion: load -> split -> embed/store in Chroma + extract/store in Neo4j."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from tqdm.auto import tqdm

from graph_rag.ingestion.loader import load_all_documents
from graph_rag.ingestion.splitter import split_documents

_EMBED_BATCH_SIZE = 50   # chunks per Gemini request batch
_EMBED_BATCH_DELAY = 1.5  # seconds between batches to respect rate limits

logger = logging.getLogger(__name__)


@dataclass
class IngestionStats:
    documents_loaded: int = 0
    chunks_created: int = 0
    chunks_indexed: int = 0
    entities_created: int = 0
    relationships_created: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Ingestion summary:\n"
            f"  documents loaded   : {self.documents_loaded}\n"
            f"  chunks created     : {self.chunks_created}\n"
            f"  chunks indexed     : {self.chunks_indexed}\n"
            f"  entities created   : {self.entities_created}\n"
            f"  relationships made : {self.relationships_created}\n"
            f"  errors             : {len(self.errors)}"
        )


class IngestionPipeline:
    """Orchestrates document ingestion across both vector store and knowledge graph."""

    def __init__(
        self,
        folders: list[Path] | None = None,
        skip_vector: bool = False,
        skip_graph: bool = False,
    ):
        self.folders = folders
        self.skip_vector = skip_vector
        self.skip_graph = skip_graph

    def run(self) -> IngestionStats:
        stats = IngestionStats()

        logger.info("Step 1/4 — discovering and loading documents")
        documents = load_all_documents(self.folders)
        stats.documents_loaded = len(documents)
        if not documents:
            logger.warning("No documents found. Check DOWNLOADS_DIR/ATLASES_DIR.")
            return stats

        logger.info("Step 2/4 — splitting %d documents into chunks", len(documents))
        chunks = split_documents(documents)
        stats.chunks_created = len(chunks)
        logger.info("Created %d chunks", len(chunks))

        if not self.skip_vector:
            logger.info("Step 3/4 — embedding & storing in ChromaDB")
            try:
                from graph_rag.embeddings.nvidia_embedder import get_embedder
                from graph_rag.vector_store.chroma_store import ChromaStore

                store = ChromaStore(embedder=get_embedder())
                total_indexed = 0
                batch_errors = 0
                for i in tqdm(range(0, len(chunks), _EMBED_BATCH_SIZE), desc="Embedding batches"):
                    batch = chunks[i : i + _EMBED_BATCH_SIZE]
                    try:
                        added = store.add_documents(batch)
                        total_indexed += len(added)
                    except Exception as batch_exc:
                        batch_errors += 1
                        logger.warning("Batch %d/%d failed: %s", i // _EMBED_BATCH_SIZE + 1,
                                       (len(chunks) + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE,
                                       batch_exc)
                        stats.errors.append(f"batch {i}: {batch_exc}")
                stats.chunks_indexed = total_indexed
                if batch_errors:
                    logger.warning("%d batches failed; re-run ingest to resume.", batch_errors)
                logger.info("Indexed %d chunks into ChromaDB", total_indexed)
            except Exception as exc:
                logger.exception("Vector indexing failed: %s", exc)
                stats.errors.append(f"vector: {exc}")
        else:
            logger.info("Step 3/4 — skipped (skip_vector=True)")

        if not self.skip_graph:
            logger.info("Step 4/4 — extracting triples & storing in Neo4j")
            try:
                from graph_rag.knowledge_graph.extractor import EntityRelationExtractor
                from graph_rag.knowledge_graph.neo4j_store import Neo4jStore

                extractor = EntityRelationExtractor()
                neo4j = Neo4jStore()
                neo4j.ensure_schema()

                entities_total = 0
                rels_total = 0
                for chunk in tqdm(chunks, desc="Extracting triples"):
                    triples = extractor.extract(
                        chunk.page_content,
                        source_chunk_id=chunk.metadata.get("chunk_id", ""),
                        source_path=chunk.metadata.get("source", ""),
                    )
                    for t in triples:
                        neo4j.upsert_triple(t)
                        entities_total += 2
                        rels_total += 1
                neo4j.close()
                stats.entities_created = entities_total
                stats.relationships_created = rels_total
            except Exception as exc:
                logger.exception("Knowledge graph build failed: %s", exc)
                stats.errors.append(f"graph: {exc}")
        else:
            logger.info("Step 4/4 — skipped (skip_graph=True)")

        return stats
