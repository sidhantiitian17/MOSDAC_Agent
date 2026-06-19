"""Drupal JSON:API → Graph RAG incremental ingestion.

Run:  python drupal_ingest.py

Drupal-specific config (URL, credentials) comes from .env via python-dotenv.
All other settings (ChromaDB path, Neo4j URI, chunk size, embedder, …) are
inherited from graph_rag.config.settings so nothing is duplicated.

Delta sync: a JSON state file (drupal_ingestion_state.json) maps
  { drupal_uuid: sha256(title + body_html) }
so unchanged nodes are skipped entirely — no re-embedding, no LLM calls.

KG creation is fully delegated to IngestionPipeline.run_on_documents() so
Drupal articles go through the identical code path as file-based ingestion:
quantity_parser, measurements, resolver, _pick_anchor, upsert_triples,
upsert_measurements, upsert_chunks — nothing duplicated here.
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
from langchain_core.documents import Document
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("drupal_ingest")


# ─────────────────────────────────────────────────────────────────────────────
# Config — Drupal-only vars; DB settings come from graph_rag.config.settings
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DrupalConfig:
    jsonapi_url: str
    username: str
    password: str
    state_path: str
    page_size: int
    request_timeout: int

    @staticmethod
    def from_env() -> "DrupalConfig":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise SystemExit(f"Missing required environment variable: {name}")
            return value

        return DrupalConfig(
            jsonapi_url=required("DRUPAL_JSONAPI_URL"),
            username=required("DRUPAL_USERNAME"),
            password=required("DRUPAL_PASSWORD"),
            state_path=os.getenv("DRUPAL_STATE_PATH", "drupal_ingestion_state.json"),
            page_size=int(os.getenv("DRUPAL_PAGE_SIZE", "50")),
            request_timeout=int(os.getenv("DRUPAL_REQUEST_TIMEOUT", "30")),
        )


# ─────────────────────────────────────────────────────────────────────────────
# State management & hashing (delta sync)
# ─────────────────────────────────────────────────────────────────────────────
def compute_content_hash(title: str, body_html: str) -> str:
    """SHA-256 over canonical JSON of title+body — stable regardless of dict order."""
    canonical = json.dumps(
        {"title": title, "body": body_html}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StateManager:
    """JSON-backed {uuid: content_hash} store with atomic, crash-safe writes."""

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
        """Return 'new', 'updated', or 'skip'."""
        existing = self._state.get(uuid)
        if existing is None:
            return "new"
        if existing != content_hash:
            return "updated"
        return "skip"

    def record(self, uuid: str, content_hash: str) -> None:
        self._state[uuid] = content_hash

    def save(self) -> None:
        """Atomic write via temp file + os.replace — no torn writes on crash."""
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
# API ingestion & authentication (with cursor pagination)
# ─────────────────────────────────────────────────────────────────────────────
class DrupalClient:
    """Fetches JSON:API nodes with Basic auth, following links.next pagination."""

    def __init__(self, config: DrupalConfig) -> None:
        self._config = config
        self._session = requests.Session()
        self._session.auth = HTTPBasicAuth(config.username, config.password)
        self._session.headers.update({"Accept": "application/vnd.api+json"})

    def iter_nodes(self) -> Iterator[dict]:
        """Generator — yields one node dict at a time, flat memory across all pages."""
        url: str | None = self._config.jsonapi_url
        params: dict | None = {"page[limit]": self._config.page_size}
        while url:
            resp = self._session.get(url, params=params, timeout=self._config.request_timeout)
            resp.raise_for_status()
            payload = resp.json()
            for node in payload.get("data", []):
                yield node
            # links.next already embeds page params, so clear our own after page 1.
            url = payload.get("links", {}).get("next", {}).get("href")
            params = None


# ─────────────────────────────────────────────────────────────────────────────
# Data parsing & cleaning
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ParsedNode:
    uuid: str
    title: str
    text: str        # clean plain text (HTML stripped)
    body_html: str   # raw HTML — used for hashing (before cleaning)
    content_hash: str


def parse_node(node: dict) -> ParsedNode:
    """Extract uuid/title/clean-text and compute the delta-sync hash."""
    attributes = node.get("attributes", {}) or {}
    uuid = node.get("id", "")
    title = attributes.get("title") or ""
    body = attributes.get("body") or {}
    body_html = (body or {}).get("value") or ""
    clean_text = BeautifulSoup(body_html, "html.parser").get_text(separator=" ", strip=True)
    return ParsedNode(
        uuid=uuid,
        title=title,
        text=clean_text,
        body_html=body_html,
        content_hash=compute_content_hash(title, body_html),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shared helper
# ─────────────────────────────────────────────────────────────────────────────
def _to_document(parsed: ParsedNode) -> Document:
    """Wrap a Drupal node as a LangChain Document for the existing pipeline."""
    return Document(
        page_content=parsed.text,
        metadata={
            "source": parsed.uuid,
            "file_name": parsed.title,
            "file_hash": parsed.content_hash,
            "drupal_uuid": parsed.uuid,   # metadata filter key for Chroma deletion
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Vector deletion helper (UPDATED nodes only)
# ─────────────────────────────────────────────────────────────────────────────
def _delete_stale_vector_chunks(uuid: str) -> None:
    """Remove old Chroma chunks for an UPDATED node before re-indexing.

    Must run before IngestionPipeline.run_on_documents() so stale chunks are
    purged before new ones are added. The pipeline has no concept of Drupal
    UUIDs, so this stays as a Drupal-specific concern here.
    """
    from graph_rag.embeddings import get_embedder
    from graph_rag.vector_store.chroma_store import ChromaStore

    try:
        store = ChromaStore(embedder=get_embedder())
        raw = store._store._collection
        existing = raw.get(where={"drupal_uuid": uuid})
        if existing["ids"]:
            raw.delete(ids=existing["ids"])
            logger.debug(
                "[vector] deleted %d stale chunks for uuid=%s",
                len(existing["ids"]), uuid,
            )
    except Exception:
        logger.warning("[vector] could not delete old chunks for uuid=%s", uuid, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline delegation — all KG work goes through IngestionPipeline
# ─────────────────────────────────────────────────────────────────────────────
def ingest_node(
    parsed: ParsedNode,
    is_update: bool,
    skip_vector: bool = False,
    skip_graph: bool = False,
) -> None:
    """Route one Drupal article through the full graph_rag ingestion pipeline.

    Uses IngestionPipeline.run_on_documents() so quantity_parser, measurements,
    resolver, _pick_anchor, upsert_triples, upsert_measurements, and upsert_chunks
    all run through the same code as file-based ingestion — no duplication.

    extract_at_document_level=True means one LLM call for the full article text
    instead of one call per chunk, fixing the N-LLM-calls-per-article bug.
    """
    from graph_rag.ingestion.pipeline import IngestionPipeline

    if is_update and not skip_vector:
        _delete_stale_vector_chunks(parsed.uuid)

    pipeline = IngestionPipeline(
        skip_vector=skip_vector,
        skip_graph=skip_graph,
        extract_at_document_level=True,
        kg_min_confidence=float(os.getenv("DRUPAL_KG_MIN_CONFIDENCE", "0.6")),
    )
    pipeline.run_on_documents([_to_document(parsed)])


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────
def run(config: DrupalConfig) -> dict[str, int]:
    client = DrupalClient(config)
    state = StateManager(config.state_path)
    stats = {"scanned": 0, "new": 0, "updated": 0, "skipped": 0, "errors": 0}

    for node in client.iter_nodes():
        stats["scanned"] += 1
        try:
            parsed = parse_node(node)
        except Exception:
            stats["errors"] += 1
            logger.exception("Failed to parse node %s", node.get("id", "<unknown>"))
            continue

        verdict = state.verdict(parsed.uuid, parsed.content_hash)
        if verdict == "skip":
            stats["skipped"] += 1
            logger.debug("SKIP    %s  %s", parsed.uuid, parsed.title)
            continue

        try:
            ingest_node(parsed, is_update=(verdict == "updated"))
        except Exception:
            stats["errors"] += 1
            logger.exception("Failed to ingest %s (%s)", parsed.uuid, parsed.title)
            continue

        state.record(parsed.uuid, parsed.content_hash)
        stats[verdict] += 1
        logger.info("%s  %s  %s", verdict.upper(), parsed.uuid, parsed.title)

    state.save()
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
        config = DrupalConfig.from_env()
        run(config)
        return 0
    except requests.HTTPError as exc:
        logger.error("Drupal API error: %s", exc)
        return 1
    except SystemExit as exc:
        logger.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
