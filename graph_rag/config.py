"""Centralized configuration loaded from .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

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
    downloads_dir: str = "./downloads"
    atlases_dir: str = "./atlases_pdfs"

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 100

    # Retrieval
    top_k_vector: int = 5
    top_k_graph: int = 10
    graph_depth: int = 2
    top_k_bm25: int = 5
    hybrid_rrf_k: int = 60

    # LLM — Tabby ML (OpenAI-compatible — active backend for both LLM and embeddings)
    # Credentials must come from .env. Never hardcode the token here.
    tabby_base_url: str = "http://localhost:8080/v1"
    tabby_api_token: str = ""
    tabby_model: str = "Qwen2-1.5B-Instruct"

    # Embeddings — Nomic Embed Text served by Tabby ML
    # NOMIC_BASE_URL and NOMIC_API_TOKEN fall back to the shared TABBY_* values
    # so a single Tabby ML instance serving both LLM and embeddings needs only
    # one set of credentials in .env. Override with NOMIC_* to point at a
    # separate embeddings server.
    nomic_model_name: str = "nomic-embed-text"
    nomic_base_url: str = Field(
        default="http://localhost:8080/v1",
        validation_alias=AliasChoices("nomic_base_url", "tabby_base_url"),
    )
    # Credential — never hardcoded. Resolved from NOMIC_API_TOKEN or TABBY_API_TOKEN.
    nomic_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("nomic_api_token", "tabby_api_token"),
    )

    # System prompt file path (change this to reconfigure LLM behaviour)
    system_prompt_path: str = "./prompts/system_prompt.txt"

    def source_folders(self) -> list[Path]:
        return [Path(self.downloads_dir), Path(self.atlases_dir)]


settings = Settings()
