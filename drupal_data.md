# Drupal JSON:API → Graph RAG Ingestion Plan

> **Role:** Expert Python Data Engineer & AI Architect (Graph RAG + Headless CMS).
> **Goal:** A production-ready, modular Python pipeline that pulls content from a
> Drupal JSON:API, detects new/changed/unchanged nodes via SHA-256 content hashing
> (delta sync), cleans the HTML, and routes the result into **both** a Vector DB and
> a Knowledge Graph.

This document is the implementation blueprint plus the complete reference code
(`drupal_ingest.py`) and the `.env` template.

---

## 1. Architecture & Separation of Concerns

The pipeline is intentionally split into independent, testable units. Each box is a
class/function with one job — you can unit-test the hashing logic without a live
Drupal, and swap ChromaDB/Neo4j without touching the fetch loop.

```
                         ┌─────────────────────────┐
   .env (python-dotenv)  │      Config (dataclass) │   ← STEP 1
   ───────────────────►  │  all settings, no       │
                         │  hardcoding             │
                         └────────────┬────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                        ▼
      ┌───────────────┐      ┌─────────────────┐      ┌────────────────┐
      │ DrupalClient  │      │  StateManager   │      │   Processing   │
      │  (STEP 3)     │      │   (STEP 2)      │      │   (STEP 4)     │
      │ HTTPBasicAuth │      │ ingestion_state │      │ BeautifulSoup  │
      │ + pagination  │      │ .json  hashes   │      │ HTML → text    │
      └───────┬───────┘      └────────┬────────┘      └───────┬────────┘
              │                       │                       │
              └───────────┬───────────┴───────────────────────┘
                          ▼
                ┌───────────────────┐
                │   Orchestrator    │  decide NEW / UPDATED / SKIP
                │   (run loop)      │
                └─────────┬─────────┘
                  ┌───────┴────────┐
                  ▼                ▼
        ┌──────────────────┐  ┌──────────────────┐
        │ push_to_vector_db│  │ push_to_graph_db │   ← STEP 5 (interfaces)
        │ chunk + embed    │  │ entities + Neo4j │
        └──────────────────┘  └──────────────────┘
                          │
                          ▼
                 logging (STEP 6)  →  "Skipped 15 / Ingested 2 / Updated 1"
```

**Module responsibilities**

| Concern | Unit | Why isolated |
|---|---|---|
| Configuration | `Config.from_env()` | One source of truth; fail fast if a var is missing. |
| API fetching | `DrupalClient.iter_nodes()` | Generator that hides auth + pagination; yields raw nodes. |
| State / hashing | `StateManager` | Pure logic — testable with no network or DB. |
| Processing | `parse_node()` | HTML → clean text; no I/O, easy to test. |
| DB insertion | `push_to_vector_db` / `push_to_graph_db` | Swappable sinks (Chroma, Neo4j, …). |
| Orchestration | `run()` | Wires the above together and reports stats. |

> **Alignment with this repo:** the existing
> [`graph_rag/ingestion/manifest.py`](graph_rag/ingestion/manifest.py) already does
> SHA-256 content hashing for *file* ingestion (`compute_file_hash`,
> `IngestionManifest`). The Drupal `StateManager` below is the **same idea keyed by
> Drupal UUID instead of file path** — once this is wired into the real pipeline,
> consider merging it into `IngestionManifest` so there is one manifest abstraction.

---

## 2. STEP 1 — Environment Variables (No Hardcoding)

All configuration is loaded from `.env` with `python-dotenv`. The pipeline refuses
to start if a required variable is missing (fail fast, clear message).

**Required variables**

| Variable | Purpose | Example |
|---|---|---|
| `DRUPAL_JSONAPI_URL` | JSON:API collection endpoint | `http://my-drupal-site.ddev.site/jsonapi/node/article` |
| `DRUPAL_USERNAME` | Basic-auth user | `api_user` |
| `DRUPAL_PASSWORD` | Basic-auth password | `••••••` |
| `CHROMA_PERSIST_DIR` | ChromaDB local storage directory | `./chroma_db` |
| `CHROMA_COLLECTION` | ChromaDB collection name | `graph_rag` |
| `GRAPH_DB_CONNECTION_STRING` | Neo4j Bolt URI | `bolt://localhost:7687` |

**Optional (sensible defaults)**

| Variable | Default | Purpose |
|---|---|---|
| `INGESTION_STATE_PATH` | `ingestion_state.json` | Where the UUID→hash map lives |
| `DRUPAL_PAGE_SIZE` | `50` | JSON:API `page[limit]` |
| `DRUPAL_REQUEST_TIMEOUT` | `30` | Per-request timeout (seconds) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## 3. STEP 2 — State Management & Hashing (Delta Sync)

**State store:** a single JSON file `ingestion_state.json` mapping
`{ "content_uuid": "content_hash" }`. JSON is chosen for transparency and zero
dependencies; for very large catalogs swap the same interface to SQLite.

**Hash input:** SHA-256 over a *canonical* serialization of the fields that define
"meaningful change" — here `title` + `body.value`. Using `json.dumps(..., sort_keys=True)`
guarantees the same bytes regardless of dict ordering, so the hash is stable.

**Decision table**

| UUID in state? | Hash matches? | Verdict | Action |
|---|---|---|---|
| No | — | **NEW** | Ingest + record hash |
| Yes | No | **UPDATED** | Re-ingest + overwrite hash |
| Yes | Yes | **SKIP** | Do nothing (saves embedding + LLM cost) |

**Why hash the payload, not a timestamp?** Drupal's `changed` field can be bumped by
edits that don't affect content (e.g. re-save with no diff). Hashing the actual
title/body means we only pay for re-embedding when the text truly changed.

**Crash safety:** the state file is written **atomically** (write to a temp file,
then `os.replace`) so an interrupted run never leaves a half-written, corrupt state.

---

## 4. STEP 3 — API Ingestion & Authentication

- `requests.Session` with `HTTPBasicAuth(username, password)` reused across requests
  (connection pooling).
- Pagination follows Drupal JSON:API's HATEOAS link: keep requesting
  `response.json()["links"]["next"]["href"]` until it's absent.
- `page[limit]` set from `DRUPAL_PAGE_SIZE`.
- `response.raise_for_status()` surfaces auth/HTTP errors immediately.
- Implemented as a **generator** (`iter_nodes`) so memory stays flat regardless of
  catalog size — we never hold the whole site in RAM.

---

## 5. STEP 4 — Data Parsing & Cleaning

For each node:
- `uuid  = node["id"]`
- `title = node["attributes"]["title"]`
- raw HTML = `node["attributes"]["body"]["value"]` (defensively handles missing body)
- `BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)` strips tags
  and collapses whitespace into clean plain text ready for chunking/embedding.

---

## 6. STEP 5 — Graph RAG Population (Interfaces)

Two clearly-marked placeholder sinks. They log today and carry `TODO` markers showing
exactly where the heavy lifting plugs in:

- **`push_to_vector_db(uuid, title, text)`** — *TODO:* chunk with LangChain/LlamaIndex
  (`RecursiveCharacterTextSplitter`), embed each chunk (this repo uses the
  [`OllamaEmbedder`](graph_rag/embeddings/) → `bge-large`), upsert into ChromaDB
  (`chromadb.PersistentClient(path=config.chroma_persist_dir)`) keyed by `uuid` so
  an UPDATED node replaces its old vectors — never appends or edits create duplicates.
- **`push_to_graph_db(uuid, title, text)`** — *TODO:* run entity/relationship
  extraction (this repo's [`extractor.py`](graph_rag/knowledge_graph/extractor.py),
  LLM or spaCy) and `MERGE` nodes/edges into Neo4j via
  [`neo4j_store.py`](graph_rag/knowledge_graph/neo4j_store.py), keyed by `uuid` for
  idempotent updates.

> **Upsert, not append:** because UPDATED content re-runs these functions, both sinks
> must key on `uuid` and replace prior data, otherwise edits create duplicates.

---

## 7. STEP 6 — Output & Logging

Standard-library `logging`. Per-node DEBUG lines (`NEW`/`UPDATED`/`SKIP`) and a final
INFO summary:

```
INFO  Ingestion complete — scanned 18 | new 2 | updated 1 | skipped 15 | errors 0
```

---

## 8. Reference Implementation — `drupal_ingest.py`

```python
"""Drupal JSON:API → Graph RAG incremental ingestion.

Fetches nodes from a Drupal JSON:API collection, detects new/changed content via
SHA-256 hashing of the title+body payload (delta sync), cleans the HTML, and routes
each new/updated node to a vector DB and a knowledge graph.

Config is loaded strictly from .env — nothing is hardcoded.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterator

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("drupal_ingest")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Configuration (no hardcoding; fail fast on missing required vars)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Config:
    jsonapi_url: str
    username: str
    password: str
    chroma_persist_dir: str
    chroma_collection: str
    graph_db_connection: str
    state_path: str = "ingestion_state.json"
    page_size: int = 50
    request_timeout: int = 30

    @staticmethod
    def from_env() -> "Config":
        load_dotenv()  # reads .env from CWD; real env vars take precedence

        def required(name: str) -> str:
            value = os.getenv(name)
            if not value:
                raise SystemExit(f"Missing required environment variable: {name}")
            return value

        return Config(
            jsonapi_url=required("DRUPAL_JSONAPI_URL"),
            username=required("DRUPAL_USERNAME"),
            password=required("DRUPAL_PASSWORD"),
            chroma_persist_dir=required("CHROMA_PERSIST_DIR"),
            chroma_collection=os.getenv("CHROMA_COLLECTION", "graph_rag"),
            graph_db_connection=required("GRAPH_DB_CONNECTION_STRING"),
            state_path=os.getenv("INGESTION_STATE_PATH", "ingestion_state.json"),
            page_size=int(os.getenv("DRUPAL_PAGE_SIZE", "50")),
            request_timeout=int(os.getenv("DRUPAL_REQUEST_TIMEOUT", "30")),
        )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — State management & hashing (delta sync)
# ─────────────────────────────────────────────────────────────────────────────
def compute_content_hash(title: str, body_html: str) -> str:
    """Stable SHA-256 over the fields that define a meaningful change.

    Canonical JSON (sorted keys) makes the byte stream deterministic, so the same
    content always yields the same hash regardless of dict ordering.
    """
    canonical = json.dumps(
        {"title": title, "body": body_html}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StateManager:
    """JSON-backed `{uuid: content_hash}` store with atomic, crash-safe writes."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._state: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("State file %s unreadable (%s); starting fresh.", self.path, exc)
            return {}

    def verdict(self, uuid: str, content_hash: str) -> str:
        """Return one of: 'new', 'updated', 'skip'."""
        existing = self._state.get(uuid)
        if existing is None:
            return "new"
        if existing != content_hash:
            return "updated"
        return "skip"

    def record(self, uuid: str, content_hash: str) -> None:
        self._state[uuid] = content_hash

    def save(self) -> None:
        """Atomic write: temp file in the same dir, then os.replace (no torn writes)."""
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — API ingestion & authentication (with pagination)
# ─────────────────────────────────────────────────────────────────────────────
class DrupalClient:
    """Fetches JSON:API nodes with Basic auth, following links.next pagination."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(config.username, config.password)
        self._session.headers.update({"Accept": "application/vnd.api+json"})

    def iter_nodes(self) -> Iterator[dict]:
        """Yield each node dict across all pages. Memory stays flat (generator)."""
        url: str | None = self._config.jsonapi_url
        params: dict | None = {"page[limit]": self._config.page_size}
        while url:
            resp = self._session.get(
                url, params=params, timeout=self._config.request_timeout
            )
            resp.raise_for_status()
            payload = resp.json()
            for node in payload.get("data", []):
                yield node
            # JSON:API HATEOAS: follow links.next until it disappears.
            # The next href already embeds page params, so clear our own.
            url = payload.get("links", {}).get("next", {}).get("href")
            params = None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Data parsing & cleaning
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParsedNode:
    uuid: str
    title: str
    text: str
    content_hash: str


def parse_node(node: dict) -> ParsedNode:
    """Extract uuid/title/clean-text and compute the delta-sync hash."""
    attributes = node.get("attributes", {}) or {}
    uuid = node.get("id", "")
    title = attributes.get("title") or ""
    body = attributes.get("body") or {}
    body_html = (body or {}).get("value") or ""

    clean_text = BeautifulSoup(body_html, "html.parser").get_text(
        separator=" ", strip=True
    )
    return ParsedNode(
        uuid=uuid,
        title=title,
        text=clean_text,
        content_hash=compute_content_hash(title, body_html),
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Graph RAG population (interfaces — placeholders)
# ─────────────────────────────────────────────────────────────────────────────
def push_to_vector_db(uuid: str, title: str, text: str) -> None:
    """Upsert content into the vector store.

    TODO (Graph RAG integration):
      1. Chunk `text` with LangChain/LlamaIndex (e.g. RecursiveCharacterTextSplitter).
      2. Embed each chunk (this repo: OllamaEmbedder → bge-large).
      3. Upsert into ChromaDB keyed by `uuid` so an UPDATED node REPLACES its old
         vectors (delete-by-uuid then add) — never append, or edits duplicate.
    """
    # TODO: chromadb.PersistentClient(path=config.chroma_persist_dir)
    #       .get_or_create_collection(config.chroma_collection)
    #       .upsert(ids=[uuid], documents=[text], ...)
    logger.debug("[vector] would upsert uuid=%s title=%r (%d chars)", uuid, title, len(text))


def push_to_graph_db(uuid: str, title: str, text: str) -> None:
    """Upsert content into the knowledge graph.

    TODO (Graph RAG integration):
      1. Run entity/relationship extraction (this repo: knowledge_graph/extractor.py,
         LLM or spaCy backend) over `text`.
      2. MERGE nodes/edges into Neo4j (knowledge_graph/neo4j_store.py), keyed by
         `uuid` for idempotent re-ingest of UPDATED content.
    """
    logger.debug("[graph] would MERGE uuid=%s title=%r (%d chars)", uuid, title, len(text))


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration + STEP 6 (logging summary)
# ─────────────────────────────────────────────────────────────────────────────
def run(config: Config) -> dict[str, int]:
    client = DrupalClient(config)
    state = StateManager(config.state_path)
    stats = {"scanned": 0, "new": 0, "updated": 0, "skipped": 0, "errors": 0}

    for node in client.iter_nodes():
        stats["scanned"] += 1
        try:
            parsed = parse_node(node)
        except Exception:  # noqa: BLE001 — one bad node must not kill the run
            stats["errors"] += 1
            logger.exception("Failed to parse node %s", node.get("id", "<unknown>"))
            continue

        verdict = state.verdict(parsed.uuid, parsed.content_hash)
        if verdict == "skip":
            stats["skipped"] += 1
            logger.debug("SKIP    %s (%s)", parsed.uuid, parsed.title)
            continue

        try:
            push_to_vector_db(parsed.uuid, parsed.title, parsed.text)
            push_to_graph_db(parsed.uuid, parsed.title, parsed.text)
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
            logger.exception("Failed to ingest node %s", parsed.uuid)
            continue

        state.record(parsed.uuid, parsed.content_hash)
        stats[verdict] += 1  # 'new' or 'updated'
        logger.debug("%s %s (%s)", verdict.upper(), parsed.uuid, parsed.title)

    state.save()  # persist only after the run completes successfully
    logger.info(
        "Ingestion complete — scanned %d | new %d | updated %d | skipped %d | errors %d",
        stats["scanned"], stats["new"], stats["updated"], stats["skipped"], stats["errors"],
    )
    return stats


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env()
        run(config)
        return 0
    except requests.HTTPError as exc:
        logger.error("Drupal API error: %s", exc)
        return 1
    except SystemExit as exc:  # raised by Config.from_env() on missing vars
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

---

## 9. `.env` Template

Save as `.env.drupal.example` and copy to `.env` (which is git-ignored). **Never
commit real credentials.**

```dotenv
# ── Drupal JSON:API source ────────────────────────────────────────────────
DRUPAL_JSONAPI_URL=http://my-drupal-site.ddev.site/jsonapi/node/article
DRUPAL_USERNAME=api_user
DRUPAL_PASSWORD=change-me

# ── ChromaDB (embedded / local — no server needed) ───────────────────────
# Matches the existing CHROMA_PERSIST_DIR from graph_rag/config.py
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=graph_rag

# ── Neo4j ─────────────────────────────────────────────────────────────────
GRAPH_DB_CONNECTION_STRING=bolt://localhost:7687

# ── Delta-sync state & fetch tuning (optional; defaults shown) ────────────
INGESTION_STATE_PATH=ingestion_state.json
DRUPAL_PAGE_SIZE=50
DRUPAL_REQUEST_TIMEOUT=30
LOG_LEVEL=INFO
```

---

## 10. Dependencies

```bash
pip install requests beautifulsoup4 python-dotenv
```

(`requests` for HTTP+auth, `beautifulsoup4` for HTML→text, `python-dotenv` for config.)

---

## 11. Run

```bash
python drupal_ingest.py
```

- **First run:** every node is `NEW` → fully ingested; `ingestion_state.json` created.
- **Later runs:** unchanged nodes are `SKIP`ped (no embedding/LLM cost); only edited or
  new nodes are processed.

---

## 12. Testing Strategy (per repo's 80% target)

| Unit | Test (no network/DB needed) |
|---|---|
| `compute_content_hash` | Same title+body → same hash; any change → different hash. |
| `StateManager.verdict` | Missing uuid → `new`; changed hash → `updated`; same → `skip`. |
| `StateManager.save` | Atomic write survives simulated interruption (temp file cleaned up). |
| `parse_node` | HTML stripped; missing `body` → empty text, no crash. |
| `DrupalClient.iter_nodes` | Mock `Session.get` returning 2 pages → yields all nodes, stops when `links.next` absent. |

---

## 13. Production Hardening (next steps)

- **Retries/backoff:** wrap `Session.get` with `urllib3.util.Retry` (429/5xx, exponential backoff).
- **Deletions:** Drupal nodes removed from the source won't be detected here. Track seen
  UUIDs per run and prune state + DB entries for UUIDs absent from the latest full scan.
- **Secrets:** prefer OAuth2/JWT (Drupal `simple_oauth`) over Basic auth for production.
- **Scale:** swap the JSON `StateManager` for SQLite (same interface) past ~100k nodes.
- **Consolidate:** fold this into the repo's [`IngestionManifest`](graph_rag/ingestion/manifest.py)
  so file-based and Drupal-based ingestion share one manifest abstraction.
```
