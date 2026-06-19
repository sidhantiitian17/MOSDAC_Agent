"""PII detection and redaction.

Strategy (tiered, no new models):
  1. Microsoft Presidio (spaCy-backed) if installed — rich NER for person names,
     organisations, locations, credit cards, etc.
  2. India-specific regex recognisers always applied on top — Aadhaar, PAN, GSTIN,
     voter ID, passport, +91 mobile — because Presidio misses these.

Usage:
    from guardrails.input.pii import redact, contains_pii
"""
from __future__ import annotations

import logging
import re
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ── India-specific patterns ───────────────────────────────────────────────────

# Aadhaar: 12 digits optionally grouped as XXXX XXXX XXXX; starts with 2-9
_AADHAAR_RE = re.compile(r"\b[2-9]\d{3}[\s\-]?\d{4}[\s\-]?\d{4}\b")

# PAN: 5 letters + 4 digits + 1 letter  e.g. ABCDE1234F
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")

# Indian mobile: optional +91 / 0 prefix, 10 digits starting with 6-9
_PHONE_IN_RE = re.compile(r"(?:\+91[\-\s]?|0)?(?<!\d)[6-9]\d{9}(?!\d)")

# Email
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# GSTIN: 15-char alphanumeric with fixed structure
_GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")

# Indian passport: letter + 7 digits
_PASSPORT_RE = re.compile(r"\b[A-PR-WY][1-9]\d{7}\b")

# Voter ID: 3 letters + 7 digits
_VOTER_ID_RE = re.compile(r"\b[A-Z]{3}[0-9]{7}\b")

# Credit card: major card patterns
_CC_RE = re.compile(
    r"\b(?:4\d{3}|5[1-5]\d{2}|6011|3[47]\d{2})[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"
)

# IPv4
_IP_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
)

_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (_AADHAAR_RE, "<AADHAAR>"),
    (_PAN_RE, "<PAN>"),
    (_PHONE_IN_RE, "<PHONE>"),
    (_EMAIL_RE, "<EMAIL>"),
    (_GSTIN_RE, "<GSTIN>"),
    (_PASSPORT_RE, "<PASSPORT>"),
    (_VOTER_ID_RE, "<VOTER_ID>"),
    (_CC_RE, "<CREDIT_CARD>"),
    (_IP_RE, "<IP_ADDRESS>"),
]


# ── Presidio helper ───────────────────────────────────────────────────────────

def _try_presidio(text: str) -> str | None:
    """Run Presidio if installed. Returns redacted text or None on failure."""
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore
        from presidio_anonymizer import AnonymizerEngine  # type: ignore

        analyzer = AnalyzerEngine()
        anonymizer = AnonymizerEngine()
        results = analyzer.analyze(text=text, language="en")
        if results:
            return anonymizer.anonymize(text=text, analyzer_results=results).text
        return text
    except Exception as exc:
        logger.debug("Presidio unavailable (%s) — regex fallback only", exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def redact(text: str) -> str:
    """Redact PII.  Presidio first (if installed), then India-specific regex always."""
    result = _try_presidio(text)
    if result is not None:
        text = result
    for pattern, placeholder in _PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def contains_pii(text: str) -> bool:
    """Quick scan — True if text likely contains any of the tracked PII types."""
    return any(pat.search(text) for pat, _ in _PATTERNS)
