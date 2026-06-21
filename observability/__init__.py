"""Cross-cutting observability — metrics shared by guardrails and the chat API.

Importing this package never fails: if ``prometheus_client`` is installed the
metrics are real Prometheus collectors exposed at ``/metrics``; otherwise a
dependency-free in-process fallback keeps the same API so callers don't branch.
"""
from observability.metrics import (
    inc,
    observe,
    metrics_enabled,
    render_latest,
    CONTENT_TYPE,
)

__all__ = ["inc", "observe", "metrics_enabled", "render_latest", "CONTENT_TYPE"]
