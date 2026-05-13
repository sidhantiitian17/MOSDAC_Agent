"""Document ingestion: loader, splitter, pipeline."""
from graph_rag.ingestion.loader import load_all_documents
from graph_rag.ingestion.pipeline import IngestionPipeline
from graph_rag.ingestion.splitter import split_documents

__all__ = ["load_all_documents", "split_documents", "IngestionPipeline"]
