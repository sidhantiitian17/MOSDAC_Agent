"""Document preprocessing layer — cleans, parses, and chunks PDFs and HTML files
before they enter the main ingestion pipeline."""
from graph_rag.preprocessing.preprocessor import preprocess_file

__all__ = ["preprocess_file"]
