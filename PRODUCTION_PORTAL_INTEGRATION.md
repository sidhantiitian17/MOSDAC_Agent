# MOSDAC BOT — Production Portal Integration Guide

**Read me to put the chatbot on the *real* government portal.**

You already proved the whole thing works on a **test Drupal site** (DDEV) with a
**local Keycloak** for Single Sign‑On (SSO). This guide takes that exact same setup
and moves it to the **real portal**, step by step, explaining **every file, every
setting, and every line of Nginx** in the simplest possible words.

> **One‑sentence summary:** *Keycloak signs a login token → the portal puts that
> token on the page → the chat widget sends it to our backend → Nginx passes it
> through → the backend checks it and gives the user their name and saved chats.*

Nothing in the code is hard‑coded. Moving to a new portal is **configuration**, not
new programming. This guide shows you exactly which knobs to turn.

---

## Table of contents

0. [How to read this guide](#0-how-to-read-this-guide)
1. [The 5‑year‑old explanation](#1-the-5-year-old-explanation)
2. [The full map (architecture)](#2-the-full-map-architecture)
3. [The cast of characters (every file)](#3-the-cast-of-characters-every-file)
4. [TEST vs REAL — what actually changes](#4-test-vs-real--what-actually-changes)
5. [Part A — The backend brain (Docker + `.env`)](#5-part-a--the-backend-brain-docker--env)
6. [Part B — Keycloak SSO for the real portal](#6-part-b--keycloak-sso-for-the-real-portal)
7. [Part C — The token bridge on the portal](#7-part-c--the-token-bridge-on-the-portal)
8. [Part D — The chat widget (the `<script>` tag)](#8-part-d--the-chat-widget-the-script-tag)
9. [Part E — Nginx, the traffic cop (full config, line by line)](#9-part-e--nginx-the-traffic-cop-full-config-line-by-line)
10. [The request lifecycle, step by step](#10-the-request-lifecycle-step-by-step)
11. [CORS explained simply](#11-cors-explained-simply)
12. [Rate limiting and the real‑client‑IP trick](#12-rate-limiting-and-the-real-client-ip-trick)
13. [Custom token payloads (gov portal claims)](#13-custom-token-payloads-gov-portal-claims)
14. [The API endpoints (reference table)](#14-the-api-endpoints-reference-table)
15. [Security checklist (everything)](#15-security-checklist-everything)
16. [The production deployment checklist (do this in order)](#16-the-production-deployment-checklist-do-this-in-order)
17. [Verification — smoke tests](#17-verification--smoke-tests)
18. [Troubleshooting (big table)](#18-troubleshooting-big-table)
19. [Appendix A — Every environment variable](#19-appendix-a--every-environment-variable)
20. [Appendix B — Every file and where it lives](#20-appendix-b--every-file-and-where-it-lives)

---

## 0. How to read this guide

There are **three machines / roles** in this story. Keep them straight and
everything else is easy:

| Nickname | What it really is | Who runs it |
|---|---|---|
| **The Portal** | The real government website your visitors open (the MOSDAC portal). | The portal team |
| **The Guard (Keycloak)** | The login server that checks passwords and hands out signed “ID cards” (tokens). | The SSO / IT team |
| **The Brain (our backend)** | Our FastAPI chatbot + Nginx + databases, running in Docker. | You |

The chat **widget** is a tiny piece of JavaScript that lives **inside the Portal’s
pages** but **talks to the Brain**. Nginx sits in front of the Brain like a
**traffic cop / receptionist**.

Throughout, **“like you’re five”** explanations are in plain text; the **exact
production values** follow right after in code blocks. Read the plain text to
understand *why*, copy the code blocks to actually *do it*.

---

## 1. The 5‑year‑old explanation

Imagine a **library** (the Portal). Inside the library there is a little **help
desk robot** (the chat widget). When you ask the robot a question, the robot runs
to a **back room** (the Brain) where a very smart helper reads books and writes you
an answer.

* If you are a **stranger** who just walked in, the robot still helps you, but it
  **doesn’t remember you** after you leave. (This is an *anonymous* chat.)

* If you **showed your library card** at the front door (you **logged in** with
  SSO), the **guard** (Keycloak) gave you a special **wristband** (a token). Now
  the robot can say **“Hi, Priya!”** and **remember every chat you’ve had**,
  because it shows your wristband to the back room and the back room keeps a
  notebook with your name on it.

* The **wristband can’t be faked.** The guard signs it with invisible ink. The
  back room has the guard’s special lamp (the **public keys / JWKS**) and checks
  every wristband under the lamp. A fake or expired wristband is thrown out.

* In front of the back room there is a **receptionist** (Nginx). She:
  * hands out the robot’s instruction sheets (the widget JavaScript files),
  * carries your question to the smart helper and brings the answer back,
  * **passes your wristband through untouched** so the helper can read it,
  * and writes down **which door you came from** so nobody can pretend to be a
    huge crowd and tire out the helper (rate limiting).

That’s the whole system. The rest of this document is just **how to set up each
of those people for the real library** instead of the practice one.

---

## 2. The full map (architecture)

```
                            ┌──────────────────────────────────┐
                            │   KEYCLOAK  (the Guard / IdP)     │
                            │   realm: mosdac                   │
                            │   signs JWT access tokens          │
                            └───────────────┬──────────────────┘
        (1) user clicks "Login"             │  server-to-server OIDC code exchange
            on the portal                   │  (browser never talks to Keycloak directly)
                                            ▼
  Browser  ───────────────►  THE PORTAL  (HTTPS, e.g. https://mosdac.gov.in)
  (widget UI)                 - logs the user in via its OIDC module
        ▲                     - STORES the access token in the user's session
        │ (3) widget reads     - the theme/template prints the token onto the page:
        │     the token from        <meta name="kc-token" content="<JWT>">
        │     <meta kc-token>  - loads the widget <script> from /static/...
        │
        │ (4) widget calls the API:  Authorization: Bearer <JWT>
        ▼
 ┌──────────────────────  NGINX  (the receptionist / reverse proxy)  ─────────────┐
 │  listen 443 ssl       (TLS terminates here — HTTPS to the world)               │
 │  /static/   →  serves widget JS/CSS/logo from disk                              │
 │  /chatapi/  →  proxy_pass to the FastAPI container on :8000                      │
 │                · forwards the Authorization header UNCHANGED (SSO passthrough)   │
 │                · sets X-Real-IP = real client IP (anti-spoof rate limiting)      │
 │                · proxy_buffering off  → streaming answers (SSE) flow live        │
 └───────────────────────────────────────┬─────────────────────────────────────────┘
                                          ▼
 ┌────────────────────────  FastAPI  (chat_api — the Brain's front door)  ─────────┐
 │  auth.py     verify JWT signature against Keycloak's public keys (JWKS),         │
 │              check exp / iss / aud, then map claims → NormalizedUser             │
 │  routes.py   /chat, /chat/stream, /conversations*, /me, /config, /health         │
 │  service.py  guardrails L1–L5  +  hybrid Graph-RAG retrieval  +  persist history │
 │  db/ (SQLite or Postgres)   per-user conversations + messages                    │
 │  session.py  ephemeral history for anonymous visitors (TTL, no DB)               │
 │  titler.py   names each new chat with a 4–5 word title in the background         │
 └───┬──────────────┬──────────────┬─────────────────┬──────────────┬──────────────┘
     ▼              ▼              ▼                 ▼              ▼
  Keycloak       Tabby ML       Ollama            Neo4j         Redis
  JWKS keys      (the LLM)      (embeddings,      (knowledge    (shared session
  (verify        OpenAI-API     bge-large)        graph)        store, multi-replica)
   tokens)       compatible
                                     +  ChromaDB (vector store, on disk)
```

The **answer pipeline** (retrieval + L1–L5 guardrails + the LLM) is the **same**
code that already works. SSO only wraps **three thin layers** around it: **auth**
(check the token), a **conversation database** (save history per user), and the
**widget UI** (the sidebar + greeting). You are not changing the brain — you are
giving it a front door and a receptionist.

---

## 3. The cast of characters (every file)

This is **every file** that touches integration, grouped by **where it lives**.

### 3.1 In THIS repo — the backend (`chat_api/`)

| File | In one sentence | What to know for production |
|---|---|---|
| [chat_api/config.py](chat_api/config.py) | The big box of settings; reads **everything** from `.env`. | Every knob below (issuer, claim names, CORS, login URL…) is defined here with an `.env` override. **No claim name or URL is hard‑coded.** |
| [chat_api/auth.py](chat_api/auth.py) | The bouncer + translator. `decode_token()` checks the wristband under the lamp; `normalize_user_data()` reads the name/email using the claim names from `.env`. | The rest of the app only ever sees a clean `NormalizedUser(id, username, email)`. Swapping IdPs = `.env` edit. |
| [chat_api/routes.py](chat_api/routes.py) | All the HTTP doors: `/chat`, `/chat/stream`, `/conversations*`, `/me`, `/config`, `/health`, `/ready`, `/metrics`, `/reload`. | `/me` powers the “Hi, Priya” greeting; `/config` tells the widget whether auth is on and where “Sign in” goes. |
| [chat_api/service.py](chat_api/service.py) | The actual work: guardrails + retrieval + LLM + saving history. | Unchanged by SSO. `chat()` = anonymous path, `chat_authenticated()` = logged‑in path. |
| [chat_api/session.py](chat_api/session.py) | Short‑term memory for **anonymous** visitors (kept in RAM/Redis, expires). | Anonymous chats are **never** written to the conversation DB. |
| [chat_api/db/](chat_api/db/) | Per‑user chat history (SQLite by default, Postgres for multi‑replica). | Every query is filtered by `user_id` → **one user can never read another’s chats** (anti‑IDOR). |
| [chat_api/titler.py](chat_api/titler.py) | Gives a brand‑new chat a short 4–5 word title in the background. | Best‑effort; if it fails the title just stays “New chat”. |
| [chat_api/main.py](chat_api/main.py) | `create_app()` — wires CORS, rate limiting, security headers, routes. | CORS allow‑list and the body‑size cap come from `.env`. |

### 3.2 In THIS repo — the widget (`static/`)

| File | In one sentence |
|---|---|
| [static/graph-rag-chat-widget.js](static/graph-rag-chat-widget.js) | The **real** widget. Portal‑agnostic. It only knows two abstract things: “call `getToken()` to get a token” and “go to `loginUrl` to sign in.” It never reads the token itself — it asks `GET /me` for your name. |
| [static/mosdac-chat-widget.js](static/mosdac-chat-widget.js) | A 40‑line **MOSDAC shim**: sets MOSDAC defaults (title, logo, the default `getToken` that reads `window.KC_TOKEN` or `<meta name="kc-token">`), then loads the real widget. **Page config always wins**, so re‑branding is config‑only. |
| [static/isro-logo.png](static/isro-logo.png) | The logo shown in the widget header. Swap per portal. |
| [static/vendor/katex/](static/vendor/katex/) | Vendored math typesetting (so formulas render **offline**, no CDN). |
| [static/sso-demo.html](static/sso-demo.html) | A **test harness only** — it pretends to be a portal using browser‑side `keycloak-js`. Great for testing the widget without the portal. **Not used in production.** |

### 3.3 In THIS repo — deployment

| File | In one sentence |
|---|---|
| [deployments/nginx/mosdac.conf](deployments/nginx/mosdac.conf) | The Nginx site config (the test/HTTP version). [Part E](#9-part-e--nginx-the-traffic-cop-full-config-line-by-line) gives you the **production HTTPS** version. |
| [deployments/widget-snippets/mosdac-drupal.html](deployments/widget-snippets/mosdac-drupal.html) | Copy‑paste embed snippet for a Drupal theme, with the token‑bridge hook. |
| [docker-compose.yml](docker-compose.yml) | Starts Neo4j + Redis + the FastAPI `chat_api` container together. |
| [Dockerfile.api](Dockerfile.api) | Builds the `chat_api` image (offline‑ready: Docling models baked in, runs as non‑root user `appuser`). |
| [docker-entrypoint.sh](docker-entrypoint.sh) | Fixes file ownership on the writable mounts, then drops to the non‑root user. |
| [.env](.env) | **The single place** you turn everything on and point it at the real portal. |

### 3.4 OUTSIDE this repo — on the PORTAL (e.g. a Drupal theme)

| File (example for Drupal) | In one sentence |
|---|---|
| `<theme>.theme` → `*_preprocess_html()` | **The token bridge.** For a logged‑in user it pulls the Keycloak access token the OIDC module stored in the session and hands it to the page template. Adds a per‑user cache rule so a token can never leak to another user. |
| `templates/.../html.html.twig` | Prints `<meta name="kc-token" …>` **only for logged‑in users**, sets the widget config block, and loads the widget `<script>`. |

> **Single source of truth:** the widget JS is served by **our** Nginx under
> `/static/…`. There are **no copies** inside the portal’s code. Fixing
> `static/graph-rag-chat-widget.js` updates every portal at once.

---

## 4. TEST vs REAL — what actually changes

You already did the hard part on the test site. Here is the **only** list of things
that are different in production. **This table is the heart of the migration.**

| Thing | Test (Drupal DDEV) | **Real portal (production)** | Where you change it |
|---|---|---|---|
| Portal address | `https://my-site.ddev.site` | `https://mosdac.gov.in` (the real domain) | Nginx `server_name`, CORS, Keycloak redirect URIs |
| Keycloak address | `http://192.168.1.36:8081` (HTTP, LAN IP) | `https://sso.mosdac.gov.in` (**HTTPS**) | `.env` issuer, Keycloak hostname |
| Keycloak realm | `master` (during testing) | **`mosdac`** (a dedicated realm) | Keycloak + `.env` issuer |
| Token `iss` (issuer) | `http://192.168.1.36:8081/realms/master` | `https://sso.mosdac.gov.in/realms/mosdac` | `CHAT_API_KEYCLOAK_ISSUER` **must equal this exactly** |
| Transport | HTTP everywhere (localhost is exempt) | **HTTPS everywhere** (TLS certs on Nginx) | Nginx `listen 443 ssl`, real certs |
| Mixed‑content problem | Real (HTTPS page → HTTP Keycloak) → must bridge server‑side | **Gone** if Keycloak is HTTPS — but **keep the server‑side bridge anyway** (it’s simpler and safer) | Portal theme |
| Backend reachable at | host `0.0.0.0:8000` via DDEV proxy | `chat_api:8000` on the Docker network, fronted by Nginx | Nginx `proxy_pass` |
| Secrets | weak/dev (`please-change-me`) | **strong, unique** (Neo4j, Redis, admin token) | `.env` |
| CORS origins | `localhost`, ddev site | the **real HTTPS origin(s)** only | `CHAT_API_ALLOWED_ORIGINS` |
| Token lifespan | raised to 1h for convenience | a sensible value (e.g. 15–60 min) the portal can refresh | Keycloak realm settings |

**Everything else stays the same.** Same widget file, same backend image, same
`docker-compose.yml`. That is the whole point of the design.

> 💡 **Big simplification in production:** if the widget pages are served from the
> **same domain** as `/chatapi` (because Nginx fronts both), then the browser sees
> the API as **same‑origin** and **CORS is never even triggered**. CORS only
> matters if the API lives on a *different* domain than the portal. See
> [§11](#11-cors-explained-simply).

---

## 5. Part A — The backend brain (Docker + `.env`)

### 5.1 What runs

The reference production stack is **one Docker Compose file** that starts three
containers (see [docker-compose.yml](docker-compose.yml)):

1. **neo4j** — the knowledge graph. Bound to **loopback only** (`127.0.0.1`), so it
   is never reachable from the network. Auth is **on**; the password comes from
   `.env`.
2. **redis** — the shared session store (so anonymous sessions survive restarts and
   could be shared across replicas). Password‑protected, **not** published to the host.
3. **chat_api** — our FastAPI app. This is the only container that publishes a port
   (`8000`), and even that should sit **behind Nginx** in production.

Two more services run **outside** compose, on the host (or a LAN box):

* **Tabby ML** — the LLM (OpenAI‑compatible API on `:8080`).
* **Ollama** — embeddings (`bge-large` on `:11434`).

Inside the container, `localhost` means *the container itself*, so compose
rewrites those two to `host.docker.internal` automatically (see the `environment:`
block in [docker-compose.yml](docker-compose.yml)). For ISRO’s air‑gapped LAN you’d
point them at a fixed LAN IP instead.

### 5.2 The production `.env` — the SSO block, explained line by line

Start from the template and edit:

```bash
cp deployments/generic.env .env   # then edit the values below
```

The block that turns on SSO for the **real portal** (this is the production version
of what [set_sso.md](set_sso.md) shows for the test site):

```ini
# ── Turn SSO on ──────────────────────────────────────────────────────────────
CHAT_API_AUTH_ENABLED=true

# ── Point at the REAL Keycloak realm ─────────────────────────────────────────
# This MUST be byte-for-byte equal to the `iss` claim inside the tokens Keycloak
# issues. In production it is HTTPS and the dedicated `mosdac` realm:
CHAT_API_KEYCLOAK_ISSUER=https://sso.mosdac.gov.in/realms/mosdac

# Optional: pin the audience. Leave EMPTY to skip the check (many Keycloak setups
# don't put an `aud` on access tokens). If you do pin it, it must match the token.
CHAT_API_KEYCLOAK_AUDIENCE=

# Where the widget's "Sign in" button sends an anonymous user. For a Drupal portal
# with the OpenID Connect module this is the OIDC login route:
CHAT_API_LOGIN_URL=/user/login/openid_connect

# ── Claim mapping (which field in the token holds what) ──────────────────────
# Defaults are the STANDARD Keycloak claim names. Only change these if the gov
# portal issues custom/nested claims (see §13).
JWT_FIELD_ID=sub
JWT_FIELD_USERNAME=preferred_username
JWT_FIELD_EMAIL=email

# ── Signing algorithm allow-list (blocks alg:none / HS-RS confusion attacks) ──
CHAT_API_JWT_ALGORITHMS=RS256

# ── How long to cache Keycloak's public keys before re-fetching ──────────────
CHAT_API_JWKS_CACHE_SECONDS=3600
```

What each line **does**, plainly:

* `CHAT_API_AUTH_ENABLED=true` — the master switch. `false` (the default) makes
  the bot anonymous‑only and the `/conversations` doors return **503**. Turn it on
  **only after** the issuer is set, or every token is rejected.
* `CHAT_API_KEYCLOAK_ISSUER` — the **address of the guard’s office**. The backend
  derives the public‑keys URL from it automatically as
  `{issuer}/protocol/openid-connect/certs` (see `effective_jwks_url()` in
  [chat_api/config.py](chat_api/config.py)). **The #1 cause of “logged in but bot
  says ‘Hey User’” is this not matching the token’s `iss` exactly** (watch
  `http` vs `https`, trailing slash, `localhost` vs real host, realm name).
* `CHAT_API_KEYCLOAK_AUDIENCE` — optional extra check that the token was meant for
  us. Empty = skip. Only set it if Keycloak actually stamps an `aud`.
* `CHAT_API_LOGIN_URL` — handed to the widget through `GET /config`, so the
  **front‑end has zero hard‑coding**. Change the login route in one place.
* `JWT_FIELD_*` — the **claim names**. Standard Keycloak = `sub` /
  `preferred_username` / `email`. A custom IdP only edits these three (they support
  nested paths and fallbacks — see [§13](#13-custom-token-payloads-gov-portal-claims)).

### 5.3 The production `.env` — the rest of what matters

```ini
# ── CORS: list the REAL portal origin(s). Exact match incl. scheme + host + port. ─
# If the widget is served same-origin via Nginx you can keep this tight; list any
# OTHER origin that will embed the widget cross-domain.
CHAT_API_ALLOWED_ORIGINS=https://mosdac.gov.in,https://www.mosdac.gov.in

# DELETE must stay in the methods list — the widget calls DELETE /conversations/{id}.
CHAT_API_ALLOWED_METHODS=GET,POST,DELETE,OPTIONS
CHAT_API_ALLOWED_HEADERS=Content-Type,Authorization,Accept

# ── Behind Nginx: trust the proxy's real-client-IP header (see §12) ──────────
# REQUIRED behind the bundled Nginx, or EVERY client shares one rate-limit bucket.
CHAT_API_TRUST_FORWARDED_FOR=true

# ── Per-user history store ───────────────────────────────────────────────────
# Single container (the reference stack): sqlite on a durable volume is correct.
# Multi-replica: set conv_store=postgres + CHAT_API_POSTGRES_DSN (sqlite is refused).
CHAT_API_CONV_STORE=sqlite
# (compose pins CHAT_API_SQLITE_PATH=/app/data/conversations.db on a named volume)

# ── Sessions (anonymous, ephemeral) ──────────────────────────────────────────
CHAT_API_SESSION_BACKEND=redis           # survives restarts; compose injects the URL
CHAT_API_SESSION_TTL_SECONDS=86400        # drop idle anon history after 24h

# ── Abuse / DoS controls ─────────────────────────────────────────────────────
CHAT_API_REQUIRE_RATE_LIMIT=true          # fail CLOSED if the limiter can't attach
CHAT_API_MAX_REQUEST_BYTES=12582912       # ~12 MB hard body cap (matches Nginx below)
CHAT_API_MAX_MESSAGE_CHARS=8000

# ── Optional shared API key for /chat (defense in depth) ─────────────────────
# Empty = open endpoint (normal for a public portal). If set, the widget must send
# it as X-API-Key / Bearer. For a PUBLIC chatbot leave empty; rely on rate limits.
CHAT_API_API_KEY=

# ── Operator token guarding /reload and /metrics (keep DISTINCT from api_key) ─
CHAT_API_ADMIN_TOKEN=<long-random-operator-token>

# ── Screenshots: OFF unless a real vision model is wired (else 8 MB uploads to a
#    text-only model). Turn on ONLY together with CHAT_API_VISION_MODEL. ────────
CHAT_API_ENABLE_SCREENSHOT=false

# ── Secrets for the data stores (STRONG + UNIQUE in production) ───────────────
NEO4J_PASSWORD=<strong-unique-password>
REDIS_PASSWORD=<strong-unique-password>
```

> ⚠️ **Neo4j password gotcha:** `NEO4J_AUTH` only sets the password when the
> `./neo4j_data` volume is **created fresh**. If you already ran the graph with a
> weak/`none` password, the old setting **sticks**. Either recreate `./neo4j_data`
> or run the `neo4j-admin dbms set-initial-password` command noted in
> [docker-compose.yml](docker-compose.yml).

### 5.4 Start it and verify

```bash
docker compose up -d --build
docker compose ps                  # all three healthy?
curl -s http://localhost:8000/health   | python -m json.tool
curl -s http://localhost:8000/ready    | python -m json.tool   # 200 only when deps are up
curl -s http://localhost:8000/config   | python -m json.tool   # must show "auth_enabled": true
```

`/config` showing `"auth_enabled": true` and your `login_url` means the backend is
ready for SSO. (`/ready` returns **503** until embedder + Chroma + Neo4j actually
answer — that is correct; the load balancer should not send traffic before then.)

---

## 6. Part B — Keycloak SSO for the real portal

This is what the **SSO/IT team** sets up in the real Keycloak. You already did the
equivalent in the test Keycloak; here is the production version.

### 6.1 Create a dedicated realm

* Realm name: **`mosdac`** (not `master`). The `master` realm is for administering
  Keycloak itself — never use it for end users in production.
* This makes the issuer `https://sso.mosdac.gov.in/realms/mosdac`. **Write this
  down** — it must match `CHAT_API_KEYCLOAK_ISSUER` exactly.

### 6.2 Create the portal client (confidential)

The **portal’s server** logs users in, so the client is **confidential** (it has a
secret that only the server knows — never the browser).

* **Client ID:** e.g. `mosdac-portal`
* **Client type / Access type:** **Confidential** (Client authentication **On**).
* **Standard Flow** enabled (Authorization Code Flow).
* **Valid Redirect URIs:** the real portal callback(s), e.g.
  `https://mosdac.gov.in/openid-connect/*` (Drupal OIDC module) — **HTTPS, real
  domain**. No wildcards broader than necessary.
* **Web Origins:** `https://mosdac.gov.in` (so CORS from Keycloak is correct).
* Copy the generated **client secret** into the portal’s OIDC module config (not
  into our backend — our backend never needs the secret; it only verifies tokens).

> A **second, public** client (like the test `mosdac-chat`) is only needed for the
> standalone `static/sso-demo.html` harness. The real portal does **not** use it.

### 6.3 Token lifespan

Keycloak access tokens are **short‑lived** on purpose. In the test we bumped the
lifespan to 1 hour because the bridged token isn’t refreshed on every page load.

In production, prefer a **modest access‑token lifespan** (e.g. **15–60 min**) **and
let the portal refresh it** (the OIDC module holds the refresh token server‑side
and renews the access token before printing it). Set this under **Realm settings →
Tokens → Access Token Lifespan**.

The trade‑off, simply: **longer lifespan = fewer “please sign in again” surprises
in the widget, but a stolen token is usable for longer.** Pick what your security
policy allows; the portal refreshing the token is the clean answer.

### 6.4 The golden rule

> **The token’s `iss` claim and `CHAT_API_KEYCLOAK_ISSUER` must be identical
> strings.** If they differ by even a slash or `http`/`https`, every token is
> rejected with 401 and the widget falls back to the Sign‑in card. When in doubt,
> log in, copy the token from the page, paste it into a JWT decoder, and read its
> `iss` — then make `.env` match it.

---

## 7. Part C — The token bridge on the portal

The widget needs the token **on the page**. How it gets there depends on the
portal’s software. The backend doesn’t care **how** — it only verifies whatever
token arrives.

### 7.1 Why we bridge the token server‑side (the important idea)

The portal logs the user in **server‑to‑server** (portal server → Keycloak). The
OIDC module keeps the resulting access token in the **user’s session on the
server**. We simply **reuse** that already‑obtained token: the page template prints
it into a `<meta>` tag, and the widget reads it.

We do **not** use a browser‑side library (`keycloak-js`) in production because:
* it adds another moving part and another redirect, and
* if Keycloak were ever HTTP while the page is HTTPS, the browser would **block**
  it (mixed content). The server‑side bridge sidesteps that entirely.

### 7.2 Drupal portal (the tested path)

Two files in the portal theme. This is exactly the
[deployments/widget-snippets/mosdac-drupal.html](deployments/widget-snippets/mosdac-drupal.html)
snippet.

**(a) `<theme>.theme` — the bridge hook:**

```php
function mosdac_theme_preprocess_html(array &$variables) {
  $variables['kc_access_token'] = '';
  $account = \Drupal::currentUser();
  if ($account->isAuthenticated() && \Drupal::hasService('openid_connect.session')) {
    $token = \Drupal::service('openid_connect.session')->retrieveAccessToken();
    if (!empty($token)) {
      $variables['kc_access_token'] = $token;
    }
  }
  // CRITICAL: never cache one user's token into another user's page.
  $variables['#cache']['contexts'][] = 'user';
  $variables['#cache']['max-age']     = 0;
}
```

**(b) `templates/layout/html.html.twig` — print it only when logged in:**

```twig
{% if kc_access_token %}
<meta name="kc-token" content="{{ kc_access_token }}">
{% endif %}
```

After editing either file: **`drush cr`** (Drupal caches the compiled template).

> Anonymous pages simply omit the `<meta>` tag → the widget runs ephemeral with no
> sidebar and shows the Sign‑in card. **Nothing is hard‑coded** in the backend.

### 7.3 If the real portal is NOT Drupal (generic path)

The contract is tiny. The portal must, **for logged‑in users only**, put the
Keycloak access token on the page in **one** of these two ways (the widget reads
both — see [static/mosdac-chat-widget.js](static/mosdac-chat-widget.js)):

```html
<!-- Option 1: a meta tag (rendered server-side, only for authenticated users) -->
<meta name="kc-token" content="THE_JWT_ACCESS_TOKEN">
```
```html
<!-- Option 2: a JS global, set before the widget script loads -->
<script>window.KC_TOKEN = "THE_JWT_ACCESS_TOKEN";</script>
```

If the portal exposes the token under a **different** name, you don’t touch the
backend — you give the widget a custom `getToken()` in the page config (see
[§8](#8-part-d--the-chat-widget-the-script-tag)). That function can read a cookie, a
different meta tag, a global, or even `await fetch('/my/token/endpoint')`. As long
as it returns the JWT string, everything downstream works.

> **Security note for the bridge, any portal:** make sure the page that carries the
> token is **per‑user, not cached across users** (the Drupal hook does this with
> the `user` cache context + `max-age 0`). A shared cache that stores one user’s
> token into another user’s page would be a serious leak.

---

## 8. Part D — The chat widget (the `<script>` tag)

This is the only thing that goes **into the portal’s pages**. Drop it **before
`</body>`** in the portal template.

### 8.1 The full production embed

```html
<!-- (1) The SSO token bridge — printed by the portal ONLY for logged-in users. -->
{% if kc_access_token %}
<meta name="kc-token" content="{{ kc_access_token }}">
{% endif %}

<!-- (2) Widget configuration. Only `apiBase` is truly required; the rest is branding. -->
<script>
  window.GRAPH_RAG_CHAT_CONFIG = {
    apiBase:  "/chatapi",                               // Nginx route to the backend
    botTitle: "MOSDAC BOT",
    logoUrl:  "/static/isro-logo.png",                  // or a portal-hosted logo URL
    greeting: "Hey {name}, what's on your mind today?", // {name} → username after sign-in
    suggestions: ["How can you help me browse?", "What can you do?", "Explain a topic"],
    elementPrefix: "mosdac",
    // "Sign in" target. Usually you can OMIT this — the widget gets it from
    // GET /config (CHAT_API_LOGIN_URL), so it's centrally configured. Set here to override.
    loginUrl: "/user/login/openid_connect",
    // getToken is provided by the MOSDAC shim (reads window.KC_TOKEN / <meta kc-token>).
    // Override ONLY if your portal exposes the token under a different name:
    // getToken: function () { return (window.MY_TOKEN || ""); },
  };
</script>

<!-- (3) The branded shim → it sets MOSDAC defaults and loads the real widget. -->
<script src="/static/mosdac-chat-widget.js"></script>
```

That’s it. The shim ([static/mosdac-chat-widget.js](static/mosdac-chat-widget.js))
loads the real widget ([static/graph-rag-chat-widget.js](static/graph-rag-chat-widget.js))
from the same `/static/` folder. **Page config always overrides shim defaults**, so
re‑branding is config only.

### 8.2 Every widget config option (from the widget’s `DEFAULTS`)

These are the keys the widget understands (see the top of
[static/graph-rag-chat-widget.js](static/graph-rag-chat-widget.js)). All optional
except `apiBase`.

| Key | Default | What it does (plainly) |
|---|---|---|
| `apiBase` | `/chatapi` | The path Nginx uses to reach the backend. **The one thing you must get right.** |
| `botTitle` | `MOSDAC BOT` | Title in the header and the history sidebar. |
| `logoUrl` | `''` | Logo image in the header. |
| `greeting` | `"Hey {name}, …"` | First message. `{name}` becomes the username after sign‑in, or `anonymousName` before. |
| `anonymousName` | `User` | The “name” shown before sign‑in (“Hey User…”). |
| `suggestions` | 3 chips | Clickable example questions. |
| `getToken` | `null` (shim sets it) | Function returning the JWT. The widget never parses it; it just forwards it. |
| `getUser` | `null` | Optional override for the username; otherwise the widget calls `GET /me`. |
| `authMode` | `token` | `token` = SSO on; `none` = never attempt auth. |
| `sidebarEnabled` | `true` | Show the chat‑history sidebar (only appears for signed‑in users anyway). |
| `loginUrl` | `''` (from `/config`) | Where “Sign in” goes. A **string** is navigated to; a **function** is called (used by the demo with `keycloak-js`). `''` hides the button. |
| `loginRedirectParam` | `destination` | The query param used to send the user back after login. |
| `accent`, `accentHover`, `panelBg`, `headerBg`, `sidebarBg`, `msgBotBg`, `msgUserBg`, `textColor`, `mutedColor`, `borderColor`, `placeholderColor` | a light MOSDAC theme | Every colour, themable from the page. |
| `elementPrefix` | `grag` (shim sets `mosdac`) | Prefix for the widget’s DOM ids/classes — avoids clashing with the portal’s own CSS. |
| `panelWidth` | `420` | Width of the chat panel in pixels. |
| `enableScreenshot` | `true` | Show the “attach screenshot” button (also gated by the backend `/config`). |
| `fetchRemoteConfig` | `true` | Let the widget pull `GET /config` at boot so it stays in sync with the backend. |
| `katexBase` | `''` (auto) | Where to load the vendored KaTeX math assets from. Auto‑derived from the script URL; works at any mount path. |

### 8.3 How the widget isolates itself from the portal’s CSS

The widget renders inside a **Shadow DOM** with an `elementPrefix`, and it resets
inherited font/colour/line‑height. Translation: **the portal’s stylesheets can’t
accidentally break the widget, and the widget can’t break the portal.** This is why
the same widget looks right on Drupal, a custom portal, or a plain HTML page.

---

## 9. Part E — Nginx, the traffic cop (full config, line by line)

This is the most important infrastructure file. The repo ships an **HTTP** version
at [deployments/nginx/mosdac.conf](deployments/nginx/mosdac.conf) for the test site.
Below is the **production HTTPS** version. Save it as
`/etc/nginx/conf.d/mosdac.conf` (or your distro’s sites‑available).

### 9.1 The full production config

```nginx
# ===========================================================================
# MOSDAC BOT — PRODUCTION Nginx site config (HTTPS).
# Responsibilities:
#   * terminate TLS (HTTPS) for the world
#   * serve the widget JS/CSS/logo under /static/
#   * reverse-proxy /chatapi/ to the FastAPI backend (chat_api) on :8000
#   * forward the Keycloak access token (Authorization header) for SSO users
#   * stay SSE-friendly for /chatapi/chat/stream (live token streaming)
# ===========================================================================

# The FastAPI backend. With docker-compose, use the SERVICE NAME, not 127.0.0.1,
# so Nginx (also on the compose network) resolves it. If Nginx runs on the host
# instead, use 127.0.0.1:8000 (the published port).
upstream mosdac_chat_api {
    server chat_api:8000;     # docker-compose service name  (host install: 127.0.0.1:8000)
    keepalive 16;             # reuse upstream connections — lower latency
}

# ---- 1) Redirect ALL plain HTTP to HTTPS ----------------------------------
server {
    listen 80;
    server_name mosdac.gov.in www.mosdac.gov.in;     # ← the REAL domain(s)
    # Allow ACME/Let's Encrypt renewals over HTTP if you use them:
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

# ---- 2) The real HTTPS server ---------------------------------------------
server {
    listen 443 ssl;
    http2 on;
    server_name mosdac.gov.in www.mosdac.gov.in;     # ← the REAL domain(s)

    # ---- TLS certificates (use your CA / Let's Encrypt files) -------------
    ssl_certificate     /etc/nginx/certs/mosdac.gov.in.fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/mosdac.gov.in.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # ---- Security headers (defense in depth) ------------------------------
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # ---- Body size: must be >= CHAT_API_MAX_REQUEST_BYTES (12 MB) ----------
    # Default Nginx caps uploads at 1 MB, which would break screenshot uploads.
    client_max_body_size 12m;

    # ---- a) Widget static assets (Nginx serves these, NOT FastAPI) --------
    location /static/ {
        alias /srv/mosdac/static/;                   # ← path to the repo's static/ dir
        add_header Cache-Control "public, max-age=300";
        try_files $uri =404;
    }

    # ---- b) Chat API (reverse proxy to FastAPI) ---------------------------
    location /chatapi/ {
        # Trailing slash strips the /chatapi prefix → backend sees /chat, /me, etc.
        proxy_pass http://mosdac_chat_api/;
        proxy_http_version 1.1;

        proxy_set_header Host              $host;
        # X-Real-IP is OVERWRITTEN with the true socket peer on every request, so a
        # client cannot forge it. The backend keys per-IP rate limiting on this.
        # You MUST set CHAT_API_TRUST_FORWARDED_FOR=true in .env behind this proxy.
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSO passthrough — forward the Bearer token UNCHANGED. The backend verifies
        # it against Keycloak's JWKS; Nginx does not touch or validate it here.
        proxy_set_header Authorization     $http_authorization;

        # Server-Sent Events (/chatapi/chat/stream): DO NOT buffer, so tokens reach
        # the browser as they are produced; allow a long-lived response.
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 300s;

        # CORS is handled by FastAPI (env-driven allow-list). Do NOT add
        # Access-Control-* here, or you get duplicate/again conflicting headers.
    }
}
```

### 9.2 Line by line — what each part does (plainly)

| Block | Plain explanation |
|---|---|
| `upstream mosdac_chat_api` | A nickname for “the backend”. On compose use the **service name** `chat_api:8000`; on a host install use `127.0.0.1:8000`. `keepalive 16` reuses connections so it’s faster. |
| `server { listen 80 … return 301 }` | **Anyone who comes by plain HTTP is sent to HTTPS.** The `acme-challenge` line lets Let’s Encrypt renew certificates without breaking the redirect. |
| `listen 443 ssl; http2 on;` | The real, encrypted front door. **TLS ends here** — the world speaks HTTPS to Nginx; Nginx speaks plain HTTP to the backend on the private network. |
| `ssl_certificate*` | Your TLS certificate + private key. Use the real CA‑issued files (or Let’s Encrypt). |
| `Strict-Transport-Security` (HSTS) | Tells browsers “**always** use HTTPS for this site from now on.” |
| `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` | Standard hardening headers (no MIME sniffing, no clickjacking via framing, limited referrer leakage). |
| `client_max_body_size 12m` | **Critical.** Nginx’s default is 1 MB, which would reject screenshot uploads. Match it to `CHAT_API_MAX_REQUEST_BYTES` (12 MB). |
| `location /static/` | Nginx hands out the widget files straight from disk (`alias` → the repo’s `static/`). FastAPI does **not** serve these in this setup. Cached 5 min. |
| `location /chatapi/` + `proxy_pass …/` | The reverse proxy. The **trailing slash** on `proxy_pass` strips the `/chatapi` prefix, so the backend sees clean paths like `/chat`, `/me`, `/conversations`. |
| `X-Real-IP $remote_addr` | Stamps the **true** client IP, overwriting anything the client sent. This is what makes rate limiting honest. |
| `Authorization $http_authorization` | **The SSO passthrough.** The user’s wristband (Bearer token) is forwarded untouched so the backend can verify it. |
| `proxy_buffering off; proxy_read_timeout 300s;` | Lets streamed answers (SSE) flow token‑by‑token instead of being held until the end, and allows long LLM responses without timing out. This is the fix for the old “something went wrong” at 45–109 s. |
| “CORS handled by FastAPI” comment | We deliberately **don’t** add CORS headers in Nginx, or you’d get duplicates. CORS lives in one place: the backend’s env‑driven allow‑list. |

### 9.3 Two ways to run Nginx (pick one)

* **Nginx on the host** (you manage TLS, `static/`, and proxy yourself): use
  `server 127.0.0.1:8000;` in the upstream, and `alias` to wherever you checked out
  `static/`.
* **Nginx as another compose service** on the same network: use
  `server chat_api:8000;`, and mount `./static` into the Nginx container at the
  `alias` path. Add a service like:

```yaml
  nginx:
    image: nginx:1.27-alpine
    depends_on: [chat_api]
    ports: ["80:80", "443:443"]
    volumes:
      - ./deployments/nginx/mosdac.conf:/etc/nginx/conf.d/mosdac.conf:ro
      - ./static:/srv/mosdac/static:ro
      - /etc/nginx/certs:/etc/nginx/certs:ro
```

> If the **portal’s own** web server already terminates TLS and you only need it to
> forward `/chatapi` and `/static` to us, you can instead add those two `location`
> blocks to the **portal’s** existing server block — same directives. The point is:
> **wherever the user’s browser sends `/chatapi/...`, those requests must reach our
> FastAPI on :8000 with the `Authorization` header intact and buffering off.**

---

## 10. The request lifecycle, step by step

### 10.1 An anonymous visitor (not logged in)

1. The portal renders a page **without** the `kc-token` meta tag.
2. The widget’s `getToken()` returns `""` → the widget treats the user as anonymous.
3. The widget shows the **“Sign in”** card and greets `"Hey User, …"`.
4. The user types a question → `POST /chatapi/chat` **without** an `Authorization`
   header.
5. Nginx forwards it to FastAPI. `get_optional_user` returns `None`.
6. `service.chat()` runs the **ephemeral** path: answer is produced, **nothing is
   written to the conversation DB**, and the response has `conversation_id: null`.
7. The widget shows the answer. No sidebar.

### 10.2 A logged‑in user

1. The user clicked **Sign in** earlier → the portal did the OIDC dance with
   Keycloak → the token is stored in the session.
2. On every page, the portal prints `<meta name="kc-token" content="<JWT>">`.
3. The widget’s `getToken()` reads it and sends `Authorization: Bearer <JWT>`.
4. The widget calls `GET /chatapi/me`. The backend:
   * `decode_token()` fetches Keycloak’s public keys (JWKS), checks the signature,
     `exp`, `iss` (and `aud` if configured),
   * `normalize_user_data()` reads `sub`/`preferred_username`/`email` (the names
     from `.env`) into a `NormalizedUser`.
   * Returns `{id, username, email}`.
5. The greeting becomes **“Hey priya, …”**, the **Sign‑in card disappears**, and the
   **Chat History sidebar** loads via `GET /chatapi/conversations`.
6. The user asks a question → `POST /chatapi/chat` **with** the token.
   `get_optional_user` returns the user → `service.chat_authenticated()` saves the
   turn under that user and returns a real `conversation_id`.
7. On the **first** message of a **new** chat, a background task asks the LLM for a
   4–5 word title; the sidebar refreshes and the title appears a second later.
8. Click a past chat → `GET /conversations/{id}/messages` loads it (ownership‑checked
   — a forged id returns **404**).

### 10.3 What happens if the token is bad

* **Missing/empty token** → treated as anonymous (no error).
* **Malformed / expired / wrong issuer** → `401`. The widget **does not** silently
  pretend you’re anonymous; it reverts to the **Sign‑in card** so you can log in
  again. (A real session is never silently downgraded.)

---

## 11. CORS explained simply

**CORS** = the browser’s rule: *“a page from site A may only call site B’s API if
site B says it’s allowed.”* It exists so a random evil website can’t silently use
your logged‑in session on another site.

* **If the widget pages and `/chatapi` share the same domain** (because Nginx fronts
  both — the recommended setup), the browser sees **same‑origin** requests and
  **CORS never triggers**. Easiest, safest. Nothing to configure.

* **If the API is on a different domain** than the portal, you must list the
  **portal’s exact origin** (scheme + host + port) in
  `CHAT_API_ALLOWED_ORIGINS`. The backend’s `CORSMiddleware`
  ([chat_api/main.py](chat_api/main.py)) replies with the matching
  `Access-Control-Allow-Origin`. It’s an **exact‑match allow‑list** — never a
  wildcard — and `allow_credentials` is on, so the token can ride along.

```ini
# Example: portal at https://mosdac.gov.in, API at https://api.mosdac.gov.in
CHAT_API_ALLOWED_ORIGINS=https://mosdac.gov.in,https://www.mosdac.gov.in
```

> **`DELETE` must stay** in `CHAT_API_ALLOWED_METHODS` — the widget calls
> `DELETE /conversations/{id}` and `DELETE /chat/{session_id}`. Drop it and
> cross‑origin deletes fail the CORS preflight (this is masked when same‑origin).

---

## 12. Rate limiting and the real‑client‑IP trick

The backend limits how many requests **each client IP** may make (so one abuser
can’t exhaust the LLM). But behind Nginx, **every** request arrives from Nginx’s IP.
If we trusted that, **all users would share one bucket** and the whole portal would
get rate‑limited together.

The fix, in two parts:

1. **Nginx** stamps `X-Real-IP: $remote_addr` — the **true** socket peer —
   overwriting anything the client sent. (See the Nginx block above.)
2. **Backend** reads that header **only when** `CHAT_API_TRUST_FORWARDED_FOR=true`.

> 🔴 **You must set `CHAT_API_TRUST_FORWARDED_FOR=true` in production behind this
> Nginx.** Forget it and every visitor collapses into one shared rate‑limit bucket.
> Conversely, **never** set it true if the backend is directly internet‑facing with
> no trusted proxy, or a client could spoof the header.

Other abuse controls already on: a **hard body‑size cap** (`MAX_REQUEST_BYTES`,
rejected before the body is read), a **message length cap**, **OWASP security
headers**, **pydantic input validation** (incl. UUID checks on `session_id` /
`conversation_id`), the **L1–L5 guardrail pipeline**, and an **optional shared API
key**. The limiter **fails closed** at startup (`CHAT_API_REQUIRE_RATE_LIMIT=true`)
so the service never boots without its primary DoS control.

---

## 13. Custom token payloads (gov portal claims)

Different identity providers put the user’s id/username/email under **different
claim names**, sometimes **nested**. You handle all of it with **three `.env`
values — no code changes** (this is the “adapter pattern”;
[chat_api/auth.py](chat_api/auth.py) `lookup_claim`).

Each of `JWT_FIELD_ID`, `JWT_FIELD_USERNAME`, `JWT_FIELD_EMAIL` is a **claim spec**
that supports three shapes:

| Shape | Example value | Reads from a token like |
|---|---|---|
| Plain name | `JWT_FIELD_USERNAME=preferred_username` | `{ "preferred_username": "alice" }` |
| Nested dotted path | `JWT_FIELD_USERNAME=user_info.preferred_username` | `{ "user_info": { "preferred_username": "alice" } }` |
| Fallback list (left→right, first non‑empty wins) | `JWT_FIELD_USERNAME=preferred_username,name,email` | tries each in order |

You can combine them: `JWT_FIELD_ID=sub,user_info.sub,uid`.

**Worked example — a gov portal that nests its claims:**
```ini
JWT_FIELD_ID=user.id
JWT_FIELD_USERNAME=user.login_name
JWT_FIELD_EMAIL=user.contact.email
```

**The front‑end needs nothing.** The widget never reads the token — it asks
`GET /me`, which applies your mapping. Get the backend mapping right and the
username + history “just work.”

> If the id claim is missing, `/me` returns a **401 that names the field it
> expected** (`Set JWT_FIELD_ID …`) — so a misconfiguration is diagnosable straight
> from the error message.

---

## 14. The API endpoints (reference table)

From [chat_api/routes.py](chat_api/routes.py). Paths below are what the **backend**
sees; the browser calls them under `/chatapi/...` (Nginx strips the prefix).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/chat` | optional (`api_key` if set + optional user) | Ask a question. Anonymous → `conversation_id: null`, nothing saved. Logged‑in → saved + real id. |
| `POST` | `/chat/stream` | optional | Same, but **streams** `token` events then one authoritative `final` event (use `final` as the answer). |
| `GET` | `/conversations` | **required** | The sidebar list (current user’s chats, newest first). 503 if auth disabled. |
| `GET` | `/conversations/{id}/messages` | **required** | Load one past chat. **404** if not owned (anti‑IDOR). |
| `DELETE` | `/conversations/{id}` | **required** | Delete a chat. **404** if not owned. |
| `DELETE` | `/chat/{session_id}` | optional (`api_key`) | Clear an anonymous session (UUID‑validated). |
| `GET` | `/me` | **required** | `{id, username, email}` for the greeting. 401 if token bad, 503 if auth off. |
| `GET` | `/config` | none | Tells the widget: title, screenshot toggle, `auth_enabled`, `login_url`, Keycloak coordinates. |
| `GET` | `/health` | none | Liveness — cheap, never touches dependencies. |
| `GET` | `/ready` | none | Readiness — probes embedder/Chroma/Neo4j; **503** until all are up. |
| `GET` | `/metrics` | **admin token** | Prometheus metrics. **404** unless `CHAT_API_ADMIN_TOKEN` is set; scrape with `X-Admin-Token`. |
| `POST` | `/reload` | **admin token** | Hot‑reload BM25/caches after a re‑ingest. |

---

## 15. Security checklist (everything)

The system was built for a Government of India portal. Confirm each before go‑live:

* [ ] **TLS everywhere.** Portal, Keycloak, and the public face of the API are all
  **HTTPS**. Plain HTTP 80 only redirects to 443.
* [ ] **Issuer pinned.** `CHAT_API_KEYCLOAK_ISSUER` equals the token `iss` exactly.
* [ ] **Algorithm allow‑list.** `CHAT_API_JWT_ALGORITHMS=RS256` (blocks `alg:none`
  and HS/RS confusion). Signature, `exp`, `iss` (and `aud` if set) all enforced.
* [ ] **No IDOR.** Every conversation/message query filters on `user_id`; non‑owned
  ids return **404** (not 403 — avoids existence disclosure). Unit‑tested.
* [ ] **CORS is an exact allow‑list**, never `*`, with `allow_credentials=true`.
* [ ] **Anonymous isolation.** Anon requests never get a `conversation_id`, never
  touch the DB, and the sidebar is hidden client‑side.
* [ ] **Rate limiting honest behind the proxy.** `CHAT_API_TRUST_FORWARDED_FOR=true`
  **and** Nginx overwrites `X-Real-IP`. Limiter **fails closed** at boot.
* [ ] **Body cap.** Nginx `client_max_body_size 12m` matches
  `CHAT_API_MAX_REQUEST_BYTES`.
* [ ] **Strong, unique secrets** for `NEO4J_PASSWORD`, `REDIS_PASSWORD`,
  `CHAT_API_ADMIN_TOKEN` (and `CHAT_API_API_KEY` if you use one). None left at
  defaults like `please-change-me`.
* [ ] **Data stores not exposed.** Neo4j bound to `127.0.0.1`; Redis not published;
  both password‑protected.
* [ ] **Non‑root app.** The container runs as `appuser` (UID 10001); only the
  entrypoint chowns mounts as root, then drops privileges via `gosu`.
* [ ] **`/metrics` and `/reload` guarded** by the distinct admin token (404 when
  unset).
* [ ] **Per‑user page caching.** The token bridge page is **never** cached across
  users (Drupal: `user` cache context + `max-age 0`).
* [ ] **Token lifespan sane** and refreshed by the portal.

---

## 16. The production deployment checklist (do this in order)

1. **Keycloak (IT team):** create realm `mosdac`; create the **confidential** portal
   client with real HTTPS redirect URIs; set a sane access‑token lifespan; record the
   **exact issuer** string. → [§6](#6-part-b--keycloak-sso-for-the-real-portal)
2. **Backend `.env`:** `cp deployments/generic.env .env`, then fill the SSO block
   (`AUTH_ENABLED=true`, the real **issuer**, claim mapping), CORS origins,
   `TRUST_FORWARDED_FOR=true`, strong secrets, admin token. →
   [§5](#5-part-a--the-backend-brain-docker--env)
3. **Bring up the stack:** `docker compose up -d --build`; confirm `health`/`ready`/
   `config` (`auth_enabled: true`). → [§5.4](#54-start-it-and-verify)
4. **Nginx:** install the production HTTPS config; point `alias` at `static/`,
   `proxy_pass` at the backend; install real TLS certs; reload Nginx. →
   [§9](#9-part-e--nginx-the-traffic-cop-full-config-line-by-line)
5. **Token bridge on the portal:** add the theme preprocess hook + the `<meta>` tag
   (Drupal), or the equivalent server‑side print (other CMS); clear the portal cache.
   → [§7](#7-part-c--the-token-bridge-on-the-portal)
6. **Widget embed:** add the `<script>` config + the shim `<script src>` before
   `</body>`; set `apiBase`, branding. → [§8](#8-part-d--the-chat-widget-the-script-tag)
7. **Verify** anonymous + logged‑in + IDOR + CORS (next section).
8. **Hand off:** document the admin token, where logs/metrics are, and the backup
   plan ([docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)).

---

## 17. Verification — smoke tests

**Backend (curl):**
```bash
# Auth must be ON and login_url present:
curl -s https://mosdac.gov.in/chatapi/config | python -m json.tool

# Anonymous chat works and saves nothing (conversation_id should be null):
curl -s -X POST https://mosdac.gov.in/chatapi/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"11111111-1111-1111-1111-111111111111","message":"hello"}' \
  | python -m json.tool

# With a real token, /me returns your identity:
curl -s https://mosdac.gov.in/chatapi/me \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

**Browser (the real experience):**
1. **Anonymous:** open the portal logged out → bubble opens, **no sidebar**,
   greeting “Hey User…”, chat works, **no rows** added to the conversation DB.
2. **Logged in:** sign in → reload → greeting shows **your username**, Sign‑in card
   gone, **sidebar** lists chats. Send a message → a 4–5 word **title** appears after
   ~1 s. Click a past chat → it loads. Refresh → history persists.
3. **IDOR probe:** as user A, `GET /chatapi/conversations/{B's id}/messages` → **404**.
4. **CORS:** a request from an origin **not** in `CHAT_API_ALLOWED_ORIGINS` is blocked
   (only relevant if the API is cross‑domain).
5. **Streaming:** watch the answer appear **token by token** (proves
   `proxy_buffering off` is working — no 45–109 s hang).

---

## 18. Troubleshooting (big table)

| Symptom | Likely cause | Fix |
|---|---|---|
| Widget shows **“Sign in”** even after logging in | Portal isn’t printing `<meta name="kc-token">`, or the token expired | View page source while logged in — is the meta tag there? `drush cr` (Drupal); raise token lifespan; **log out/in** to refresh a stale token. |
| Greeting stays **“Hey User”**, no Sign‑in card, `/me` returns **401** | **Issuer mismatch** (the #1 issue) | Make `CHAT_API_KEYCLOAK_ISSUER` exactly equal the token’s `iss` (watch `http`/`https`, trailing slash, realm). Decode the token to compare. |
| `/me` → 401 **“missing the required id claim”** | `JWT_FIELD_ID` points at a claim the token doesn’t have | Set `JWT_FIELD_ID` to the real claim (nested/fallback allowed). → [§13](#13-custom-token-payloads-gov-portal-claims) |
| `/conversations` → **503** | `CHAT_API_AUTH_ENABLED=false` | Set it `true` and restart. |
| Everyone gets rate‑limited together | `CHAT_API_TRUST_FORWARDED_FOR` not set behind Nginx | Set it `true`; confirm Nginx sets `X-Real-IP $remote_addr`. → [§12](#12-rate-limiting-and-the-real-client-ip-trick) |
| Answer hangs ~45–109 s then **“something went wrong”** | Nginx buffering the SSE stream | `proxy_buffering off; proxy_read_timeout 300s;` on `/chatapi/`; backend already sends `X-Accel-Buffering: no`. |
| Screenshot upload fails / 413 | Nginx body cap too small | `client_max_body_size 12m;` to match `CHAT_API_MAX_REQUEST_BYTES`. |
| Cross‑origin call blocked by browser | Origin not in the allow‑list | Add the exact origin to `CHAT_API_ALLOWED_ORIGINS`; ensure `DELETE` is in the methods. |
| Widget bubble missing entirely | Backend down, or `/static/` not served | `docker compose ps`; confirm Nginx `/static/` `alias` path; check `apiBase`. |
| Neo4j auth errors after setting a password | `NEO4J_AUTH` only applies to a **fresh** volume | Recreate `./neo4j_data` or `neo4j-admin dbms set-initial-password`. → [docker-compose.yml](docker-compose.yml) |
| `/metrics` returns 404 | `CHAT_API_ADMIN_TOKEN` unset (by design) | Set it; scrape with `X-Admin-Token`. |
| Browser blocks Keycloak (mixed content) | Trying client‑side `keycloak-js` from HTTPS page → HTTP Keycloak | Use the **server‑side bridge** (this guide) and/or put Keycloak on HTTPS. |

---

## 19. Appendix A — Every environment variable

The full list lives in [chat_api/config.py](chat_api/config.py). The ones that matter
for portal integration:

| Variable (`.env`) | Default | Meaning |
|---|---|---|
| `CHAT_API_AUTH_ENABLED` | `false` | Master SSO switch. |
| `CHAT_API_KEYCLOAK_ISSUER` | `""` | Realm issuer; **must equal token `iss`**. JWKS URL derived as `{issuer}/protocol/openid-connect/certs`. |
| `CHAT_API_KEYCLOAK_JWKS_URL` | `""` | Override the public‑keys URL if non‑standard. |
| `CHAT_API_KEYCLOAK_AUDIENCE` | `""` | Required `aud` (comma list). Empty = skip. |
| `CHAT_API_JWT_ALGORITHMS` | `RS256` | Allowed signing algorithms. |
| `CHAT_API_JWKS_CACHE_SECONDS` | `3600` | Public‑key cache TTL. |
| `JWT_FIELD_ID` / `_USERNAME` / `_EMAIL` | `sub` / `preferred_username` / `email` | Claim mapping (also `CHAT_API_JWT_FIELD_*`). Nested + fallback supported. |
| `CHAT_API_LOGIN_URL` | `""` | “Sign in” target; served to the widget via `/config`. Empty = no button. |
| `CHAT_API_KEYCLOAK_PUBLIC_CLIENT` | `""` | Public client id — **only** for the `sso-demo.html` harness. |
| `CHAT_API_ALLOWED_ORIGINS` | localhost + mosdac.gov.in | CORS exact‑match allow‑list. |
| `CHAT_API_ALLOWED_METHODS` | `GET,POST,DELETE,OPTIONS` | Keep `DELETE`. |
| `CHAT_API_ALLOWED_HEADERS` | `Content-Type,Authorization,Accept` | Allowed request headers. |
| `CHAT_API_TRUST_FORWARDED_FOR` | `false` | **Set `true` behind Nginx** for honest per‑IP limits. |
| `CHAT_API_REQUIRE_RATE_LIMIT` | `true` | Fail closed if the limiter can’t attach. |
| `CHAT_API_MAX_REQUEST_BYTES` | `12582912` | Hard body cap (match Nginx `client_max_body_size`). |
| `CHAT_API_MAX_MESSAGE_CHARS` | `8000` | Max message length. |
| `CHAT_API_CONV_STORE` | `sqlite` | `sqlite` (single replica) \| `postgres` (multi) \| `none`. |
| `CHAT_API_SQLITE_PATH` | `./conversations.db` | SQLite file (compose pins `/app/data/...`). |
| `CHAT_API_POSTGRES_DSN` | `""` | Postgres DSN for multi‑replica history. |
| `CHAT_API_SESSION_BACKEND` | `memory` | `memory` \| `redis` (compose uses `redis`). |
| `CHAT_API_REDIS_URL` | `""` | Redis URL (compose injects with password). |
| `CHAT_API_SESSION_TTL_SECONDS` | `86400` | Idle anonymous‑session expiry. |
| `CHAT_API_REQUIRE_PERSISTENT_SESSIONS` | `false` | Force Redis (refuse memory) for multi‑replica. |
| `CHAT_API_API_KEY` | `""` | Optional shared key on `/chat`. Empty = open (public portal). |
| `CHAT_API_ADMIN_TOKEN` | `""` | Guards `/reload` + `/metrics`. Keep distinct from `api_key`. |
| `CHAT_API_ENABLE_SCREENSHOT` | `false` | Screenshots; turn on only with `CHAT_API_VISION_MODEL`. |
| `CHAT_API_ENABLE_METRICS` | `true` | Expose `/metrics` (still admin‑guarded). |
| `NEO4J_PASSWORD` / `REDIS_PASSWORD` | dev defaults | **Set strong, unique values.** |

---

## 20. Appendix B — Every file and where it lives

**In this repo:**
- Backend: [chat_api/config.py](chat_api/config.py), [chat_api/auth.py](chat_api/auth.py),
  [chat_api/routes.py](chat_api/routes.py), [chat_api/service.py](chat_api/service.py),
  [chat_api/session.py](chat_api/session.py), [chat_api/titler.py](chat_api/titler.py),
  [chat_api/db/](chat_api/db/), [chat_api/main.py](chat_api/main.py)
- Widget: [static/graph-rag-chat-widget.js](static/graph-rag-chat-widget.js),
  [static/mosdac-chat-widget.js](static/mosdac-chat-widget.js),
  [static/isro-logo.png](static/isro-logo.png), [static/vendor/katex/](static/vendor/katex/),
  [static/sso-demo.html](static/sso-demo.html) *(test only)*
- Deployment: [deployments/nginx/mosdac.conf](deployments/nginx/mosdac.conf),
  [deployments/widget-snippets/mosdac-drupal.html](deployments/widget-snippets/mosdac-drupal.html),
  [docker-compose.yml](docker-compose.yml), [Dockerfile.api](Dockerfile.api),
  [docker-entrypoint.sh](docker-entrypoint.sh), [.env](.env)
- Other docs: [integration_final.md](integration_final.md) (Drupal multi‑tenant detail),
  [set_sso.md](set_sso.md) (SSO deep dive), [production.md](production.md),
  [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md)

**On the portal (outside this repo):** the theme preprocess hook (`*_preprocess_html`)
and the page template (`html.html.twig`) that print the `kc-token` meta tag — or the
equivalent server‑side token print for a non‑Drupal portal.

---

### Final mental model (say it out loud)

> **The Guard signs the wristband → the Portal puts it on the page → the Widget
> hands it to the Receptionist → the Receptionist passes it through untouched → the
> Brain checks it under the Guard’s lamp and gives you your name and your saved
> chats.** Everything is configuration — change the address, the realm, and the
> certificate, and the same code serves the real portal.
