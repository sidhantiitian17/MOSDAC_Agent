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
    # No baked-in password default (L5): when the graph runs with auth enabled this
    # MUST come from NEO4J_PASSWORD in the environment. Empty is correct for the
    # local NEO4J_AUTH=none dev container (the server ignores credentials there);
    # a real deployment supplies a strong password via .env and enables auth (B2).
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    # Driver connection pool / timeout tuning (P2-4). Bounds resource use and
    # prevents indefinite hangs if Neo4j is slow or bouncing. All env-driven.
    neo4j_max_pool_size: int = 50
    neo4j_connection_timeout: float = 30.0
    neo4j_max_connection_lifetime: float = 3600.0

    # ChromaDB
    chroma_persist_dir: str = "./chroma_db"
    chroma_collection: str = "graph_rag"

    # OCR — Tesseract + Poppler (fallback for image-only PDFs). Leave empty to
    # rely on PATH, which is the correct setting on Linux/macOS:
    #   Linux:  apt-get install tesseract-ocr poppler-utils  (already in Dockerfile.api)
    #   macOS:  brew install tesseract poppler
    #   Windows: install binaries and set these vars, e.g.:
    #     TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
    #     POPPLER_PATH=C:/path/to/poppler/bin
    tesseract_cmd: str = ""
    poppler_path: str = ""

    # ── Docling (primary PDF parser) ────────────────────────────────────────
    # Toggle Docling on/off without code changes. When False the loader uses
    # the legacy pypdf→PyMuPDF→OCR cascade only.
    use_docling: bool = True
    # OCR every page of these image-heavy PDFs (labels burned into raster).
    # Match on path substring; defaults to the atlases folder.
    docling_force_full_page_ocr_dirs: str = "atlases_pdfs"
    # Tesseract language(s) for Docling OCR — must match installed tessdata.
    docling_ocr_lang: str = "eng"
    # Extract formulas as LaTeX ($$...$$). Uses the CPU CodeFormula model.
    docling_do_formula_enrichment: bool = True
    # Parse tables into structured Markdown tables (TableFormer).
    docling_do_table_structure: bool = True
    # Skip Docling for files larger than this (MB) — guards against OOM on huge atlases.
    docling_max_file_mb: int = 250
    # Cap pages parsed per document (0 = no cap). Bounds worst-case memory/time.
    docling_max_pages: int = 0
    # OFFLINE / AIR-GAPPED: directory holding pre-downloaded Docling models
    # (layout, TableFormer, CodeFormula). When set, the converter loads models
    # from this local path and makes NO HuggingFace Hub calls at parse time. The
    # Docker image populates it at build (`docling-tools models download -o ...`)
    # and exports DOCLING_ARTIFACTS_PATH, which maps to this field. Empty = let
    # Docling fetch/cache from the Hub on first use (needs network).
    docling_artifacts_path: str = ""

    # ── Universal multi-format ingestion ────────────────────────────────────
    # Kill-switches for whole format families (registry: graph_rag/ingestion/
    # formats.py). Flip to False to stop discovering/parsing that category — no
    # code change needed. Core PDF/HTML/text are always on.
    ingest_enable_office: bool = True   # .docx / .xlsx / .pptx / .csv / .adoc
    ingest_enable_images: bool = True   # .png / .jpg / .tiff / .bmp / .webp / .gif (OCR)
    ingest_image_max_mb: int = 50       # skip oversized images (OOM guard)

    # Garbage-data quality gate (graph_rag/preprocessing/quality.py). All gates
    # are config-driven so a noisy corpus can be tuned without code edits.
    ingest_min_chars: int = 40             # drop near-empty extractions
    ingest_min_alnum_ratio: float = 0.50   # drop OCR gibberish / binary noise
    ingest_min_unique_tokens: int = 8      # drop degenerate repetition
    ingest_max_repeat_ratio: float = 0.40  # drop "the the the…" loops
    ingest_max_replacement_ratio: float = 0.10  # drop replacement/control-char soup

    # Data source folders (HTML + PDF ingestion)
    downloads_dir: str = "./downloads"
    atlases_dir: str = "./atlases_pdfs"

    # Incremental ingestion: content-hash manifest of already-ingested files.
    # Files whose SHA-256 hash is recorded here are skipped on subsequent runs.
    ingest_manifest_path: str = "./ingest_manifest.json"

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 100
    # Cap on a single header-section chunk. Sections longer than this are
    # sub-split (math/table-safe, with overlap) so they stay within the
    # embedder's context window and remain fully searchable. Set
    # enable_section_subsplit=False to restore whole-section chunking.
    chunk_max_section_chars: int = 1600
    enable_section_subsplit: bool = True

    # Retrieval
    top_k_vector: int = 5
    top_k_graph: int = 10
    graph_depth: int = 2
    top_k_bm25: int = 5
    hybrid_rrf_k: int = 60
    # BM25 staleness guard (P1-4): the in-memory keyword index is rebuilt when the
    # underlying Chroma collection count changes (e.g. after a re-ingest), so a
    # long-running server never serves a stale keyword view. Set False to pin the
    # index for the instance lifetime (lowest overhead, manual reload only).
    bm25_auto_refresh: bool = True

    # ── Formula / quantitative precision ────────────────────────────────────
    # Exact-substring fast path: when a query carries math notation, inject
    # chunks containing the verbatim symbol run at the top of the candidate pool.
    enable_exact_formula_match: bool = True
    # Feature boost: nudge has_formula / numeric-dense chunks up the ranking for
    # numeric/formula queries (boosts ordering only, not the grounding score).
    enable_feature_boost: bool = True
    feature_boost_weight: float = 0.25
    # Parent-section expansion: show the LLM the full parent section while keeping
    # the precise child chunk for grounding/citation. Requires a re-ingest so
    # chunks carry parent_id; OFF by default to keep current behaviour stable.
    enable_parent_expansion: bool = False

    # ── Cross-encoder rerank (optional, stronger than the bi-encoder re-sort) ──
    # When enabled and a reranker endpoint is configured, the fused pool is
    # re-scored by a cross-encoder; otherwise the bi-encoder cosine rerank is used.
    # Falls back to the bi-encoder automatically if the endpoint is unreachable.
    enable_cross_encoder_rerank: bool = False
    reranker_base_url: str = ""        # e.g. http://localhost:8081  (POST /rerank)
    reranker_model: str = ""
    reranker_api_token: str = ""

    # ── History-aware retrieval & answer quality ────────────────────────────
    # Query contextualization: rewrite a follow-up ("what's its resolution?")
    # into a standalone search query using recent turns BEFORE retrieval, so the
    # embedding/keyword search targets the right entity. Gated — the LLM rewrite
    # fires only on detected follow-ups, so most turns pay nothing.
    enable_query_contextualization: bool = True
    contextualizer_max_history_chars: int = 1500
    # Embedding rerank of the fused vector+BM25 passages against the query
    # (cheap/local, same mechanism as graph_rerank). Pulls a wider candidate
    # pool, reranks, then keeps the most relevant few — less noise, more grounded.
    enable_passage_rerank: bool = True
    rerank_candidate_pool: int = 20
    top_k_passages: int = 6
    # Rolling conversation summary: when history exceeds the recent window,
    # fold the evicted turns into a running summary so older context is not lost.
    # Opt-in — adds an LLM call on overflow only.
    enable_conversation_summary: bool = False
    summary_keep_recent_turns: int = 6

    # LLM — Tabby ML (OpenAI-compatible — active backend for both LLM and embeddings)
    # Credentials must come from .env. Never hardcode the token here.
    tabby_base_url: str = "http://localhost:8080/v1"
    tabby_api_token: str = ""
    tabby_model: str = "Qwen2-1.5B-Instruct"

    # ── Knowledge-graph extraction LLM ──────────────────────────────────────
    # extraction_backend selects HOW triples are mined from each chunk:
    #   "llm"   — schema-guided LLM extraction (richest graph)
    #   "spacy" — spaCy SVO dependency parse (offline, no LLM needed)
    #   "auto"  — use the LLM when the endpoint is reachable, else fall back to spaCy
    extraction_backend: str = "auto"
    # SWITCH THE EXTRACTION MODEL FROM .env WITHOUT TOUCHING CODE.
    # Set TABBY_EXTRACTION_MODEL to any model Tabby ML is serving (e.g. a larger,
    # more accurate model than the chat model). Empty → reuse TABBY_MODEL.
    tabby_extraction_model: str = Field(
        default="",
        validation_alias=AliasChoices("tabby_extraction_model", "extraction_model"),
    )
    # Endpoint/credentials for the extraction LLM. Default to the shared TABBY_*
    # values so a single Tabby ML instance needs no extra configuration. Override
    # with EXTRACTION_LLM_* only to point extraction at a separate server.
    extraction_llm_base_url: str = Field(
        default="http://localhost:8080/v1",
        validation_alias=AliasChoices("extraction_llm_base_url", "tabby_base_url"),
    )
    extraction_llm_api_token: str = Field(
        default="",
        validation_alias=AliasChoices("extraction_llm_api_token", "tabby_api_token"),
    )
    extraction_temperature: float = 0.0
    extraction_max_tokens: int = 2048

    # LLM generation settings for chat (shared by all get_llm() callers)
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    # ── LLM resilience (P1-5) ───────────────────────────────────────────────
    # Hard request timeout and bounded retries so a slow/hung Tabby endpoint can
    # never stall a request thread indefinitely. A process-wide concurrency cap
    # provides backpressure in front of the single shared LLM endpoint (chat +
    # extraction + contextualization + summarization all share it).
    llm_request_timeout: float = 60.0
    llm_max_retries: int = 2
    llm_max_concurrency: int = 8
    # Skip chunks longer than this many characters in a single LLM call (they are
    # already bounded by CHUNK_SIZE, but this guards against pathological inputs).
    extraction_max_chars: int = 6000

    # ── Reasoning-aware retrieval & iterative answering ─────────────────────
    # Query decomposition: split a complex question into sub-questions before
    # retrieval (Phase 6). Extra LLM call — off by default to keep latency low.
    enable_query_decomposition: bool = False
    max_subquestions: int = 4
    # Embedding rerank of graph paths against the question (Phase 6). Cheap/local.
    graph_rerank: bool = True
    top_k_paths: int = 8
    # Iterative retrieve→reason→re-retrieve answer loop with a faithfulness
    # self-check (Phase 7). Off by default — multiple LLM calls per question.
    enable_iterative_reasoning: bool = False
    max_reasoning_iterations: int = 3
    enable_faithfulness_check: bool = True
    # Community summaries (Phase 6 GraphRAG global view). Build offline with
    # `python main.py build-communities`; used at query time when present.
    enable_community_summaries: bool = False
    community_collection: str = "graph_communities"
    max_communities: int = 50
    community_min_degree: int = 3

    def extraction_model_name(self) -> str:
        """Model used for KG extraction — TABBY_EXTRACTION_MODEL or fallback to TABBY_MODEL."""
        return self.tabby_extraction_model or self.tabby_model

    # ── Embeddings — bge-large via Ollama ──────────────────────────────────
    # Endpoint (host:port only) and model come from .env; never hardcoded.
    # The /api/embeddings path is appended automatically by OllamaEmbedder.
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "bge-large"
    # ── Embedding throughput (P0-1) ─────────────────────────────────────────
    # Native batch endpoint: Ollama's /api/embed accepts an array of inputs and
    # returns all vectors in ONE round-trip. embed_documents() uses it to collapse
    # N sequential HTTP calls (8 attack phrases, 20 rerank passages, …) into one.
    # Falls back automatically to the legacy per-item /api/embeddings on any error
    # (older Ollama builds), so this is safe to leave on.
    ollama_use_native_batch: bool = True
    ollama_embed_batch_path: str = "/api/embed"
    ollama_embed_batch_size: int = 64          # split very large batches defensively
    embed_timeout_seconds: int = 120
    # Process-level LRU cache of QUERY embeddings. The same query is embedded
    # several times per request (injection check, scope gate, vector search,
    # passage rerank, graph rerank) — caching makes all but the first free.
    # Set to 0 to disable. Document embeddings are never cached (unbounded text).
    embed_query_cache_size: int = 512
    # bge-style asymmetric retrieval: the QUERY is embedded with this instruction
    # prefix while passages stay bare, which measurably improves recall. Applied
    # only in embed_query. Set to "" to disable (e.g. for a symmetric model).
    embed_query_instruction: str = "Represent this sentence for searching relevant passages: "

    # System prompt file path (change this to reconfigure LLM behaviour)
    system_prompt_path: str = "./prompts/system_prompt.txt"

    def source_folders(self) -> list[Path]:
        return [Path(self.downloads_dir), Path(self.atlases_dir)]


settings = Settings()
