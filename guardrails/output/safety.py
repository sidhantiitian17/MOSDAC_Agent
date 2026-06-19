"""Toxicity and profanity check on model output (L4).

Primary: deterministic profanity filter via `better-profanity` (zero-shot, no model).
Falls open if the library is not installed — a curated wordlist can always be added
later without changing the interface.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Additional domain-inappropriate terms beyond the default wordlist
_EXTRA_WORDS: list[str] = []


def check_toxicity(text: str) -> bool:
    """Return True if text contains profanity or abuse. Fails open on import error."""
    try:
        from better_profanity import profanity  # type: ignore

        profanity.load_censor_words(whitelist_words=[], custom_words=_EXTRA_WORDS)
        return profanity.contains_profanity(text)
    except ImportError:
        logger.debug("better_profanity not installed — toxicity check skipped")
        return False
    except Exception as exc:
        logger.debug("Toxicity check error (fail-open): %s", exc)
        return False
