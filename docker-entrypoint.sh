#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint: fix ownership of the writable runtime mounts, THEN drop privileges.
#
# The image runs the application as the non-root user `appuser` (UID 10001 —
# hardening H3). But two paths are writable mounts whose ownership is decided by
# the HOST at runtime, not baked into the image:
#   * /app/data      — named volume for the SQLite conversation store.
#   * /app/chroma_db — host bind mount with the vector index. ChromaDB opens its
#                      SQLite file read-WRITE (WAL journal + schema migrations)
#                      even for read-only queries, so a host-owned directory makes
#                      BM25 warm-up fail with "attempt to write a readonly
#                      database". chowning it to appuser fixes that.
#
# We start as root ONLY long enough to chown these, then exec the real process
# as appuser via gosu. Nothing in the application/dependency tree runs as root.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

for d in /app/data /app/chroma_db; do
    if [ -d "$d" ]; then
        chown -R appuser:appuser "$d" 2>/dev/null \
            || echo "entrypoint: could not chown $d (read-only/unsupported FS) — continuing" >&2
    fi
done

exec gosu appuser "$@"
