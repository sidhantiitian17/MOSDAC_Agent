# `static/` — The Browser Chat Widget & Front-End Assets

This folder holds the **front-end**: an embeddable chat widget any website can load with a
single `<script>` tag, the MOSDAC-branded shim, an SSO test harness, and vendored libraries
(KaTeX, Swagger UI) so the whole thing works **air-gapped with no CDN**.

The API serves this folder at **`/static/...`** (mounted in
[chat_api/main.py](../chat_api/main.py)), so a single copy of the widget is the source of
truth across every portal — a fix ships everywhere at once.

---

## File-by-file

### [graph-rag-chat-widget.js](graph-rag-chat-widget.js) — the generic widget (~68 KB)
The full, **domain-agnostic** chat widget. Everything — branding, backend URL, SSO token
source — comes from runtime config (`window.GRAPH_RAG_CHAT_CONFIG`), so the same file serves
any portal with nothing hardcoded. Highlights:
- Renders inside a **Shadow DOM** so the host page's CSS can't break it (and vice versa).
- Talks to the API; uses **SSE streaming** (`/chat/stream`) so tokens appear as they
  generate (avoids the "something went wrong" timeout on long answers).
- Renders **Markdown** + typesets **LaTeX** via the vendored **KaTeX** (`vendor/katex/`).
- Optional **Sign-in** affordance and authenticated calls when SSO is configured (driven by
  the `/config` endpoint).
- Optional screenshot upload when the deployment enables it.

### [mosdac-chat-widget.js](mosdac-chat-widget.js) — MOSDAC-branded shim (~2 KB)
A thin loader that sets ISRO/MOSDAC defaults (title, bot name, logo) and then loads the
generic widget. Kept for backward compatibility with existing nginx `sub_filter` rules and
portal `<script>` tags. **Page config wins** over these defaults, so re-branding is
config-only. For a new portal, point directly at the generic widget with your own config.

### [sso-demo.html](sso-demo.html) — Keycloak SSO verification harness
A standalone page to test the full SSO flow (login → token → authenticated `/chat`) against
the **same** Keycloak realm/host the backend verifies — it self-configures from the API's
`/config` so it can't drift into a 401 trap. Use it to validate auth before wiring the real
portal. (See also [set_sso.md](../set_sso.md).)

### [isro-logo.png](isro-logo.png)
The ISRO/MOSDAC logo asset used by the branded widget.

### `vendor/` — vendored third-party assets (no CDN)
- **`vendor/katex/`** — KaTeX for offline LaTeX rendering in the widget.
- **`vendor/swagger/`** — the Swagger UI bundle + CSS, and `vendor/favicon.png`, so the
  API's self-hosted `/docs` renders with **no public CDN** (required air-gapped, consistent
  with the strict CSP). Wired in [chat_api/main.py](../chat_api/main.py).

---

## How a site embeds it

```html
<script>
  window.GRAPH_RAG_CHAT_CONFIG = {
    apiBaseUrl: "https://your-host/chatapi",
    title: "MOSDAC BOT",
    botName: "MOSDAC Assistant"
  };
</script>
<script src="https://your-host/static/graph-rag-chat-widget.js" defer></script>
```

Ready-made snippets (generic + MOSDAC/Drupal) and the nginx reverse-proxy that maps
`/static` and `/chatapi` are in [deployments/](../deployments/). Because `static/` is a
**bind-mount** in Docker, front-end edits ship on `docker compose up -d` without an image
rebuild — bump the `?v=` query string to bust the browser cache (recorded in project
memory).

> These are static browser assets — there is no Python here and nothing to "import." The
> widget's only contract is the [HTTP API](../chat_api/routes.py).
