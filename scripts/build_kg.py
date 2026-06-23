"""Build the Neo4j knowledge graph from chunks ALREADY in Chroma — no re-Docling.

Why this exists: the full pipeline rebuilds the KG by re-parsing the PDF (Docling,
~3 min + a ~2 GB memory spike). When the vector store is already populated, the KG
can be (re)built directly from the persisted chunks. This is also the robust way to
run the slow, CPU-bound per-chunk LLM extraction as a DEDICATED one-off container
(`docker compose run`) that is independent of the chat_api container's lifecycle.

Run (dedicated container, survives chat_api restarts):
    docker compose run -d --name mosdac_kg chat_api python -c "$(cat scripts/build_kg.py)"
or, if the file is present in the image/mount:
    docker compose run --rm chat_api python /app/scripts/build_kg.py
"""
from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s | %(message)s')
log = logging.getLogger('build_kg')


def main() -> int:
    from langchain_core.documents import Document

    from graph_rag.embeddings import get_embedder
    from graph_rag.ingestion.pipeline import IngestionPipeline, IngestionStats
    from graph_rag.vector_store.chroma_store import ChromaStore

    raw = ChromaStore(embedder=get_embedder()).get_all_chunks()
    ids = raw.get('ids', []) or []
    texts = raw.get('documents', []) or []
    metas = raw.get('metadatas', []) or []
    if not ids:
        log.error('Chroma has no chunks — ingest the vector store first.')
        return 1

    # The Chroma id IS the chunk_id; carry source through so provenance/links work.
    docs = [
        Document(page_content=t, metadata={**(m or {}), 'chunk_id': i})
        for i, t, m in zip(ids, texts, metas)
    ]
    log.info('Reconstructed %d chunks from Chroma; starting KG extraction...', len(docs))

    stats = IngestionStats()
    # skip_vector=True → only the Neo4j (triples + measurements + chunks) path runs.
    IngestionPipeline(skip_vector=True)._build_kg_chunk_level(docs, stats)

    print(stats.summary())
    if stats.errors:
        for e in stats.errors:
            log.error('KG error: %s', e)
        return 1
    print('KG_BUILD_DONE entities=%d relationships=%d measurements=%d'
          % (stats.entities_created, stats.relationships_created, stats.measurements_created))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
