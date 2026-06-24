# `observability/` — Metrics

A small, **cross-cutting** package that exposes runtime metrics (request counts, latency,
guardrail refusals/degradation, answer-cache hits) for Prometheus to scrape at
`GET /metrics`. It is imported by both the chat service and the guardrails, and is
deliberately **best-effort**: if `prometheus-client` isn't installed, it degrades to an
in-process fallback and **never lets a metrics call break a request**.

---

## File-by-file

### [metrics.py](metrics.py) — the metrics facade
The actual implementation. Uses Prometheus counters/histograms when available, else an
in-process dict fallback.
- **Functions:** `inc(name, labels)` (increment a counter), `observe(name, value)` (record
  a latency/value), `metrics_enabled()`, `render_latest()` (the Prometheus exposition text),
  and `CONTENT_TYPE` (the exposition media type). `_key` builds the labelled metric key.
- **Depends on:** optional `prometheus-client`.

### [__init__.py](__init__.py)
Re-exports `inc`, `observe`, `metrics_enabled`, `render_latest`, `CONTENT_TYPE` — the public
surface everyone imports as `from observability import inc, observe`.

---

## Who emits metrics

| Caller | Metrics |
|--------|---------|
| [chat_api/service.py](../chat_api/service.py) | `chat_requests_total{action}`, `chat_request_latency_ms`, `answer_cache_total{result}` |
| [chat_api/routes.py](../chat_api/routes.py) | `chat_requests_total{action="error"}`; serves `/metrics` |
| [guardrails/pipeline.py](../guardrails/pipeline.py) | `guardrail_refusals_total{reason}`, `guardrail_degraded_total{check}` |

The `/metrics` endpoint is **admin-token protected** (metrics leak request volumes and
refusal rates) and exempt from the rate limiter so Prometheus polling never trips it. Enable
with `CHAT_API_ENABLE_METRICS=true` + `CHAT_API_ADMIN_TOKEN`.

> The calls are wrapped in `try/except` at every call site — observability is never allowed
> to be the thing that fails a user's request.
