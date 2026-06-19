"""Detect and scrub system-prompt / context / secret leakage in model output (L4).

Catches cases where a jailbreak succeeded partially and the model echoed back
its system prompt, raw retrieval output, fence markers, or credential strings.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_LEAKAGE_PATTERNS = [
    # System prompt section headers that should never appear in output
    re.compile(r"RESPONSE RULES:|SCREENSHOT ANALYSIS INSTRUCTIONS:|GENERAL RULES:|MULTI-HOP PROTOCOL:", re.I),
    re.compile(r"CONVERSATION & FOLLOW-UPS:|MULTI-HOP & QUANTITATIVE REASONING:", re.I),
    # Raw retriever formatting
    re.compile(r"\[Source:.*?\|\s*score=[\d.]+\]"),
    re.compile(r"KNOWLEDGE GRAPH \(entity relationships\):|DOCUMENT PASSAGES \(relevant text", re.I),
    # Spotlighting fences (should never appear in user-visible output)
    re.compile(r"<<CONTEXT>>|<</CONTEXT>>|<<USER_QUERY>>|<</USER_QUERY>>", re.I),
    # Credential-like strings
    re.compile(r"TABBY_API_TOKEN|NEO4J_PASSWORD|NOMIC_API_TOKEN|TABBY_API_KEY", re.I),
    # Source file internals
    re.compile(r"system_prompt\.txt|graph_rag_chain\.py|tabby_client\.py", re.I),
    # NEED_MORE protocol tags (from IterativeReasoner — should not reach user)
    re.compile(r"NEED_MORE:\s*.+", re.I),
]


def check_leakage(text: str) -> bool:
    """Return True if text reveals system internals."""
    for pattern in _LEAKAGE_PATTERNS:
        if pattern.search(text):
            logger.warning("Output leakage detected")
            return True
    return False


def scrub_leakage(text: str) -> str:
    """Replace matched leakage patterns with [REDACTED]."""
    for pattern in _LEAKAGE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text.strip()
