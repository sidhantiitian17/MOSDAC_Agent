"""Input normalization: Unicode NFKC, control-char strip, length cap, charset check.

This is the first step in L1 — cheap, deterministic, zero external deps.
"""
from __future__ import annotations

import re
import unicodedata

# Dangerous control chars (keep tab, newline, CR for multi-line queries)
_CTRL_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"​-\u200F\u202A-\u202E  ﻿]"
)
# Collapse runs of horizontal whitespace (preserve newlines)
_HSPACE_RE = re.compile(r"[ \t]+")
# Very long base64/hex blobs — injection attempt; replace with placeholder
_LONGBLOB_RE = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")


def normalize(text: str, max_length: int = 2000) -> str:
    """NFKC-normalize, strip control chars, collapse whitespace, enforce max length."""
    text = unicodedata.normalize("NFKC", text)
    text = _CTRL_RE.sub("", text)
    text = _HSPACE_RE.sub(" ", text)
    # Enforce max length before blob check so truncated text isn't flagged
    text = text[:max_length]
    text = _LONGBLOB_RE.sub("[ENCODED_CONTENT]", text)
    return text.strip()


def check_length(text: str, max_length: int) -> bool:
    return len(text) <= max_length


def check_charset(text: str) -> bool:
    """Reject binary/non-printable content.

    Accepts Latin, all Indic scripts (Devanagari, Bengali, Tamil, Telugu, …),
    digits, punctuation, symbols.  Rejects stray control characters.
    """
    for ch in text:
        if ch in "\n\r\t":
            continue
        cat = unicodedata.category(ch)
        # L=letter, N=number, Z=separator, P=punctuation, S=symbol
        if cat[0] in ("L", "N", "Z", "P", "S"):
            continue
        if cat.startswith("C"):
            return False
    return True
