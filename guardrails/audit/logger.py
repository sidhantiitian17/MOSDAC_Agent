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
import time
import uuid
from typing import List

audit_logger = logging.getLogger("guardrails.audit")


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
    }
    audit_logger.info(json.dumps(record))
    return request_id
