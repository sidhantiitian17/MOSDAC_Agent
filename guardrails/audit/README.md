# `guardrails/audit/` — L5 Audit & Abuse Monitoring

The **final** checkpoint, run at the end of every turn. It records a **PII-safe** structured
log of what happened (without ever storing raw user text) and tracks per-session abuse so a
repeat offender can be temporarily locked out. This is what makes the deployment
**auditable** and gives the abuse counter that L1 consults on the next request.

Invoked from [chat_api/service.py](../../chat_api/service.py) (`log_request`) and
[guardrails/pipeline.py](../pipeline.py) (`record_event` / `is_locked_out`).

---

## File-by-file

### [logger.py](logger.py) — PII-safe structured audit log
Writes one structured record per request: hashed session id, action (allow/refuse), reason
codes, grounded/refused flags, top retrieval score, latency, whether citations were present,
and a hash of the active system prompt — **never the raw question or answer**. Optionally
mirrors to a size-rotating file (`GUARD_AUDIT_LOG_PATH`) that survives restarts.
- **Functions:** `log_request(...)`, `_ensure_file_sink`, `_hash_session`,
  `_system_prompt_hash`.
- **Depends on:** `graph_rag.config`, `guardrails.config`.

### [abuse.py](abuse.py) — per-session abuse counter & lockout
Counts abuse events (injection attempts, off-topic floods, etc.) per session and enforces a
temporary lockout once `GUARD_ABUSE_LOCKOUT_THRESHOLD` is reached. L1 calls `is_locked_out`
at the top of `check_input` and refuses immediately while locked.
- **Functions:** `record_event(session_id)`, `is_locked_out(session_id, threshold)`,
  `event_count(session_id)`, `clear_session(session_id)`.

### [__init__.py](__init__.py)
Package marker (L5 audit/abuse modules).

---

## Privacy by design

- **Session ids are hashed** before logging — you can correlate a session's events without
  knowing who it was.
- **No raw text** (question/answer) is ever written to the audit log — only metadata and
  reason codes. This is a deliberate privacy boundary for a government deployment.
- Stored conversation history (separate, in [chat_api/db/](../../chat_api/db/) /
  [chat_api/session.py](../../chat_api/session.py)) only ever holds the **PII-redacted**
  user turn — redaction happens in L1 before storage.

## Dependencies at a glance
- **Internal:** `graph_rag.config`, `guardrails.config`.
- **Consumed by:** the chat service (audit per turn) and the L1 guard (abuse lockout).
