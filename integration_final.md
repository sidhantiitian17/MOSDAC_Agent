# MOSDAC BOT — Drupal Integration & Multi‑Tenant Chat Widget

End‑to‑end implementation guide for embedding the MOSDAC Graph‑RAG chatbot as a
single‑`<script>` widget on a Drupal site, with **per‑user chat history for
Keycloak‑SSO users** and **ephemeral sessions for anonymous visitors**.

Everything is driven by `.env` and the widget's `<script>` config — **no hardcoding**.
Re‑using the bot on another Drupal site is a configuration change, not a code change.

---

## 1. Overview & goals

| Capability | How it works |
|---|---|
| Embeddable widget | One `<script>` tag drops a floating chat bubble + panel onto any page. |
| ISRO branding | Logo + "MOSDAC BOT" title come from widget config (`logoUrl`, `botTitle`). |
| Per‑user history | SSO users get their own conversations (sidebar), persisted in SQLite. |
| Anonymous use | No login → ephemeral chat (existing TTL session store), **no sidebar**, nothing written to the conversation DB. |
| Short titles | The first message of a new conversation gets a 4‑5 word LLM title in the background. |
| Adapter‑pattern auth | JWT claim names live in `.env`; the code never hardcodes `sub`/`preferred_username`/`email`. |
| Multi‑site | Add the origin to `CHAT_API_ALLOWED_ORIGINS`, point the script‑tag config, set nginx `server_name`. Done. |

---

## 2. Architecture

```
 Browser (Drupal page)
   │  <script src=".../mosdac-chat-widget.js">
   │  Authorization: Bearer <Keycloak access token>   (SSO users only)
   ▼
 ┌───────────────── nginx (reverse proxy) ─────────────────┐
 │  /static/   → widget JS/CSS assets (filesystem)         │
 │  /chatapi/  → proxy_pass FastAPI :8000 (Authorization   │
 │              header forwarded verbatim, SSE‑friendly)    │
 └─────────────────────────┬───────────────────────────────┘
                           ▼
 ┌──────────────────── FastAPI (chat_api) ─────────────────┐
 │  auth.py        verify JWT (JWKS) → normalize_user_data  │
 │  routes.py      /chat, /chat/stream, /conversations*     │
 │  service.py     guardrails L1–L5 + hybrid RAG + persist  │
 │  db/ (SQLite)   conversations + messages (per user)      │
 │  session.py     ephemeral history for anonymous users    │
 │  titler.py      background 4‑5 word title                 │
 └───┬───────────────┬───────────────┬─────────────────────┘
     ▼               ▼               ▼
  Keycloak JWKS   Graph‑RAG core   Tabby LLM / Ollama / Neo4j / Chroma
```

The graph‑RAG answer pipeline (hybrid retrieval + L1–L5 guardrails) is **unchanged**.
This feature adds three layers around it: per‑user **auth**, a conversation **DB**, and
the widget **UI** with a history sidebar.

---

## 3. Authentication flow (adapter pattern)

```
Drupal (logged in via Keycloak OIDC)
  └─ exposes the access token to the page (JS var / <meta name="kc-token">)
       └─ widget getToken() reads it → Authorization: Bearer <jwt>
            └─ FastAPI dependency:
                 get_current_user / get_optional_user   (chat_api/auth.py)
                   └─ decode_token(jwt)         # verify signature via JWKS, exp/aud/iss
                        └─ normalize_user_data(claims)   # THE ADAPTER
                             # reads settings.jwt_field_id / _username / _email
                             └─ NormalizedUser(id, username, email)
```

**Why the adapter matters.** Route handlers and the DB layer only ever see
`NormalizedUser`. They never import `jwt` and never reference a Keycloak claim name.
If the government portal issues custom claims (e.g. `user_id` instead of `sub`), you
change `JWT_FIELD_ID` in `.env` — no code edit. See
[chat_api/auth.py](chat_api/auth.py) (`normalize_user_data`, `get_current_user`,
`get_optional_user`).

- **Anonymous** request (no/empty token): `get_optional_user` returns `None` →
  `/chat` runs the ephemeral path, returns `conversation_id: null`, writes nothing to
  the DB. The `/conversations*` endpoints require `get_current_user` (401/503 for anon).
- **A malformed token is never silently downgraded** to anonymous — it returns 401.

**Library:** `PyJWT[crypto]` (native `PyJWKClient` caches signing keys per `kid`).
Imported lazily, so deployments with `CHAT_API_AUTH_ENABLED=false` don't need it.

---

## 4. Database schema (SQLite, per‑user)

Stored via the stdlib `sqlite3` module — no extra dependency. The whole service is
synchronous (FastAPI runs sync handlers in a threadpool), so a synchronous repository
is the natural, contention‑free fit for the "small database" of chat history.

**`conversations`**

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `user_id` | TEXT | the `NormalizedUser.id` (ownership key) |
| `title` | TEXT | "New chat" → replaced by the LLM title |
| `created_at` | TEXT | ISO‑8601 UTC |
| `updated_at` | TEXT | bumped on each new message |

Index: `(user_id, updated_at DESC)` → fast sidebar listing.

**`messages`**

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `conversation_id` | TEXT FK | → `conversations.id` `ON DELETE CASCADE` |
| `role` | TEXT | `user` \| `assistant` |
| `content` | TEXT | PII‑redacted user text / assistant answer |
| `created_at` | TEXT | ISO‑8601 UTC |

Index: `(conversation_id, created_at)`.

**Ownership invariant (anti‑IDOR).** Every repository method that targets a specific
conversation takes **both** `user_id` and `conversation_id` and filters on both (messages
join through the owning conversation). There is no "fetch by id alone" method, so a forged
id belonging to another user always resolves to `None`/no‑op → the API answers `404`.
See [chat_api/db/sqlite_repo.py](chat_api/db/sqlite_repo.py) and
[chat_api/db/repository.py](chat_api/db/repository.py).

**Anonymous data is never persisted** — it lives only in the TTL session store
(`chat_api/session.py`). To disable persistence entirely set `CHAT_API_CONV_STORE=none`.

---

## 5. Endpoint reference

| Method | Path | Auth | Body / Params | Returns |
|---|---|---|---|---|
| POST | `/chat` | optional (`_require_api_key` + `get_optional_user`) | `{session_id, message, conversation_id?, screenshot_base64?}` | `{answer, session_id, conversation_id, citations, grounded, refused}` |
| POST | `/chat/stream` | optional | same | SSE `token` events + one `final` event (incl. `conversation_id`) |
| GET | `/conversations` | required (`get_current_user`) | — | `[{id, title, created_at, updated_at}]` |
| GET | `/conversations/{id}/messages` | required | — | `{id, title, messages:[{role, content, created_at}]}` (404 if not owned) |
| DELETE | `/conversations/{id}` | required | — | `{deleted: id}` (404 if not owned) |
| GET | `/config` | none | — | widget config (title, screenshot support) |
| GET | `/health`, `/ready`, `/metrics` | none | — | ops probes |

- `conversation_id = null` (or omitted) on `/chat` starts a **new** conversation; the
  response carries the new id, which the widget sends on follow‑ups.
- For anonymous callers `conversation_id` is always `null`.

See [chat_api/routes.py](chat_api/routes.py) and [chat_api/models.py](chat_api/models.py).

### Short‑title summarization
On the **first** message of a **new** conversation, the answer is returned immediately and
a FastAPI `BackgroundTask` then asks the LLM *"Summarize this user query into a short 4‑5
word title"* and stores it on the conversation (`chat_api/titler.py`). It reuses the shared
`get_llm()`/`llm_slot()`; failures are swallowed (title stays "New chat"). The widget
refreshes the sidebar shortly after sending so the title appears.

---

## 6. Widget embed (single `<script>` tag)

The generic widget is [static/graph-rag-chat-widget.js](static/graph-rag-chat-widget.js);
[static/mosdac-chat-widget.js](static/mosdac-chat-widget.js) is a thin MOSDAC‑branded shim.
Drop this into the Drupal theme (e.g. `html.html.twig`, before `</body>`):

```html
<!-- Expose the Keycloak access token to the page (Drupal/OIDC module, server-side).
     Anonymous pages simply omit this; the widget then runs ephemeral, no sidebar. -->
<meta name="kc-token" content="{{ keycloak_access_token }}">

<script>
  window.GRAPH_RAG_CHAT_CONFIG = {
    apiBase:  "/chatapi",                     // nginx route to FastAPI — change per site
    botTitle: "MOSDAC BOT",
    logoUrl:  "/sites/default/files/isro-logo.png",
    greeting: "Hey User, what's on your mind today?",
    // How the widget gets the SSO token (adapter on the frontend side):
    getToken: function () {
      return (window.KC_TOKEN ||
        (document.querySelector('meta[name=kc-token]') || {}).content || "");
    }
  };
</script>
<script src="/static/html2canvas.min.js"></script>     <!-- optional: screenshots -->
<script src="/static/mosdac-chat-widget.js"></script>
```

Nothing here is mandatory except `apiBase`. All branding/colours/token‑source are config.

---

## 7. nginx configuration

See [deployments/nginx/mosdac.conf](deployments/nginx/mosdac.conf). Key points:

```nginx
server {
    listen 80;
    server_name my-drupal-site.ddev.site localhost;   # ← change per site

    location /static/ {
        alias /srv/mosdac/static/;                     # ← widget assets
        add_header Cache-Control "public, max-age=300";
    }

    location /chatapi/ {
        proxy_pass http://chat_api:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Authorization $http_authorization;   # SSO passthrough
        proxy_buffering off;                                    # SSE streaming
        proxy_read_timeout 300s;
    }
}
```

Set `CHAT_API_TRUST_FORWARDED_FOR=true` so per‑IP rate limiting keys on the real client
(the backend reads `X-Forwarded-For` only when this flag is on). The backend verifies the
JWT itself via JWKS, so no `auth_request` is required; an optional `auth_request` to
Keycloak can pre‑reject expired tokens at the edge.

---

## 8. Environment variable reference (new)

| Variable | Default | Purpose |
|---|---|---|
| `JWT_FIELD_ID` | `sub` | Claim → user id (also `CHAT_API_JWT_FIELD_ID`) |
| `JWT_FIELD_USERNAME` | `preferred_username` | Claim → username |
| `JWT_FIELD_EMAIL` | `email` | Claim → email |
| `CHAT_API_AUTH_ENABLED` | `false` | Master switch for SSO/per‑user history |
| `CHAT_API_KEYCLOAK_ISSUER` | `""` | Realm issuer (JWKS URL derived from it) |
| `CHAT_API_KEYCLOAK_JWKS_URL` | `""` | Explicit JWKS URL (optional) |
| `CHAT_API_KEYCLOAK_AUDIENCE` | `""` | Required `aud` (comma list; empty = skip) |
| `CHAT_API_JWT_ALGORITHMS` | `RS256` | Signing‑alg allow‑list |
| `CHAT_API_JWKS_CACHE_SECONDS` | `3600` | JWKS key cache TTL |
| `CHAT_API_CONV_STORE` | `sqlite` | `sqlite` \| `none` |
| `CHAT_API_SQLITE_PATH` | `./conversations.db` | SQLite file path |
| `CHAT_API_ALLOWED_ORIGINS` | (CSV) | CORS allow‑list — **add each Drupal origin here** |

All claim‑mapping defaults are standard Keycloak; change only in `.env` for a custom IdP.

---

## 9. Multi‑site reuse (no code changes)

To embed on another Drupal site:

1. **Backend:** add the site's browser origin to `CHAT_API_ALLOWED_ORIGINS` (consumed by
   the existing `CORSMiddleware` in `chat_api/main.py`).
2. **Page:** set the `<script>` tag's `GRAPH_RAG_CHAT_CONFIG` (`apiBase`, `logoUrl`,
   `botTitle`, `getToken`).
3. **nginx:** set `server_name` + the `/static` `alias`.

The same container image and the same widget file serve every site.

---

## 10. Security notes

- **No IDOR:** ownership filtered in SQL on every conversation/message access;
  non‑owned ids return 404 (not 403, to avoid existence disclosure). Unit‑tested in
  `tests/test_conversation_repo.py` and `tests/test_chat_api.py`.
- **JWT integrity:** signature verified against JWKS; `exp`/`aud`/`iss` enforced;
  algorithm allow‑list blocks `alg:none` and HS/RS confusion. Forged/expired → 401.
- **CORS:** exact‑match allow‑list per deployment, `allow_credentials=true`, never wildcard.
- **Anonymous isolation:** anon requests never receive a `conversation_id`, never query
  the DB, and the sidebar is hidden client‑side; ephemeral history uses the existing
  TTL/LRU session store.
- **Retained controls:** slowapi per‑IP rate limiting, body‑size cap, OWASP security
  headers, pydantic input validation (incl. UUID checks on `session_id`/`conversation_id`),
  L1–L5 guardrails, optional shared `X-API-Key`.

---

## 11. Deployment steps

1. `pip install -r requirement.txt` (adds `PyJWT[crypto]`; SQLite is stdlib).
2. Configure Keycloak: a client for the Drupal site; note the realm issuer
   (`CHAT_API_KEYCLOAK_ISSUER`) and, if you pin one, the audience.
3. Set the new env vars (§8). For local/anonymous testing keep
   `CHAT_API_AUTH_ENABLED=false`.
4. Start FastAPI (`uvicorn chat_api.main:app --port 8000`). The SQLite schema is created
   automatically on first boot.
5. Deploy nginx (§7); copy `static/` to the `alias` path.
6. Add the widget `<script>` to the Drupal theme (§6) and the origin to
   `CHAT_API_ALLOWED_ORIGINS`.

---

## 12. Testing & verification

**Automated** (mocked; no live Keycloak/LLM/DB):

```
python -m pytest tests/test_auth.py tests/test_conversation_repo.py \
                 tests/test_titler.py tests/test_chat_api.py
```

Covers: the adapter (incl. a config‑driven claim‑name swap proving no hardcoding),
JWKS decode failures → 401, ownership filtering / IDOR → 404, background titling
fail‑safe, anonymous backward‑compat (`conversation_id: null`), and the full
`/conversations*` lifecycle. The whole suite stays green (`python -m pytest`).

**Manual (SSO end‑to‑end):**
1. Anonymous: load the Drupal page with no token → bubble opens, no sidebar, chat works,
   no rows in `conversations.db`.
2. SSO: obtain a Keycloak access token, inject via `window.KC_TOKEN`, reload → sidebar
   lists conversations; send a message → a 4‑5 word title appears after ~1 s; click a
   past conversation → it loads; refresh → history persists.
3. IDOR probe: with user A's token, `GET /chatapi/conversations/{B's id}/messages` → 404.
4. CORS: a request from an origin not in `CHAT_API_ALLOWED_ORIGINS` is blocked.
