"""PII-safe structured audit logging (L5).

One JSON record per request.  Fields:
  request_id    — UUIDv4 for correlation
  session_hash  — SHA-256(session_id)[:16] — never the raw id
  timestamp     — ISO-8601 UTC
  action        — allow | sanitize | refuse
  reason_codes  — list of guard reason codes (e.g. ["injection:exfil", "pii_redacted"])
  grounded      — bool: did the answer pass the grounding checks?
  refused       — bool: was a refusal returned to the user?
  top_score     — best retrieval relevance score (float, 4 d.p.)
  latency_ms    — total request latency in milliseconds
  has_citations — bool: did the response carry verified citations?

NEVER includes: raw user text, raw model output, session_id, IP address.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import List

audit_logger = logging.getLogger("guardrails.audit")

# ── Durable file sink (attached once, lazily) ─────────────────────────────────
_sink_lock = threading.Lock()
_sink_attached = False

# Cached hash of the active system prompt (recomputed when the file changes), so
# each audit record can be tied to the exact prompt that produced the answer.
_prompt_hash_cache: tuple[float, str] | None = None


def _ensure_file_sink() -> None:
    """Attach a size-rotating file handler to the audit logger when configured."""
    global _sink_attached
    if _sink_attached:
        return
    with _sink_lock:
        if _sink_attached:
            return
        try:
            from guardrails.config import guardrail_settings as cfg

            path = (cfg.audit_log_path or "").strip()
            if path:
                from logging.handlers import RotatingFileHandler

                Path(path).parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(
                    path,
                    maxBytes=cfg.audit_log_max_bytes,
                    backupCount=cfg.audit_log_backups,
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter("%(message)s"))
                audit_logger.addHandler(handler)
                audit_logger.setLevel(logging.INFO)
                audit_logger.info(json.dumps({"event": "audit_sink_attached", "path": path}))
        except Exception:  # never let logging setup break a request
            pass
        _sink_attached = True


def _system_prompt_hash() -> str:
    """Short hash of the active system prompt file (cached on mtime)."""
    global _prompt_hash_cache
    try:
        from graph_rag.config import settings

        p = Path(settings.system_prompt_path)
        if not p.exists():
            return "default"
        mtime = p.stat().st_mtime
        if _prompt_hash_cache and _prompt_hash_cache[0] == mtime:
            return _prompt_hash_cache[1]
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        _prompt_hash_cache = (mtime, digest)
        return digest
    except Exception:
        return "unknown"


def _hash_session(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


def log_request(
    *,
    session_id: str,
    action: str,
    reason_codes: List[str],
    grounded: bool,
    refused: bool,
    top_score: float = 0.0,
    latency_ms: float = 0.0,
    has_citations: bool = False,
) -> str:
    """Write one audit record; returns the request_id for response correlation."""
    _ensure_file_sink()
    request_id = str(uuid.uuid4())
    record = {
        "request_id": request_id,
        "session_hash": _hash_session(session_id),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "reason_codes": reason_codes,
        "grounded": grounded,
        "refused": refused,
        "top_score": round(top_score, 4),
        "latency_ms": round(latency_ms, 1),
        "has_citations": has_citations,
        "system_prompt_hash": _system_prompt_hash(),
    }
    audit_logger.info(json.dumps(record))
    return request_id
