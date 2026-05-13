"""Centralized configuration loaded from .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM — LongCat (OpenAI-compatible, swappable to any local Docker model)
    longcat_api_key: str = "missing"
    longcat_model: str = "LongCat-Flash-Chat"
    longcat_api_base: str = "https://api.longcat.chat/openai"
    longcat_api_format: str = "openai"

    # Embeddings — Gemini (kept for reference; no longer used by default)
    gemini_api_key: str = "missing"
    gemini_embedding_model: str = "models/gemini-embedding-001"

    # Embeddings — NVIDIA NIM
    nvidia_api_key: str = "missing"
    nvidia_embedding_model: str = "nvidia/llama-nemotron-embed-1b-v2"

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "neo4j_password"
    neo4j_database: str = "neo4j"

    # ChromaDB
    chroma_persist_dir: str = "./chroma_db"
    chroma_collection: str = "graph_rag"

    # OCR — Tesseract + Poppler paths (Windows; empty = rely on PATH)
    tesseract_cmd: str = ""
    poppler_path: str = ""

    # Data source folders (HTML + PDF ingestion)
    downloads_dir: str = "D:/AI_agents/downloads"
    atlases_dir: str = "D:/AI_agents/atlases_pdfs"

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 100

    # Retrieval
    top_k_vector: int = 5
    top_k_graph: int = 10
    graph_depth: int = 2

    def source_folders(self) -> list[Path]:
        return [Path(self.downloads_dir), Path(self.atlases_dir)]


settings = Settings()
