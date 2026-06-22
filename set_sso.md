# MOSDAC BOT — Single Sign-On (SSO) Setup & Integration Guide

This document explains, in plain language, **how SSO works for the MOSDAC BOT chat
widget**, how it is wired into the chatbot, every file involved and why it exists, and
**how to adapt it to a different identity provider or a custom token payload — with no
code changes, only `.env` edits.**

If you only want the widget to stop showing "Sign in" after a user logs in and to show
their username, you mainly care about three things: a running Keycloak, the backend
pointed at the right realm (`.env`), and the Drupal theme bridging the token. All three
are covered below.

---

## 1. The big picture (read this first)

There are **two separate programs** and **one identity server**:

```
                                  ┌──────────────────────────┐
                                  │   Keycloak (the IdP)      │
                                  │   issues signed JWT tokens │
                                  └────────────┬──────────────┘
                                               │  (1) user logs in
                                               ▼
   Browser  ──────────►  Drupal site (HTTPS)  ◄── OpenID Connect module does the
   (the widget UI)        my-drupal-site            login server-to-server and
        ▲                                           STORES the access token in the
        │ (3) widget reads token                    user's session
        │     from <meta kc-token>          (2) theme prints that token into the page
        │
        │ (4) widget calls the API with the token
        ▼
   FastAPI backend (chat_api)  ──── verifies the token's signature against Keycloak's
   /me, /config, /chat,             public keys (JWKS), maps its claims to a user, and
   /conversations                   stores each user's chat history (SQLite).
```

**Key design rule: nothing about the token is hardcoded.** Which realm, which claim
holds the username, where "Sign in" points — all of it lives in `.env` (backend) or
Drupal config (theme). Swapping identity providers is a configuration task.

### Why the token is bridged server-side (important)

The Drupal site runs on **HTTPS** but the local Keycloak runs on **HTTP**. A browser
will **block** an HTTPS page from talking to an HTTP server (this is called *mixed
content*). So we **cannot** use a browser-side library like `keycloak-js` to fetch the
token here.

Instead, Drupal's *OpenID Connect* module already did the login **server-to-server**
(server → Keycloak, no mixed-content rule applies) and kept the token in the user's
session. We simply **reuse** that token: the theme prints it into the page, and the
widget reads it. This is the "token bridge".

---

## 2. How a request actually flows

### Anonymous visitor (not logged in)
1. Drupal renders the page **without** a `kc-token` meta tag.
2. The widget's `getToken()` returns an empty string → the widget treats the user as
   anonymous.
3. The widget shows the **"Sign in"** card and greets with the anonymous name
   (`"Hey User, …"`). Chat still works, but nothing is saved.
4. "Sign in" sends the user to the Drupal OIDC login (`/user/login/openid_connect`).

### Logged-in user
1. The user logs in once via Keycloak (through Drupal's "Login").
2. Drupal stores the Keycloak **access token** in the session.
3. On every page, the theme prints `<meta name="kc-token" content="…the JWT…">`.
4. The widget's `getToken()` reads that token and attaches it as
   `Authorization: Bearer <token>` on API calls.
5. The widget calls `GET /me` → the backend verifies the token and returns the
   username → the greeting becomes **"Hey admin, …"** and "Sign in" disappears.
6. Chat messages are saved per-user; the **Chat History** sidebar appears.

---

## 3. Files involved — what each one does

### Backend (in this repo — `chat_api/`)

| File | Purpose |
|---|---|
| [`chat_api/config.py`](chat_api/config.py) | All settings, loaded from `.env`. Holds the SSO knobs: `auth_enabled`, `keycloak_issuer`, `keycloak_audience`, the JWT field mapping (`jwt_field_id/username/email`), and `login_url`. **No claim name or URL is hardcoded** — everything has an `.env` override. |
| [`chat_api/auth.py`](chat_api/auth.py) | The security + **adapter** layer. `decode_token()` verifies the JWT signature against Keycloak's public keys (JWKS) and checks expiry/issuer/audience. `lookup_claim()` + `normalize_user_data()` translate the token's claims into a stable internal `NormalizedUser` using the names from `.env`. The rest of the app never sees a raw token or a claim name. |
| [`chat_api/routes.py`](chat_api/routes.py) | The HTTP endpoints. `GET /me` returns the logged-in user's `{id, username, email}` (used to personalise the greeting). `GET /config` tells the widget `auth_enabled` and `login_url` so the front-end needs **zero** hardcoding. `/conversations` (list/read/delete) and the authed `/chat` path persist history. |
| [`chat_api/db/sqlite_repo.py`](chat_api/db/sqlite_repo.py) | Per-user chat history storage (SQLite). Every row is scoped to the user id from the token, so one user can never read another's conversations. |
| [`.env`](.env) / [`.env.example`](.env.example) | Where you actually turn SSO on and point it at your Keycloak. This is the single place to manage auth. |

### Front-end widget (in this repo — `static/`)

| File | Purpose |
|---|---|
| [`static/graph-rag-chat-widget.js`](static/graph-rag-chat-widget.js) | The generic, portal-agnostic chat widget. It knows only two abstract things about auth: "call `getToken()` to get a token" and "call/visit `loginUrl` to sign in". It never parses a JWT itself — it asks `GET /me` for the username. If a token is present but rejected (expired/invalid), it cleanly falls back to the Sign-in card. |
| [`static/mosdac-chat-widget.js`](static/mosdac-chat-widget.js) | A thin MOSDAC "shim": sets MOSDAC defaults (title, logo, the default `getToken` that reads `window.KC_TOKEN` or the `kc-token` meta tag) and then loads the generic widget. Page config always wins, so re-branding is config-only. |
| [`static/sso-demo.html`](static/sso-demo.html) | A standalone **test harness** that stands in for the portal. It uses `keycloak-js` to log in and hand the widget a token. Useful for testing the widget's SSO behaviour against Keycloak **without** Drupal (works only when Keycloak and the page share http/https — i.e. both over `localhost`). |
| [`deployments/widget-snippets/mosdac-drupal.html`](deployments/widget-snippets/mosdac-drupal.html) | A copy-paste reference snippet showing exactly how to embed the widget in a Drupal theme, including the preprocess-hook token bridge. |

### Drupal theme (lives OUTSIDE this repo, at `~/my-drupal-site/web/themes/custom/mosdac_theme/`)

| File | Purpose |
|---|---|
| `mosdac_theme.theme` | **The token bridge.** Implements `mosdac_theme_preprocess_html()`: for a logged-in user it reads the Keycloak access token that the OpenID Connect module stored in the session (`\Drupal::service('openid_connect.session')->retrieveAccessToken()`) and exposes it to the template as `kc_access_token`. Adds a per-user cache rule so a token can never leak to another user via caching. |
| `templates/layout/html.html.twig` | The page template. Prints `<meta name="kc-token" …>` **only for logged-in users**, sets the widget config (`apiBase`, title, logo, greeting `"Hey {name}, …"`), and loads the widget. It deliberately does **not** hardcode the login URL — the widget gets that from `GET /config`. |

> **Single source of truth:** the widget JS is served by the FastAPI backend at
> `/static/…` and the DDEV nginx proxies `/static/` and `/chatapi/` to it. There are
> **no copies** of the widget in the Drupal docroot — fixing
> `static/graph-rag-chat-widget.js` updates every site at once.

---

## 4. Step-by-step: how SSO was configured

### Step 1 — Keycloak (the identity provider)
- Keycloak runs locally at `http://localhost:8081` (admin console `admin` / `admin`).
- The DDEV Drupal site logs in via the **`master`** realm using a **confidential**
  client called **`mosdac`** (it has a client secret; only the Drupal server uses it).
- Because Drupal redirects the browser to the WSL LAN address, tokens are issued with
  `iss = http://192.168.1.36:8081/realms/master`. **The backend must trust this exact
  issuer string** (see Step 4).
- Keycloak access tokens are short-lived. The realm default was **60 seconds**, which
  is too short for a bridged token (it isn't refreshed on each page load). We raised the
  master realm **Access Token Lifespan to 3600s (1 hour)** so a login stays usable for
  the session. (Realm settings → Tokens → Access Token Lifespan.)

> A separate **public** client `mosdac-chat` exists for the `static/sso-demo.html`
> harness only. The Drupal site does **not** use it.

### Step 2 — Drupal OpenID Connect module
- The contrib module **OpenID Connect** (`openid_connect`) is enabled.
- A client named **`keycloak_sso`** points at the Keycloak `master` realm endpoints and
  uses client id `mosdac` + its secret, scopes `openid email profile`.
- This module performs the actual login and **stores the access token in the session** —
  that stored token is what we bridge.

### Step 3 — The theme token bridge (two files)
- `mosdac_theme.theme` → `mosdac_theme_preprocess_html()` puts the stored token into
  `$variables['kc_access_token']` (only for authenticated users) and disables caching of
  that value per user.
- `html.html.twig` → prints `{% if kc_access_token %}<meta name="kc-token" content="{{ kc_access_token }}">{% endif %}` and loads the widget.
- After editing either file, run **`ddev exec vendor/bin/drush cr`** (Drupal caches the
  compiled template).

### Step 4 — The backend (`.env`)
Turn auth on and point it at the realm Drupal uses:

```ini
CHAT_API_AUTH_ENABLED=true
# MUST equal the `iss` claim inside the tokens (note: LAN IP, not localhost):
CHAT_API_KEYCLOAK_ISSUER=http://192.168.1.36:8081/realms/master
CHAT_API_KEYCLOAK_AUDIENCE=            # empty = skip audience check
CHAT_API_LOGIN_URL=/user/login/openid_connect   # widget reads this from /config
# Claim mapping (defaults are standard Keycloak claims):
JWT_FIELD_ID=sub
JWT_FIELD_USERNAME=preferred_username
JWT_FIELD_EMAIL=email
```

Then run the backend (host `0.0.0.0` so the DDEV container can reach it):

```bash
venv/bin/uvicorn chat_api.main:app --host 0.0.0.0 --port 8000
```

### Step 5 — Verify
- `curl http://localhost:8000/config` → should show `"auth_enabled":true` and your
  `login_url`.
- Log into the Drupal site, reload, open the widget → greeting shows your username, the
  "Sign in" card is gone, and the **Chat History** sidebar appears.

---

## 5. How to adapt it to a CUSTOM authentication payload

This is the part that makes the system reusable. Different identity providers put the
user's id / username / email under **different claim names**, sometimes **nested**
inside objects. You handle all of that with **three `.env` values — no code changes.**

Each of `JWT_FIELD_ID`, `JWT_FIELD_USERNAME`, `JWT_FIELD_EMAIL` is a **claim spec** that
supports three shapes:

| Shape | Example `.env` value | Reads from a token like |
|---|---|---|
| Plain claim name | `JWT_FIELD_USERNAME=preferred_username` | `{ "preferred_username": "alice" }` |
| Nested dotted path | `JWT_FIELD_USERNAME=user_info.preferred_username` | `{ "user_info": { "preferred_username": "alice" } }` |
| Fallback list (left→right, first non-empty wins) | `JWT_FIELD_USERNAME=preferred_username,name,email` | tries `preferred_username`, then `name`, then `email` |

You can combine them, e.g. `JWT_FIELD_ID=sub,user_info.sub,uid`.

### Worked examples

**A government portal that nests claims:**
```ini
JWT_FIELD_ID=user.id
JWT_FIELD_USERNAME=user.login_name
JWT_FIELD_EMAIL=user.contact.email
```

**A provider that uses non-standard names with a fallback for the display name:**
```ini
JWT_FIELD_ID=oid
JWT_FIELD_USERNAME=upn,name,email
JWT_FIELD_EMAIL=email
```

### Other things you can customize via `.env` (no code)
| Setting | What it controls |
|---|---|
| `CHAT_API_KEYCLOAK_ISSUER` | Which realm/IdP to trust. Must equal the token's `iss`. |
| `CHAT_API_KEYCLOAK_JWKS_URL` | Override the public-keys URL if it isn't `{issuer}/protocol/openid-connect/certs`. |
| `CHAT_API_KEYCLOAK_AUDIENCE` | Require a specific `aud` claim (comma-separated). Empty = skip the check. |
| `CHAT_API_JWT_ALGORITHMS` | Allowed signing algorithms (default `RS256`; the allow-list blocks downgrade attacks). |
| `CHAT_API_LOGIN_URL` | Where "Sign in" sends anonymous users. Served to the widget via `/config`. |
| `CHAT_API_AUTH_ENABLED` | Master on/off switch. `false` = anonymous/ephemeral only. |

The flow is always: **`decode_token()` verifies** → **`normalize_user_data()` maps the
claims you named in `.env`** → the rest of the app uses the normalized user. So the only
thing a new IdP touches is configuration.

### What the FRONT-END needs for a custom payload
**Nothing.** The widget never reads the token's contents — it asks `GET /me`, which
applies your `.env` mapping. So once the backend mapping is right, the username and
history "just work" in the widget.

---

## 6. Troubleshooting (common issues)

| Symptom | Likely cause | Fix |
|---|---|---|
| Widget shows "Sign in" even after logging in | Theme isn't printing the `kc-token` meta tag, OR the token expired | Check the page source for `<meta name="kc-token">` while logged in; run `drush cr`; raise the realm Access Token Lifespan; **log out and back in** to refresh a stale token. |
| Greeting stays "Hey User" but no Sign-in card | Token present but rejected by the backend (401 on `/me`) | Usually an **issuer mismatch**: `CHAT_API_KEYCLOAK_ISSUER` must exactly equal the token's `iss` (watch `localhost` vs LAN IP). Decode the token at jwt.io to compare. The widget now also reverts to the Sign-in card on a 401. |
| `/me` returns 401 "missing the required id claim" | `JWT_FIELD_ID` points at a claim the token doesn't have | Set `JWT_FIELD_ID` to the real claim (supports nested/fallback). |
| `/conversations` returns 503 | `CHAT_API_AUTH_ENABLED=false` | Set it to `true` and restart. |
| Widget bubble missing entirely | Backend down (it serves `/static/…` via the DDEV proxy) | Start `uvicorn` on `0.0.0.0:8000`. |
| Browser blocks Keycloak (mixed content) | Trying client-side `keycloak-js` from an HTTPS page to HTTP Keycloak | Use the server-side bridge (this guide). Don't load `keycloak-js` from HTTP on an HTTPS page. |

---

## 7. One-line mental model

> **Keycloak signs the token → Drupal stores it → the theme prints it into the page →
> the widget sends it to the backend → the backend verifies it and maps its claims
> (all names configurable in `.env`) → the user gets their name and saved history.**
