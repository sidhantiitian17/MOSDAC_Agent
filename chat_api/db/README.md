# `chat_api/db/` — Per-User Conversation Persistence

This sub-package stores **per-user chat history** (conversations + their messages) so a
logged-in user can see and resume past chats. It is **backend-agnostic**: the rest of the
app codes against one interface, and the concrete store (SQLite or PostgreSQL) is chosen
at startup from `CHAT_API_CONV_STORE`.

> This is **different** from [`../session.py`](../session.py): `session.py` holds
> *short-term, anonymous* history in memory/Redis; this package holds *durable, per-user*
> history keyed to an authenticated identity. When auth is disabled (`conv_store="none"`),
> persistence is off and every request behaves anonymously.

---

## When is which backend used?

| `CHAT_API_CONV_STORE` | Backend | Use it for |
|-----------------------|---------|-----------|
| `sqlite` (default) | [sqlite_repo.py](sqlite_repo.py) — stdlib `sqlite3`, no extra dep | **Single replica**. A local file. |
| `postgres` | [postgres_repo.py](postgres_repo.py) — psycopg 3 + connection pool | **Multi-replica / scalable** (shared DB). Needs `CHAT_API_POSTGRES_DSN` + `pip install 'psycopg[binary,pool]'`. |
| `none` | — | Disables persistence entirely. |

> ⚠️ A multi-replica deployment must **not** use SQLite — a local file would split each
> user's history across replicas. The factory **refuses** SQLite when persistent sessions
> are required (hardening H4).

---

## File-by-file

### [repository.py](repository.py) — the interface (depend on this)
The backend-agnostic contract every store implements. Defines the data shapes and the
ABC, so `service.py` never imports a concrete backend.
- **Data classes:** `Conversation` (id, user_id, title, timestamps), `Message`
  (role, content, created_at).
- **`ConversationRepository`** (abstract): `create_conversation`, `get_conversation`
  (ownership-checked), `list_conversations`, `append_message`, `list_messages`,
  `update_title`, `delete_conversation`, `close`.
- **`ConversationNotFoundError`** — raised when a user references a conversation they
  don't own (the API maps it to **404**, not 403, so ids can't be probed).
- **Depends on:** stdlib only.

### [sqlite_repo.py](sqlite_repo.py) — SQLite backend (default)
`SQLiteConversationRepository` — implements the interface on stdlib `sqlite3`. Creates the
schema on first use, enforces ownership in every query, stores ISO timestamps.
- **Depends on:** `chat_api.db.repository`, stdlib `sqlite3`.
- **Path:** `CHAT_API_SQLITE_PATH` (in Docker, a durable named volume at
  `/app/data/conversations.db`).

### [postgres_repo.py](postgres_repo.py) — PostgreSQL backend (multi-replica)
`PostgresConversationRepository` — same interface, backed by psycopg 3 with a connection
pool for concurrent replicas. Shared across all API instances so history is consistent.
- **Depends on:** `chat_api.db.repository`, `psycopg[binary,pool]` (lazy import).
- **DSN:** `CHAT_API_POSTGRES_DSN`.

### [__init__.py](__init__.py) — the backend factory
`build_conversation_repository()` reads `CHAT_API_CONV_STORE` and returns the right
implementation (or `None` for `"none"`). Enforces the SQLite-vs-multi-replica safety rule.
- **Depends on:** `chat_api.config`, both repo modules, `repository`.
- **Used by:** [chat_api/main.py](../main.py) `create_app()` → passes the repo into
  `ChatService`.

---

## How it's used

```
main.create_app()
  └─ repo = build_conversation_repository()      # picks sqlite/postgres/none
       └─ ChatService(repo=repo)
            ├─ chat_authenticated(...)  → repo.create_conversation / append_message / list_messages
            ├─ list_conversations(...)  → repo.list_conversations
            ├─ get_conversation_with_messages(...) → repo.get_conversation + list_messages
            └─ delete_conversation(...) → repo.delete_conversation
```

Routes that surface this: `GET /conversations`, `GET /conversations/{id}/messages`,
`DELETE /conversations/{id}` (all require an authenticated user via
[`../auth.py`](../auth.py)). Ownership is enforced **inside the repo**, so the HTTP layer
can stay thin.
