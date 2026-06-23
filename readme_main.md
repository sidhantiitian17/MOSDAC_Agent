# MOSDAC GraphRAG Agent — The Complete Guide (`readme_main.md`)

> **New here? Read this file top to bottom.** It explains *what* this project is, *why*
> each piece exists, and *how data flows end-to-end* from a raw PDF all the way to a
> grounded answer in a chat widget. It assumes **zero prior knowledge** of the codebase.
> When you want to *install and run* it, jump to **[install.md](install.md)**.
> When you want to understand *one folder in depth*, open the `README.md` inside that
> folder — every code directory has its own.

---

## Table of Contents

1. [What is this project? (plain English)](#1-what-is-this-project-plain-english)
2. [The core idea: RAG + Knowledge Graph + Guardrails](#2-the-core-idea)
3. [The 30,000-foot architecture](#3-the-30000-foot-architecture)
4. [The technology stack & why each piece was chosen](#4-the-technology-stack)
5. [The two pipelines: Ingestion vs. Query](#5-the-two-pipelines)
6. [Pipeline 1 — Ingestion (offline, builds the knowledge)](#6-pipeline-1--ingestion)
7. [Pipeline 2 — Query / Answer (online, serves users)](#7-pipeline-2--query--answer)
8. [The Guardrail system (security & anti-hallucination)](#8-the-guardrail-system)
9. [Feature-by-feature reference](#9-feature-by-feature-reference)
10. [The HTTP API surface](#10-the-http-api-surface)
11. [The front-end chat widget & SSO](#11-the-front-end-chat-widget--sso)
12. [Configuration model (everything via `.env`)](#12-configuration-model)
13. [Repository map (where everything lives)](#13-repository-map)
14. [How the modules depend on each other](#14-how-the-modules-depend-on-each-other)
15. [Testing & evaluation](#15-testing--evaluation)
16. [Observability, production hardening & deployment](#16-observability-production-hardening--deployment)
17. [Glossary](#17-glossary)
18. [Where to go next](#18-where-to-go-next)

---

## 1. What is this project? (plain English)

**MOSDAC GraphRAG Agent** is a **chatbot that answers questions about ISRO/MOSDAC
satellites, meteorology, and oceanography** — and answers them *only* from a curated
set of official documents (scientific PDFs, the MOSDAC website, atlases, Drupal CMS
content). It will **not** invent facts and **refuses** anything off-topic.

MOSDAC = **M**eteorological & **O**ceanographic **S**atellite **D**ata **A**rchival
**C**entre, run by ISRO's Space Applications Centre (SAC).

Why is this hard, and why not just use ChatGPT?

- A government science portal cannot tolerate a chatbot that **hallucinates**
  (makes up satellite specs, resolutions, formulas, dates).
- It must run **fully offline / air-gapped** — no data may leave ISRO's network, and
  there is **no internet at runtime**. So no OpenAI/Anthropic cloud APIs.
- Answers must be **traceable to a source document** (citations), and the system must
  be **safe** against prompt injection, PII leakage, and abuse.

This project solves all of that with a technique called **Graph RAG** wrapped in a
**deterministic guardrail pipeline**, served behind a hardened **FastAPI** gateway,
with a drop-in **browser chat widget**.

---

## 2. The core idea

There are three big ideas layered on top of each other. Understanding these three
sentences is 80% of understanding the whole repo.

### 2.1 RAG — Retrieval-Augmented Generation

A plain LLM only knows what was in its training data. **RAG** fixes this: before the
LLM answers, we **retrieve** the most relevant snippets from *our own* documents and
paste them into the prompt as context. The LLM then answers **from the provided text**
instead of from memory. That makes answers current, domain-specific, and auditable.

> Analogy: it's an open-book exam. The LLM is the student; retrieval hands it the exact
> pages it needs; the system prompt says "answer **only** from these pages."

### 2.2 Graph RAG — RAG + a Knowledge Graph

Plain RAG retrieves *flat text passages*. That's great for "what does this paragraph
say" but weak for "how is INSAT-3D related to its Imager payload, and what is that
payload's resolution?" — questions that span **relationships** between entities.

So we also build a **Knowledge Graph (KG)** in **Neo4j**: a network of nodes
(satellites, payloads, instruments, measurements) connected by typed relationships
(`INSAT-3D —[HAS_PAYLOAD]→ Imager —[HAS_RESOLUTION]→ 1 km`). At query time we retrieve
**both**: relevant text passages (vector + keyword search) **and** the relevant
sub-graph (multi-hop graph traversal). The LLM gets the best of both worlds.

### 2.3 Guardrails — make it safe enough to deploy publicly

Around the RAG core sits a **defense-in-depth guardrail pipeline** with four checkpoints
(named **L1 / L2 / L4 / L5** after the layer they protect):

- **L1 Input guard** — runs *before* spending any compute: normalize text, block
  prompt-injection/jailbreaks, redact PII, refuse off-topic questions.
- **L2 Retrieval/grounding gate** — after retrieval: is there *enough relevant
  evidence* to answer? If not, refuse with "I don't have that information."
- **L4 Output guard** — after the LLM: scrub secrets, verify citations, strip/refuse
  **ungrounded** numbers and sentences, redact PII, block toxicity.
- **L5 Audit** — log every request (PII-safe) + emit metrics, track abuse.

The guardrails are **deterministic** (rules + embeddings, not "ask another LLM to be
nice"), so their behaviour is predictable and testable — a hard requirement for a
Government of India deployment.

---

## 3. The 30,000-foot architecture

```
                        ┌──────────────────────────────────────────────┐
   Browser widget  ───► │   FastAPI gateway  (chat_api/)                │
   (static/*.js)        │   CORS · rate-limit · auth · security headers │
                        │   sessions · SSE streaming · /metrics         │
                        └───────────────┬──────────────────────────────┘
                                        │ calls
                                        ▼
                        ┌──────────────────────────────────────────────┐
                        │   ChatService  (chat_api/service.py)          │
                        │   orchestrates one turn end-to-end            │
                        └───┬───────────────┬───────────────┬──────────┘
                            │               │               │
              L1/L2/L4/L5   │      retrieve │      generate │
                            ▼               ▼               ▼
                    ┌──────────────┐ ┌─────────────┐ ┌──────────────┐
                    │ guardrails/  │ │ graph_rag/  │ │ graph_rag/   │
                    │ (security)   │ │ retrieval/  │ │ chain/+llm/  │
                    └──────────────┘ └──────┬──────┘ └──────┬───────┘
                                            │               │
                       ┌────────────────────┼───────────────┼───────────────┐
                       ▼                    ▼               ▼               ▼
                ┌────────────┐      ┌────────────┐  ┌────────────┐  ┌────────────┐
                │ ChromaDB   │      │  Neo4j     │  │  Ollama    │  │  Tabby ML  │
                │ (vectors)  │      │  (graph)   │  │ (embeddings│  │  (the LLM) │
                │ in-process │      │ container  │  │  bge-large)│  │  OpenAI-API│
                └────────────┘      └────────────┘  └────────────┘  └────────────┘
                       ▲                    ▲
                       └──────── populated by ────────┐
                                                      │
                        ┌─────────────────────────────┴────────────────┐
                        │  Ingestion pipeline (graph_rag/ingestion/)    │
                        │  PDFs · HTML · Office · images · Drupal       │
                        └──────────────────────────────────────────────┘
```

**Four external services** (everything else is in-process Python):

| Service | What it does | Default endpoint | Where it runs |
|---------|--------------|------------------|---------------|
| **Tabby ML** | The chat LLM **and** the KG-extraction LLM. OpenAI-compatible HTTP API. | `http://localhost:8080/v1` | Separate container / host process |
| **Ollama** | Serves the **`bge-large`** embedding model over HTTP (turns text into vectors). | `http://localhost:11434` | Host process |
| **Neo4j 5.18** | The **Knowledge Graph** database (Bolt protocol). | `bolt://localhost:7687` | Docker container |
| **Redis** | Persistent session store for multi-replica production. | `redis://redis:6379/0` | Docker container |

> **Key design choice — no model weights in-process.** Both the LLM and the embedder
> are *HTTP services*, never loaded into the Python process. That keeps the app
> lightweight (no `torch`, no `sentence-transformers`) and lets you swap models by
> changing one line in `.env`. **ChromaDB is the only datastore that lives inside the
> Python process** (it persists to the `./chroma_db` folder).

---

## 4. The technology stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Language | **Python 3.11+** | Ecosystem for RAG/NLP. |
| LLM orchestration | **LangChain 0.3** (pinned) | LCEL chains, message types, splitters. Pinned to 0.3 — **do not** bump to 0.4/1.x (breaks `langchain-chroma`/`langchain-neo4j`). |
| LLM serving | **Tabby ML** (OpenAI-compatible) | Local, offline, no cloud. Swappable via `.env`. |
| Embeddings | **Ollama** + **`bge-large`** | Local HTTP embeddings; bge models support an asymmetric query prefix for better recall. |
| Vector DB | **ChromaDB** | In-process, persists to disk, no server to run. |
| Graph DB | **Neo4j 5.18** | Mature property-graph DB with Cypher + fulltext index. |
| PDF parsing | **Docling** (primary) + pypdf/PyMuPDF/OCR (fallback) | Docling extracts Markdown structure, **LaTeX formulas**, and tables — critical for scientific docs. |
| OCR | **Tesseract + Poppler** | For scanned/image-only atlas pages. |
| NLP | **spaCy** (`en_core_web_sm`) | Offline entity/relation extraction fallback. |
| Web framework | **FastAPI** + **Uvicorn** | Async HTTP gateway, app-factory pattern. |
| Keyword search | **rank-bm25** | Exact-term matching, fused with vectors via RRF. |
| Rate limiting | **slowapi** | Per-IP abuse/DoS control (fails closed). |
| Auth (optional) | **PyJWT[crypto]** + **Keycloak/OIDC** | Per-user login via JWKS verification. |
| Sessions | **Redis** (optional) | Multi-replica persistent sessions. |
| Conversation history | **SQLite** (default) / **PostgreSQL** (multi-replica) | Per-user chat history. |
| Metrics | **prometheus-client** | `/metrics` endpoint. |
| Evaluation | **RAGAS 0.2** (pinned) | Production go/no-go quality gate. |
| Config | **pydantic-settings** | Every knob is an env var; nothing hardcoded. |

---

## 5. The two pipelines

The system has exactly **two end-to-end flows**. Keep them mentally separate:

| | **Ingestion pipeline** | **Query / Answer pipeline** |
|---|---|---|
| When | Offline, run by an operator | Online, per user request |
| Trigger | `python main.py ingest` | `POST /chat` (or `python main.py chat`) |
| Input | Files + Drupal content | A user's question |
| Output | Populated ChromaDB + Neo4j | A grounded answer + citations |
| Owner | [graph_rag/ingestion/pipeline.py](graph_rag/ingestion/pipeline.py) | [chat_api/service.py](chat_api/service.py) |

You **must run ingestion first** (to fill the stores) before the query pipeline can
answer anything — otherwise the grounding gate (L2) correctly refuses with "no info."

---

## 6. Pipeline 1 — Ingestion

**Goal:** turn a pile of documents into two searchable stores — a **vector store**
(ChromaDB) and a **knowledge graph** (Neo4j).

**Command:** `python main.py ingest` (orchestrated by
[graph_rag/ingestion/pipeline.py](graph_rag/ingestion/pipeline.py); CLI wiring in
[main.py](main.py) `cmd_ingest`).

```
 SOURCES                         downloads/  (HTML, saved web pages)
   │                             atlases_pdfs/ (scientific PDFs)
   │                             Drupal JSON:API (optional, if DRUPAL_JSONAPI_URL set)
   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 1 · DISCOVER + LOAD            graph_rag/ingestion/loader.py + formats.py │
│   • A SHA-256 "manifest" skips files already ingested (incremental).           │
│   • Docling parses each PDF → Markdown + LaTeX math ($$...$$) + tables + OCR.   │
│   • Office/HTML/image formats handled by a pluggable format registry.          │
│   • A QUALITY GATE drops blank scans / OCR gibberish / repetitive junk.        │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ STEP 2 · PREPROCESS + SPLIT     graph_rag/preprocessing/ + ingestion/splitter  │
│   • Clean the Markdown, then split into overlapping ~800-char chunks.          │
│   • Splitting is header-aware and MATH/TABLE-SAFE (never cuts a formula).       │
│   • Each chunk gets a STABLE chunk_id (so re-ingesting is idempotent).          │
│   • Each chunk is tagged with text features: has_formula, numeric_density, …   │
└──────────────────┬──────────────────────────────────┬─────────────────────────┘
                   ▼                                   ▼
┌──────────────────────────────────┐  ┌───────────────────────────────────────────┐
│ STEP 3 · EMBED → ChromaDB        │  │ STEP 4 · EXTRACT → Neo4j                  │
│   graph_rag/embeddings/ +        │  │   graph_rag/knowledge_graph/              │
│   vector_store/                  │  │                                           │
│   • bge-large turns each chunk   │  │   • LLM (or spaCy) mines typed triples:   │
│     into a vector (via Ollama).  │  │     (subject)-[REL]->(object)             │
│   • Stored in ChromaDB, deduped  │  │   • Regex quantity parser finds specs     │
│     by chunk_id.                 │  │     ("36 m resolution") → Measurement     │
│   • Text features stored as      │  │     nodes (comparable, structured).       │
│     metadata for feature-boost.  │  │   • Chunk nodes store provenance so every │
│                                  │  │     fact links back to its source chunk.  │
│                                  │  │   • Entity RESOLVER collapses variants    │
│                                  │  │     ("INSAT 3D"/"INSAT-3D") into one node.│
└──────────────────────────────────┘  └───────────────────────────────────────────┘
                   └──────────────────┬───────────────────┘
                                      ▼
              STEP 5 · RECORD MANIFEST (only after a clean, error-free run)
                      → so a crashed run is safely retried, and the next run
                        skips everything already done.
```

### Important ingestion behaviours

- **Incremental & crash-safe.** The content-hash manifest (`ingest_manifest.json`)
  records the SHA-256 of every successfully-ingested file. Re-running `ingest` skips
  unchanged files. `--force` re-ingests everything. The manifest is written **only**
  after a completely clean run, so a partial/crashed run is safe to retry. Both stores
  are *also* idempotent: ChromaDB dedups by `chunk_id`, Neo4j `MERGE`s on canonical
  keys — so even a double-run can't create duplicates.
- **Drupal delta-sync.** If `DRUPAL_JSONAPI_URL` is set, [drupal_ingest.py](drupal_ingest.py)
  pulls CMS articles, hashes `title+body`, skips unchanged nodes, and feeds the rest
  through the **identical** KG/vector code path (`IngestionPipeline.run_on_documents`).
- **Flags:** `--force`, `--skip-files`, `--skip-drupal`, `--skip-vector` (KG only),
  `--skip-graph` (vectors only).
- **Air-gapped parsing.** Docling's ML models (layout, TableFormer, CodeFormula) are
  pre-downloaded at Docker build time and loaded from local disk with
  `HF_HUB_OFFLINE=1` — **zero** network calls at parse time.

> Deep dive: [graph_rag/ingestion/README.md](graph_rag/ingestion/README.md),
> [graph_rag/preprocessing/README.md](graph_rag/preprocessing/README.md),
> [graph_rag/knowledge_graph/README.md](graph_rag/knowledge_graph/README.md).

---

## 7. Pipeline 2 — Query / Answer

**Goal:** answer one user question, grounded in the stores, safely.

**Entry points:** `POST /chat` / `POST /chat/stream` (HTTP) or `python main.py chat`
(REPL). Orchestrated by [chat_api/service.py](chat_api/service.py) `ChatService.chat()`.

```
USER MESSAGE
   │
   ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ L1 · INPUT GUARD          guardrails/input/   (runs BEFORE any spend)          │
│   normalize → injection check → PII redact → scope gate                        │
│   ──► off-topic / injection / abuse  ⇒  REFUSE immediately (no retrieval/LLM)  │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ QUERY CONTEXTUALIZATION    graph_rag/retrieval/query_contextualizer.py         │
│   Rewrites a follow-up ("what's its resolution?") into a standalone query      │
│   using recent history — only fires on detected follow-ups (cheap otherwise).  │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ HYBRID RETRIEVAL           graph_rag/retrieval/hybrid_retriever.py             │
│   • Vector search (semantic, bge-large)   ┐                                    │
│   • BM25 search (exact keywords)          ├─► fuse with Reciprocal Rank Fusion │
│   • Graph search (Neo4j 2-hop subgraph)   ┘   (RRF)                            │
│   • Feature boost for numeric/formula queries                                  │
│   • Passage rerank (cross-encoder or local bi-encoder cosine)                  │
│   • Exact-formula verbatim injection (math notation queries)                   │
│   • Retrieved CONTEXT IS SANITIZED (indirect-injection defence)                │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ L2 · GROUNDING GATE        guardrails/retrieval/grounding_gate.py              │
│   Is there a relevant-enough hit + enough supporting passages?                 │
│   Builds a CITATION REGISTRY ([S1],[S2]…) from the allowed source chunks.      │
│   ──► not groundable  ⇒  REFUSE "I don't have that information."               │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ LLM GENERATION             graph_rag/chain/ + graph_rag/llm/tabby_client.py    │
│   Assemble prompt (system + KG context + passages + history + question)        │
│   → call Tabby ML, concurrency-throttled, optional SSE token streaming.        │
│   (Multimodal path when a screenshot is attached — same L2 gate applies.)      │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ L4 · OUTPUT GUARD          guardrails/output/                                  │
│   leakage scrub → citation verify → grounding enforcement                      │
│   (flag | strip | refuse ungrounded numbers/sentences) → PII redact → toxicity │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ L5 · AUDIT + METRICS       guardrails/audit/ + observability/                  │
│   PII-safe structured log of the turn + Prometheus counters/latency            │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                     ▼
ANSWER + citations + {grounded, refused}
(only the PII-redacted user turn is stored in the session / conversation history)
```

### Why this order matters

- **Refuse cheap, refuse early.** L1 runs before retrieval; L2 runs before the LLM.
  An off-topic question, a jailbreak, or an unanswerable query is rejected **without**
  paying for the expensive LLM call.
- **Two layers of anti-hallucination.** L2 makes sure there's evidence *before* the LLM
  speaks; L4 checks the LLM actually *used* that evidence (ungrounded numbers/sentences
  are stripped or refused per `GUARD_GROUNDING_ACTION`).
- **Streaming is still safe.** `/chat/stream` streams tokens for snappy UX, but emits a
  single authoritative `final` event whose answer has already passed L4. Clients must
  treat `final`, not the raw tokens, as the answer.
- **Fails observably.** If the embedder is down, embedder-dependent guards either
  degrade (fail-open) or refuse (fail-closed via `GUARD_EMBEDDER_REQUIRED=true`) — but
  always emit a `guardrail_degraded_total` metric. Never a silent failure.

> Deep dive: [graph_rag/retrieval/README.md](graph_rag/retrieval/README.md),
> [graph_rag/chain/README.md](graph_rag/chain/README.md),
> [chat_api/README.md](chat_api/README.md), [guardrails/README.md](guardrails/README.md).

---

## 8. The Guardrail system

The guardrails live in [guardrails/](guardrails/) and are orchestrated by a single
stateless singleton, [guardrails/pipeline.py](guardrails/pipeline.py)
(`GuardrailPipeline`). Every control is an **env flag**, fail-closed by default.

| Layer | Module | Checks |
|-------|--------|--------|
| **L1 Input** | [guardrails/input/](guardrails/input/) | `normalize` (Unicode NFKC, control-char strip, length cap, charset), `injection` (regex + embedding-similarity to a known-attack corpus), `pii` (redaction), `scope` (embedding-centroid on-topic gate). |
| **L2 Retrieval** | [guardrails/retrieval/](guardrails/retrieval/) | `grounding_gate` (relevance floor + min supporting passages + citation registry), `source_allowlist` (only chunks from manifest-ingested files), `cypher_safe` (sanitize entity names before Neo4j). |
| **L4 Output** | [guardrails/output/](guardrails/output/) | `leakage` (system-prompt/secret scrub), `citation_verify` ([Sx] IDs must exist), `grounding_check` (ungrounded numbers & sentences), `pii_out` (redact), `safety` (toxicity). |
| **L5 Audit** | [guardrails/audit/](guardrails/audit/) | `logger` (PII-safe structured audit, hashed session ids, rotating file sink), `abuse` (per-session abuse counter + temporary lockout). |

Cross-cutting config: [guardrails/config.py](guardrails/config.py) (`GUARD_*` env vars),
decision types in [guardrails/decisions.py](guardrails/decisions.py), canonical refusal
strings in [guardrails/templates.py](guardrails/templates.py).

> Deep dive: [guardrails/README.md](guardrails/README.md) and the per-subfolder READMEs.

---

## 9. Feature-by-feature reference

A catalogue of every notable capability and where it lives. Most are toggled in `.env`.

### Ingestion & parsing
- **Multi-format ingestion** — PDF, HTML, `.docx/.xlsx/.pptx/.csv/.adoc`, images
  (`.png/.jpg/.tiff/.bmp/.webp/.gif` via OCR). Format registry:
  [graph_rag/ingestion/formats.py](graph_rag/ingestion/formats.py). Toggles:
  `INGEST_ENABLE_OFFICE`, `INGEST_ENABLE_IMAGES`.
- **Docling structured PDF parsing** — Markdown + **LaTeX formulas** + tables + OCR:
  [graph_rag/ingestion/docling_parser.py](graph_rag/ingestion/docling_parser.py).
  Toggles: `USE_DOCLING`, `DOCLING_DO_FORMULA_ENRICHMENT`, `DOCLING_DO_TABLE_STRUCTURE`,
  `DOCLING_FORCE_FULL_PAGE_OCR_DIRS`.
- **Quality gate** — drops near-empty / gibberish / repetitive extractions:
  [graph_rag/preprocessing/quality.py](graph_rag/preprocessing/quality.py). Knobs:
  `INGEST_MIN_CHARS`, `INGEST_MIN_ALNUM_RATIO`, `INGEST_MIN_UNIQUE_TOKENS`, etc.
- **Math/table-safe chunking** — never splits a formula or table:
  [graph_rag/ingestion/splitter.py](graph_rag/ingestion/splitter.py),
  [graph_rag/preprocessing/preprocessor.py](graph_rag/preprocessing/preprocessor.py).
- **Incremental + crash-safe manifest** —
  [graph_rag/ingestion/manifest.py](graph_rag/ingestion/manifest.py).
- **Drupal delta-sync** — [drupal_ingest.py](drupal_ingest.py).

### Knowledge graph
- **LLM triple extraction (schema-guided)** —
  [graph_rag/knowledge_graph/llm_extractor.py](graph_rag/knowledge_graph/llm_extractor.py);
  spaCy fallback in [extractor.py](graph_rag/knowledge_graph/extractor.py). Backend:
  `EXTRACTION_BACKEND` = `llm`/`spacy`/`auto`.
- **Domain ontology (controlled vocabulary)** —
  [graph_rag/knowledge_graph/ontology.py](graph_rag/knowledge_graph/ontology.py).
- **Quantity parser → Measurement nodes** — turns "36 m resolution" into structured,
  comparable facts: [quantity_parser.py](graph_rag/knowledge_graph/quantity_parser.py).
- **Entity resolution / canonicalization** — collapses surface variants:
  [resolver.py](graph_rag/knowledge_graph/resolver.py).
- **Provenance** — every fact links back to its source Chunk node:
  [neo4j_store.py](graph_rag/knowledge_graph/neo4j_store.py).
- **GraphRAG community summaries** (global/overview questions) —
  [community.py](graph_rag/knowledge_graph/community.py); build with
  `python main.py build-communities`. Toggle: `ENABLE_COMMUNITY_SUMMARIES`.

### Retrieval
- **Hybrid retrieval (vector + BM25 + graph)** with **Reciprocal Rank Fusion** —
  [graph_rag/retrieval/hybrid_retriever.py](graph_rag/retrieval/hybrid_retriever.py).
- **Passage reranking** — local bi-encoder cosine or external cross-encoder:
  [rerankers.py](graph_rag/retrieval/rerankers.py). Toggles: `ENABLE_PASSAGE_RERANK`,
  `ENABLE_CROSS_ENCODER_RERANK`.
- **Feature boost** for numeric/formula queries — `ENABLE_FEATURE_BOOST`.
- **Exact-formula verbatim injection** — `ENABLE_EXACT_FORMULA_MATCH`.
- **History-aware query contextualization** —
  [query_contextualizer.py](graph_rag/retrieval/query_contextualizer.py).
  Toggle: `ENABLE_QUERY_CONTEXTUALIZATION`.
- **Query decomposition** (multi-hop) — [query_planner.py](graph_rag/retrieval/query_planner.py).
  Toggle: `ENABLE_QUERY_DECOMPOSITION`.
- **Iterative retrieve→reason→re-retrieve** with a faithfulness self-check —
  [graph_rag/chain/iterative_chain.py](graph_rag/chain/iterative_chain.py).
  Toggle: `ENABLE_ITERATIVE_REASONING`.

### Chat & sessions
- **Stateful REPL chatbot** — [graph_rag/chat/chatbot.py](graph_rag/chat/chatbot.py).
- **Rolling conversation summary** — [graph_rag/chat/summarizer.py](graph_rag/chat/summarizer.py).
  Toggle: `ENABLE_CONVERSATION_SUMMARY`.
- **Per-user persisted history** (SQLite / Postgres) — [chat_api/db/](chat_api/db/).
- **Auto-generated conversation titles** — [chat_api/titler.py](chat_api/titler.py).
- **Answer cache** for repeated FAQs — [chat_api/answer_cache.py](chat_api/answer_cache.py).
  Toggle: `CHAT_API_ENABLE_ANSWER_CACHE`.
- **SSE streaming** — `POST /chat/stream`.
- **Multimodal (screenshot) Q&A** — `CHAT_API_ENABLE_SCREENSHOT` + a vision model.

### Security & ops
- **Full guardrail pipeline** — [guardrails/](guardrails/).
- **CORS allowlist, security headers, body-size cap, per-IP rate limiting, API-key /
  admin-token auth, UUID session validation** — [chat_api/main.py](chat_api/main.py).
- **Keycloak/OIDC SSO** (JWKS verification, config-driven claim mapping) —
  [chat_api/auth.py](chat_api/auth.py).
- **Readiness probes** shared by CLI + `/ready` — [graph_rag/health.py](graph_rag/health.py).
- **Prometheus metrics** — [observability/](observability/).
- **RAGAS production gate** — [graph_rag/eval/](graph_rag/eval/).

---

## 10. The HTTP API surface

Defined in [chat_api/routes.py](chat_api/routes.py); request/response shapes in
[chat_api/models.py](chat_api/models.py).

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/health` | none | Liveness — cheap, never touches downstream deps. |
| `GET` | `/ready` | none | Readiness — probes embedder/Chroma/Neo4j; `503` when not ready. |
| `GET` | `/config` | none | Widget config (title, bot name, screenshot toggle, SSO coords). |
| `GET` | `/me` | bearer | Current user's profile (when SSO enabled). |
| `GET` | `/metrics` | admin token | Prometheus metrics (when `CHAT_API_ENABLE_METRICS=true`). |
| `POST` | `/chat` | optional API key / bearer | Main Q&A → `answer`, `citations`, `grounded`, `refused`. |
| `POST` | `/chat/stream` | optional | SSE: `token` events then one authoritative `final` event (post-L4). |
| `GET` | `/conversations` | bearer | List the current user's conversations. |
| `GET` | `/conversations/{id}/messages` | bearer | Load one conversation (ownership-checked). |
| `DELETE` | `/conversations/{id}` | bearer | Delete a conversation. |
| `DELETE` | `/chat/{session_id}` | optional API key | Clear an anonymous session's history. |
| `POST` | `/reload` | admin token | Hot-reload BM25 index / caches after a re-ingest. |

`/docs` (Swagger UI) is self-hosted from vendored assets so it works air-gapped.

---

## 11. The front-end chat widget & SSO

The browser side lives in [static/](static/):

- **[static/graph-rag-chat-widget.js](static/graph-rag-chat-widget.js)** — the generic,
  domain-agnostic chat widget. Embed it with a single `<script>` tag. It renders inside
  a **Shadow DOM** (so portal CSS can't break it), speaks to the API at a configurable
  base URL, renders **Markdown + LaTeX (KaTeX)**, and uses **SSE streaming** so the user
  sees tokens as they generate.
- **[static/mosdac-chat-widget.js](static/mosdac-chat-widget.js)** — a thin MOSDAC-branded
  shim that sets ISRO/MOSDAC defaults then loads the generic widget. Re-branding is
  config-only.
- **[static/sso-demo.html](static/sso-demo.html)** — a standalone Keycloak SSO test
  harness for verifying the login → token → authenticated `/chat` flow.
- **`static/vendor/`** — vendored **KaTeX** (math rendering) and **Swagger UI** so the
  widget and `/docs` need **no CDN** (air-gapped).

The widget is served by the API itself at `/static/...` (mounted in
[chat_api/main.py](chat_api/main.py)), so a single file is the source of truth across all
portals. Drupal/nginx integration and per-domain snippets live in
[deployments/](deployments/).

> Deep dive: [static/README.md](static/README.md).

---

## 12. Configuration model

**Everything is configured through `.env`** — there are no hardcoded credentials or
behaviours in source. Three pydantic-settings classes load it:

| Class | Prefix | File | Governs |
|-------|--------|------|---------|
| `Settings` | *(none)* | [graph_rag/config.py](graph_rag/config.py) | RAG core: LLM, embeddings, Neo4j, Chroma, ingestion, chunking, retrieval. |
| `ChatAPISettings` | `CHAT_API_` | [chat_api/config.py](chat_api/config.py) | Gateway: CORS, sessions, auth, rate limit, screenshots, conversation store. |
| `GuardrailSettings` | `GUARD_` | [guardrails/config.py](guardrails/config.py) | Every security control. |

The authoritative, fully-commented reference is **[.env.example](.env.example)** — copy
it to `.env` and fill it in. Notable groups:

- **LLM:** `TABBY_BASE_URL`, `TABBY_API_TOKEN`, `TABBY_MODEL`, `LLM_TEMPERATURE`,
  `LLM_MAX_TOKENS`, `LLM_REQUEST_TIMEOUT`, `LLM_MAX_RETRIES`, `LLM_MAX_CONCURRENCY`.
- **Embeddings:** `OLLAMA_BASE_URL`, `OLLAMA_EMBEDDING_MODEL`, `EMBED_QUERY_INSTRUCTION`.
- **KG extraction:** `EXTRACTION_BACKEND` (`llm`/`spacy`/`auto`), `TABBY_EXTRACTION_MODEL`.
- **Neo4j / Chroma:** `NEO4J_URI/USERNAME/PASSWORD`, `CHROMA_PERSIST_DIR`, `CHROMA_COLLECTION`.
- **Retrieval:** `TOP_K_VECTOR/BM25/GRAPH`, `GRAPH_DEPTH`, `HYBRID_RRF_K`,
  `ENABLE_PASSAGE_RERANK`, `ENABLE_FEATURE_BOOST`, `ENABLE_EXACT_FORMULA_MATCH`.
- **Guardrails:** `GUARD_GROUNDING_ACTION` (`flag`/`strip`/`refuse`), `GUARD_EMBEDDER_REQUIRED`,
  `GUARD_SCOPE_MIN_SIM`, `GUARD_RETRIEVAL_MIN_SCORE`, `GUARD_RATE_LIMIT_PER_MIN`.
- **Chat API:** `CHAT_API_ALLOWED_ORIGINS`, `CHAT_API_SESSION_BACKEND` (`memory`/`redis`),
  `CHAT_API_API_KEY`, `CHAT_API_ADMIN_TOKEN`, `CHAT_API_AUTH_ENABLED`, `CHAT_API_CONV_STORE`.
- **System prompt:** `SYSTEM_PROMPT_PATH` → [prompts/system_prompt.txt](prompts/system_prompt.txt)
  (edit the file to change behaviour — no restart needed; picked up next request).

---

## 13. Repository map

```text
MOSDAC_Agent/
├── main.py                     # CLI entry point: ingest / chat / test / eval / ragas-eval / build-communities
├── drupal_ingest.py            # Drupal JSON:API → Graph RAG ingestion (delta-sync)
├── test_main.py                # one-file end-to-end ingest smoke test (run inside the chat_api container)
│
├── chat_api/                   # ── FastAPI gateway ──────────────────────────────
│   ├── main.py                 #    app factory: middleware, CORS, rate-limit, static mount
│   ├── routes.py               #    HTTP endpoints
│   ├── service.py              #    ChatService — orchestrates one turn (L1→retrieve→L2→LLM→L4→L5)
│   ├── models.py               #    pydantic request/response models
│   ├── session.py              #    pluggable session store (memory / redis)
│   ├── auth.py                 #    Keycloak/OIDC JWT verification + claim adapter
│   ├── titler.py               #    short conversation-title generation
│   ├── answer_cache.py         #    optional FAQ answer cache
│   ├── config.py               #    ChatAPISettings (CHAT_API_* env)
│   └── db/                     #    per-user conversation persistence (sqlite / postgres)
│
├── graph_rag/                  # ── RAG core ─────────────────────────────────────
│   ├── config.py               #    Settings (reads .env) — the master knob board
│   ├── health.py               #    shared readiness probes (CLI `test` + /ready)
│   ├── text_features.py        #    symbol-aware tokenization + formula/table/number detection
│   ├── ingestion/              #    discover → load → split (+ format registry, quality gate, manifest)
│   ├── preprocessing/          #    Docling cleaning, header chunking, math safety
│   ├── embeddings/             #    Ollama bge-large HTTP embedder
│   ├── vector_store/           #    ChromaDB wrapper
│   ├── knowledge_graph/        #    extraction, quantity parsing, resolver, Neo4j store, communities
│   ├── retrieval/              #    vector + BM25 + graph + RRF fusion + rerank + contextualizer
│   ├── chain/                  #    LCEL RAG chain (prompt assembly + LLM) + iterative reasoner
│   ├── chat/                   #    stateful chatbot REPL + conversation summarizer
│   ├── llm/                    #    Tabby ML client (OpenAI-compatible)
│   └── eval/                   #    RAGAS production gate, custom metrics, scorecard, golden dataset
│
├── guardrails/                 # ── Security pipeline (L1 · L2 · L4 · L5) ─────────
│   ├── pipeline.py             #    GuardrailPipeline orchestrator (singleton)
│   ├── config.py               #    GuardrailSettings (GUARD_* env)
│   ├── decisions.py            #    Action / GuardDecision types
│   ├── templates.py            #    canonical refusal/redaction messages
│   ├── input/                  #    L1: normalize, injection, pii, scope
│   ├── retrieval/              #    L2: grounding_gate, source_allowlist, cypher_safe
│   ├── output/                 #    L4: leakage, citation_verify, grounding_check, pii_out, safety
│   └── audit/                  #    L5: logger, abuse
│
├── observability/              # Prometheus metrics facade (/metrics)
├── prompts/                    # system_prompt.txt (LLM behaviour, edit freely)
├── scripts/                    # build_kg.py, loadtest.py, helper scripts
├── static/                     # embeddable chat widget + vendored KaTeX/Swagger + SSO demo
├── deployments/                # per-domain env, nginx config, widget snippets
├── docs/                       # operational runbooks (offline setup, backup/restore, RAGAS)
├── tests/                      # pytest suite (+ tests/eval golden data, tests/guardrails corpora)
│
├── downloads/                  # ingestion source: saved HTML / web pages (data, git-ignored content)
├── atlases_pdfs/               # ingestion source: scientific PDFs
├── chroma_db/                  # ChromaDB persistence (generated)
├── neo4j_data/                 # Neo4j data bind-mount (generated)
│
├── docker-compose.yml          # Neo4j + Redis + chat_api stack
├── Dockerfile.api              # API image (Tesseract, Poppler, spaCy, baked Docling models)
├── docker-entrypoint.sh        # chowns runtime mounts then drops to non-root appuser
├── requirement.txt             # pip dependency set (mirrors pyproject [project.dependencies])
├── pyproject.toml              # project metadata + pinned dependency graph
├── .env.example                # the authoritative, fully-commented config template
├── README.md                   # the original "start here" doc (kept)
├── readme_main.md              # ← THIS FILE (the complete guide)
└── install.md                  # step-by-step install & run instructions
```

---

## 14. How the modules depend on each other

A simplified import graph (arrow = "imports / calls"). This is the mental model for
"if I change X, what else is affected?"

```
                         chat_api/routes.py
                                │
                                ▼
                         chat_api/service.py ────────────────► guardrails/pipeline.py
                          │        │        │                        │  │  │
              retriever   │        │ chain  │ llm                     L1 L2 L4/L5
                          ▼        ▼        ▼                        (input/ retrieval/
        graph_rag/retrieval/  graph_rag/chain/  graph_rag/llm/        output/ audit/)
                │                  │                │
   ┌────────────┼──────────┐      │                │
   ▼            ▼          ▼       ▼                ▼
 vector_     bm25_      graph_   (uses retriever  tabby_client ──► Tabby ML (HTTP)
 retriever   retriever  retriever  + llm + prompt)
   │            │          │
   ▼            ▼          ▼
 vector_store/ (BM25 reads   knowledge_graph/neo4j_store.py ──► Neo4j (Bolt)
 chroma_store   Chroma too)        ▲
   │                               │ (populated by)
   ▼                               │
 embeddings/ollama_embedder ──► Ollama (HTTP)   graph_rag/ingestion/pipeline.py
   ▲                                                  │ load→split→embed→extract
   │                                                  ▼
 EVERY layer that needs vectors          ingestion/loader · preprocessing · splitter
 (retrieval, guardrails scope/injection,            · knowledge_graph extraction
  resolver, rerankers, eval) calls get_embedder()
```

Key shared modules (imported nearly everywhere):

- **[graph_rag/config.py](graph_rag/config.py)** `settings` — the single source of truth
  for RAG-core config; imported by virtually every `graph_rag/` module.
- **[graph_rag/embeddings/](graph_rag/embeddings/)** `get_embedder()` — one embedder used
  by retrieval, rerankers, the resolver, community summaries, **and** the guardrails
  (scope gate + injection embedding tier).
- **[graph_rag/llm/tabby_client.py](graph_rag/llm/tabby_client.py)** `get_llm()` /
  `llm_slot()` — one LLM client + a process-wide concurrency semaphore shared by chat,
  KG extraction, contextualization, summarization, and titling.
- **[guardrails/config.py](guardrails/config.py)** — read by the pipeline and the rate
  limiter.
- **[observability/](observability/)** `inc()` / `observe()` — best-effort metrics called
  from the service and the guardrails; never allowed to break a request.

Full per-file dependency lists are in each folder's `README.md`.

---

## 15. Testing & evaluation

Two distinct things:

### Functional tests — `pytest`
44 test modules in [tests/](tests/) cover loaders, splitter, chunking, retrieval,
embeddings, KG extraction, resolver, ontology, guardrails, the chat API, conversation
repos, eval harness, and production hardening. Tests needing live Neo4j/Ollama/Tabby
auto-skip when those services are absent, so the suite runs green in CI without them.

```bash
pytest -q                                  # full suite
pytest tests/test_chat_api.py -v           # API layer
pytest tests/test_pipeline_security.py -v  # guardrails
```

### Quality evaluation — RAGAS production gate
A "no-mercy" go/no-go gate ([graph_rag/eval/](graph_rag/eval/)) that tries to *fail* the
system before real users do — faithfulness, answer relevancy, plus custom metrics
CE1–CE4 (numeric fidelity, formula fidelity, citation integrity, refusal confusion).

```bash
python main.py ragas-eval            # PROD config against tests/eval/golden/v1
python main.py ragas-eval --smoke    # cheaper subset for fast iteration / CI tripwire
python main.py eval                  # legacy cheap deterministic Phase-0 harness
```

The RAGAS **judge** must be a *stronger* model than the generator under test (never the
same one). Methodology & scorecard: [evaluation_plan.md](evaluation_plan.md).

> Deep dive: [tests/README.md](tests/README.md), [graph_rag/eval/README.md](graph_rag/eval/README.md).

---

## 16. Observability, production hardening & deployment

- **Metrics:** Prometheus at `GET /metrics` — request counts/latency, guardrail
  refusals, degradation, answer-cache hits. [observability/](observability/).
- **Security:** OWASP headers, CORS allowlist (no wildcards), per-IP rate limiting,
  body-size cap, optional API-key/admin-token auth, UUID session validation, non-root
  container. [chat_api/main.py](chat_api/main.py), [Dockerfile.api](Dockerfile.api).
- **Resilience:** LLM retries + concurrency cap, Neo4j connection pool, BM25 warm-up on
  boot + hot-reload, Redis-backed persistent sessions for multi-replica.
- **Air-gapped:** Docling models baked at build, `HF_HUB_OFFLINE=1`, self-hosted Swagger,
  vendored KaTeX. Walkthrough: [docs/start_offline.md](docs/start_offline.md).
- **Deployment:** [docker-compose.yml](docker-compose.yml) (Neo4j + Redis + API),
  per-domain config in [deployments/](deployments/), nginx in
  [deployments/nginx/mosdac.conf](deployments/nginx/mosdac.conf).
- **Production review & runbooks:** [production.md](production.md),
  [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).

---

## 17. Glossary

| Term | Meaning |
|------|---------|
| **RAG** | Retrieval-Augmented Generation — fetch relevant docs, then let the LLM answer from them. |
| **Graph RAG** | RAG that also retrieves from a knowledge graph, not just flat text. |
| **Embedding** | A list of numbers (vector) representing text meaning; similar text → nearby vectors. |
| **Vector store** | A database of embeddings supporting "find the most similar" search (ChromaDB here). |
| **Knowledge Graph (KG)** | Nodes (entities) + typed edges (relationships) stored in Neo4j. |
| **Chunk** | A small overlapping slice of a document; the unit we embed, store, and cite. |
| **BM25** | A classic keyword-ranking algorithm; complements semantic vector search. |
| **RRF** | Reciprocal Rank Fusion — merges several ranked result lists into one. |
| **Rerank** | Re-score candidate passages with a stronger model to push the best to the top. |
| **Grounding** | Whether an answer is actually supported by the retrieved evidence. |
| **Citation registry** | The map of `[S1],[S2]…` source IDs the LLM is allowed to cite. |
| **Guardrail** | A deterministic safety check (input/retrieval/output/audit). |
| **Prompt injection** | A malicious input that tries to override the system's instructions. |
| **Triple** | A `(subject, relation, object)` fact extracted into the KG. |
| **Measurement node** | A structured, comparable spec ("36 m", "0.65 µm") in the KG. |
| **Manifest** | The SHA-256 record of already-ingested files (enables incremental ingest). |
| **Tabby ML** | The local, OpenAI-compatible LLM server used for chat + extraction. |
| **Ollama** | The local HTTP server that produces `bge-large` embeddings. |
| **Keycloak / OIDC** | The SSO identity provider; the API verifies its JWTs via JWKS. |
| **L1/L2/L4/L5** | The four guardrail checkpoints (input / retrieval / output / audit). |

---

## 18. Where to go next

- **To install & run it:** → **[install.md](install.md)**
- **The original concise overview:** → [README.md](README.md)
- **To understand one folder:** open the `README.md` inside it — every code directory
  has a detailed one (e.g. [graph_rag/retrieval/README.md](graph_rag/retrieval/README.md),
  [guardrails/README.md](guardrails/README.md), [chat_api/README.md](chat_api/README.md)).
- **Air-gapped / ISRO on-prem setup:** [docs/start_offline.md](docs/start_offline.md)
- **Production readiness review:** [production.md](production.md)
- **Evaluation methodology:** [evaluation_plan.md](evaluation_plan.md)
- **The full config reference:** [.env.example](.env.example)
```
