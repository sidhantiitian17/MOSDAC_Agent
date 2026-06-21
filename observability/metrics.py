"""Lightweight metrics facade — Prometheus when available, in-process otherwise.

Design goals:
  * Zero hard dependency. If ``prometheus_client`` is not installed the module
    still works (counters/histograms accumulate in-process) so nothing breaks and
    ``/metrics`` returns a readable text exposition.
  * One tiny API used everywhere: ``inc(name, labels, amount)`` and
    ``observe(name, value, labels)``. Unknown metric names auto-register.
  * Thread-safe.

Used by the guardrail pipeline (degradation/refusal counters) and the chat API
(request latency, refusal rate, dependency errors).
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_lock = threading.Lock()

# ── Try Prometheus; fall back to in-process accumulators ──────────────────────
try:  # pragma: no cover - exercised only when prometheus_client is present
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _PROM = True
    CONTENT_TYPE = CONTENT_TYPE_LATEST
    _counters: Dict[str, Counter] = {}
    _histograms: Dict[str, Histogram] = {}

    # Pre-declare the well-known metrics with their label sets.
    _COUNTER_LABELS = {
        "chat_requests_total": ("action",),         # allow | refuse | error
        "guardrail_refusals_total": ("reason",),
        "guardrail_degraded_total": ("check",),     # scope | injection
        "dependency_errors_total": ("dependency",), # neo4j | chroma | embedder | llm
        "answer_cache_total": ("result",),          # hit | miss
    }
    _HISTO = {
        "chat_request_latency_ms": ("",),
        "retrieval_latency_ms": ("",),
    }

    def _counter(name: str, labels: Tuple[str, ...]):
        c = _counters.get(name)
        if c is None:
            label_names = _COUNTER_LABELS.get(name, labels)
            c = Counter(name, name.replace("_", " "), label_names)
            _counters[name] = c
        return c

    def _histogram(name: str):
        h = _histograms.get(name)
        if h is None:
            h = Histogram(name, name.replace("_", " "))
            _histograms[name] = h
        return h

except Exception:  # prometheus_client missing → fallback
    _PROM = False
    _fallback_counters: Dict[str, float] = {}
    _fallback_histograms: Dict[str, Tuple[int, float]] = {}  # name -> (count, sum)


def _key(name: str, labels: Optional[Dict[str, str]]) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


def inc(name: str, labels: Optional[Dict[str, str]] = None, amount: float = 1.0) -> None:
    """Increment a counter. Safe to call from any layer; never raises."""
    try:
        if _PROM:
            c = _counter(name, tuple((labels or {}).keys()))
            (c.labels(**labels) if labels else c).inc(amount)
        else:
            with _lock:
                _fallback_counters[_key(name, labels)] = (
                    _fallback_counters.get(_key(name, labels), 0.0) + amount
                )
    except Exception:
        pass


def observe(name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
    """Record a histogram observation (e.g. latency). Never raises."""
    try:
        if _PROM:
            _histogram(name).observe(value)
        else:
            with _lock:
                count, total = _fallback_histograms.get(name, (0, 0.0))
                _fallback_histograms[name] = (count + 1, total + value)
    except Exception:
        pass


def metrics_enabled() -> bool:
    return True


def render_latest() -> bytes:
    """Return the metrics exposition in Prometheus text format."""
    if _PROM:
        return generate_latest()
    lines = ["# Fallback metrics (install prometheus_client for full support)"]
    with _lock:
        for k, v in sorted(_fallback_counters.items()):
            lines.append(f"{k} {v}")
        for name, (count, total) in sorted(_fallback_histograms.items()):
            lines.append(f"{name}_count {count}")
            lines.append(f"{name}_sum {total}")
    return ("\n".join(lines) + "\n").encode("utf-8")
