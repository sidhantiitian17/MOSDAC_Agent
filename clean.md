# clean.md — Remove `mosdac_agent/` completely

Goal: delete the `mosdac_agent/` package and strip **every** reference to it from the
codebase so the project boots, tests pass, and `grep -ri mosdac_agent` returns nothing
(except this file). The graph-RAG core (`graph_rag/`, `chat_api/`) must keep working.

---

## 0. TL;DR — what depends on `mosdac_agent/`

| Kind | Where | Action |
|------|-------|--------|
| The package itself | `mosdac_agent/` (14 `.py` + `config.json` + `widget/` + `__pycache__/`) | **Delete** |
| Live production wiring | `chat_api/main.py` (`_maybe_mount_mosdac` + its call) | **Edit** |
| Dedicated tests | `tests/test_mosdac_*.py` (4 files) | **Delete** |
| Python deps | `requirement.txt` MOSDAC block (`langgraph`, `fastmcp`, `streamlit`) | **Edit** (keep `requests`) |
| Env template | `.env.example` (MOSDAC section + comment) | **Edit** |
| Deployment artifacts | `deployments/mosdac.env`, `deployments/widget-snippets/mosdac.html` | **Delete** (decision) |
| Docs | `README.md`, `document.md`, `docs/*` (7 files) | **Edit / delete** (decision) |

**Only one production module imports `mosdac_agent`:** `chat_api/main.py`. It is fail‑soft
(wrapped in `try/except`) and gated behind `MOSDAC_ENABLE_MOSDAC_ENDPOINT` (default `false`),
so the runtime blast radius of removal is very small. Everything else is tests and docs.

---

## Phase 1 — Delete the package

Remove the entire directory:

```
mosdac_agent/
├── __init__.py
├── agent.py
├── catalog.py
├── client.py
├── config.py
├── config.json
├── exceptions.py
├── mcp_server.py
├── mdapi.py
├── mock_mosdac.py
├── routes.py
├── store.py
├── streamlit_app.py
├── tools.py
├── widget/  (widget.html, widget.css, widget.js)
└── __pycache__/
```

```powershell
git rm -r mosdac_agent
```

---

## Phase 2 — Unwire live code (`chat_api/main.py`)

This is the **only** runtime dependency. Two surgical edits:

1. **Remove the call** at [chat_api/main.py:68](chat_api/main.py#L68):
   ```python
   _maybe_mount_mosdac(app, sessions)
   ```
2. **Remove the whole function** [chat_api/main.py:78-106](chat_api/main.py#L78-L106)
   (`def _maybe_mount_mosdac(...)`), which contains the only
   `from mosdac_agent...` imports in production code.

After editing, confirm `sessions` is still consumed correctly (it is built at line 64 and
used by `build_session_store()` / the chat router — the mosdac call was the only other user).

**Verify:** `python -c "import chat_api.main; chat_api.main.create_app()"` boots clean.

---

## Phase 3 — Delete dedicated tests

All four import only from `mosdac_agent.*` and have no other purpose:

```
tests/test_mosdac_tools.py
tests/test_mosdac_mock_server.py
tests/test_mosdac_agent.py
tests/test_mosdac_integration.py
```

```powershell
git rm tests/test_mosdac_tools.py tests/test_mosdac_mock_server.py `
       tests/test_mosdac_agent.py tests/test_mosdac_integration.py
```

`tests/conftest.py` does **not** reference mosdac — leave it untouched.

---

## Phase 4 — Prune Python dependencies

### `requirement.txt` (lines 59-68 — the "MOSDAC agent stack" block)

| Package | Used outside `mosdac_agent`? | Action |
|---------|------------------------------|--------|
| `langgraph>=0.2` | No (only `mosdac_agent` + deleted test) | **Remove** |
| `fastmcp>=2.5` | No (only `mosdac_agent/mcp_server.py`) | **Remove** |
| `streamlit>=1.36` | No (only `mosdac_agent/streamlit_app.py`) | **Remove** |
| `requests>=2.31` | **Yes** — `graph_rag/embeddings/ollama_embedder.py`, `tests/test_ollama_embedder.py` | **KEEP** (move out of the MOSDAC block into the core deps) |

Delete the entire `# ── MOSDAC agent stack ──` comment block and the three packages above,
but relocate `requests>=2.31` to the core/runtime section so the embedder still installs.

### `pyproject.toml`

No change — it does not declare any MOSDAC packages (only langchain core deps).

---

## Phase 5 — Env template (`.env.example`)

- Remove the **MOSDAC Agent** section, [.env.example:107-110](.env.example#L107-L110):
  ```
  # ── MOSDAC Agent ──
  MOSDAC_USE_MOCK=true
  # MOSDAC_USERNAME=your_username
  # MOSDAC_PASSWORD=your_password
  ```
- Remove the stray comment at [.env.example:32-33](.env.example#L32-L33)
  ("The MOSDAC agent reuses TABBY_*…").

---

## Phase 6 — Deployment artifacts  *(decision)*

These exist only to ship the MOSDAC agent / its embeddable widget:

- `deployments/mosdac.env` — MOSDAC backend creds + `MOSDAC_*` vars.
- `deployments/widget-snippets/mosdac.html` — embeds `mosdac_agent/widget/*`.
- `deployments/README.md` — check for MOSDAC instructions and trim.

**Recommendation:** delete `deployments/mosdac.env` and `deployments/widget-snippets/mosdac.html`.
Keep `deployments/generic.env` and `deployments/widget-snippets/generic.html` (verify the
generic widget does not point at a `/mosdac/*` route; if it does, repoint it at the chat API).

---

## Phase 7 — Documentation  *(decision)*

Split into "delete (mosdac‑only)" vs "edit (shared)".

**Delete — these docs are entirely about the agent:**
- `docs/guide.md` (the `mosdac_agent/` package walkthrough)
- `docs/instruction_integrate.md` (agent integration guide)

**Edit — remove the MOSDAC sections, keep the rest:**
- `README.md` — drop `mosdac_agent/` from the structure tree (L21, L76), the run
  commands (L28-30, L159, L167-168), and the `MOSDAC_*` env notes (L146, L235, L247).
- `document.md` — remove the `mosdac_agent/` bullet (L218).
- `docs/documentation.md` — remove the large MOSDAC layer/file/MCP/testing sections.
- `docs/docker_guide.md` — remove the MOSDAC Dockerfile/compose snippets (L535-549, L632).
- `docs/fastapi_tutor.md` — remove "Section 11 — The MOSDAC Router" and the `main.py`
  mount example; keep the generic FastAPI teaching content.
- `docs/plan_offline.md`, `docs/enhanceToolCall.md` — trim MOSDAC references
  (`enhanceToolCall.md` is a design doc about the real MOSDAC HTTP API; keep only if still
  useful, otherwise delete).

> Note: `MOSDAC` (the satellite data portal) is the product domain and appears in branding
> and the real-API design notes. Only references to the **`mosdac_agent` package / module**
> must go. Do not blanket-delete every "MOSDAC" string.

---

## Phase 8 — Verify

```powershell
# 1. No code/test references remain (this file and pure-domain docs may still match):
#    Expect: only clean.md and any intentionally-kept domain docs.
rg -i "mosdac_agent"

# 2. App imports and boots without the agent mount:
python -c "import chat_api.main; chat_api.main.create_app()"

# 3. The removed optional deps are no longer imported anywhere:
rg -n "import (langgraph|fastmcp|streamlit)|from (langgraph|fastmcp|streamlit)"

# 4. Full suite green (mosdac tests gone, rest unaffected):
pytest -q
```

Acceptance criteria:
- `rg mosdac_agent` shows no hits in `*.py`, `requirement.txt`, `.env.example`.
- `create_app()` returns without ever hitting the "MOSDAC endpoint not mounted" warning path.
- `pytest` passes with 4 fewer test files and no collection/import errors.

---

## Execution order (safe sequence)

1. Phase 2 (unwire `chat_api/main.py`) — break the live import first.
2. Phase 3 (delete tests) — so the suite stops importing the package.
3. Phase 1 (delete `mosdac_agent/`) — remove the package.
4. Phase 4-5 (deps + env) — clean install/config surface.
5. Phase 6-7 (deployments + docs) — per decisions above.
6. Phase 8 (verify) — boot + `pytest` + `rg`.
7. Commit: `chore: remove mosdac_agent package and all references`.

---

## Open decisions (confirm before executing 6 & 7)

1. **Deployment artifacts** — delete `mosdac.env` + `mosdac.html`, or keep them? (Recommend delete.)
2. **Docs** — delete `docs/guide.md` + `docs/instruction_integrate.md` outright, or keep
   stubs? (Recommend delete; edit the shared docs.)
3. **`enhanceToolCall.md`** — keep as real-MOSDAC-API design reference, or remove? (Recommend keep.)
