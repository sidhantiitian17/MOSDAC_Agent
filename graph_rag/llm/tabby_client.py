"""LangChain client for Tabby ML — an OpenAI-compatible LLM endpoint.

Tabby ML REQUIRES streaming=True; non-streaming calls time out silently
(confirmed by test_tabby.py, which uses .stream() exclusively).

Every connection setting comes from .env — never hardcode the token:
    TABBY_BASE_URL=http://localhost:8080/v1     # home development
    TABBY_API_TOKEN=<your token>
    TABBY_MODEL=Qwen2-1.5B-Instruct

    # ISRO production — swap the values above (no code change needed):
    # TABBY_BASE_URL=http://192.168.100.101:8080/v1
    # TABBY_API_TOKEN=<isro token>

Resilience (P1-5):
  * Explicit request_timeout + bounded max_retries so a slow/hung endpoint can
    never stall a request thread indefinitely.
  * A process-wide concurrency semaphore (``llm_slot``) provides backpressure in
    front of the single shared endpoint — chat, extraction, contextualization and
    summarization all hit the same Tabby instance.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from functools import lru_cache

from langchain_openai import ChatOpenAI

from graph_rag.config import settings

logger = logging.getLogger(__name__)

# Process-wide concurrency cap. Built lazily so the limit always reflects config.
_llm_semaphore: threading.BoundedSemaphore | None = None
_sem_lock = threading.Lock()


def _get_semaphore() -> threading.BoundedSemaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        with _sem_lock:
            if _llm_semaphore is None:
                limit = max(1, settings.llm_max_concurrency)
                _llm_semaphore = threading.BoundedSemaphore(limit)
    return _llm_semaphore


@contextmanager
def llm_slot(timeout: float | None = None):
    """Bound concurrent LLM calls. Acquires a slot (waiting up to ``timeout``s);
    if the pool is saturated it proceeds anyway rather than blocking forever, so
    the cap throttles load without becoming a new failure mode."""
    sem = _get_semaphore()
    wait = settings.llm_request_timeout if timeout is None else timeout
    acquired = sem.acquire(timeout=wait)
    if not acquired:
        logger.warning("LLM concurrency slot not acquired within %.1fs; proceeding.", wait)
    try:
        yield
    finally:
        if acquired:
            try:
                sem.release()
            except ValueError:  # pragma: no cover - over-release guard
                pass


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """Return a ChatOpenAI singleton pointed at the local Tabby ML endpoint.

    Reads TABBY_BASE_URL / TABBY_API_TOKEN / TABBY_MODEL / LLM_TEMPERATURE /
    LLM_MAX_TOKENS / LLM_REQUEST_TIMEOUT / LLM_MAX_RETRIES from .env.  All callers
    share this instance — to use a different temperature construct ChatOpenAI(...)
    directly.
    """
    if not settings.tabby_api_token:
        raise ValueError(
            "TABBY_API_TOKEN is not set. Add it to .env — "
            "the Tabby token must never be hardcoded in source."
        )
    return ChatOpenAI(
        model=settings.tabby_model,
        api_key=settings.tabby_api_token,
        base_url=settings.tabby_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        streaming=True,  # Tabby ML requires streaming or calls time out
        timeout=settings.llm_request_timeout,   # hard per-request timeout (P1-5)
        max_retries=settings.llm_max_retries,   # bounded retries (P1-5)
    )
