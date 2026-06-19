# OS-Independence Plan — Making MOSDAC_Agent Run Cleanly on Any OS

**Goal:** the codebase should clone-and-run on Windows, Linux, and macOS with no
platform-specific edits and no broken functionality.

**Audit date:** 2026-06-18
**Scope audited:** every `*.py` file (excl. `.venv`), `.env.example`, `pyproject.toml`,
`requirement.txt`, `Dockerfile.api`, `docker-compose.yml`, `README.md`, test fixtures.

---

## TL;DR — How portable is it today?

Mostly portable already. The hard parts are done right:

- **All file I/O uses `pathlib.Path` and explicit `encoding="utf-8"`** — nothing relies
  on the platform default encoding (cp1252 on Windows). Verified across `loader.py`,
  `manifest.py`, `config.py`, `harness.py`, `source_allowlist.py`, `scope.py`,
  `graph_rag_chain.py`, `drupal_ingest.py`.
- **All configurable paths are relative** (`./chroma_db`, `./downloads`, `./atlases_pdfs`,
  `./prompts/system_prompt.txt`) — they resolve correctly on any OS.
- **Atomic writes are cross-platform** — `drupal_ingest.py` uses
  `tempfile.mkstemp` + `os.replace`, which is correct on every OS.
- **No OS-only syscalls** — no `os.system`, `os.fork`, `os.uname`, `signal.*`, `fcntl`,
  `winreg`, `pywin32`, `multiprocessing` fork assumptions, or shelling out to
  `cmd.exe`/`bash`. No `.bat`/`.ps1`/`.sh` scripts in the repo.
- **No hardcoded drive-letter or absolute POSIX paths in code** — the only `C:\`/`D:\`
  strings live in comments and docs.
- **OCR binaries are configurable** — `tesseract_cmd` / `poppler_path` default to empty
  and fall back to `PATH`, so OCR works wherever the binaries are installed.
- **Docker uses relative bind mounts** (`./chroma_db`, `./prompts`, …).

So this is a **cleanup-and-harden** job, not a rewrite. The two items below are the only
changes that can actually *break* on a non-author OS; the rest are consistency and
documentation hardening.

---

## Severity legend

| Level | Meaning |
|-------|---------|
| **CRITICAL** | Crashes or wrong behavior on a supported OS — must fix |
| **HIGH** | Will break for common inputs/usage on another OS |
| **MEDIUM** | Inconsistency or fragility that bites under specific conditions |
| **LOW** | Documentation / hygiene; no functional break |

---

## Findings & Fix Plan

### 1. [HIGH] Console output crashes on Windows for non-ASCII text

**Files:** [main.py:53](main.py#L53), [main.py:76](main.py#L76), [main.py:93](main.py#L93)
(and every other `print(...)` in `main.py`).

**Root cause.** Two `print()` calls emit U+2500 box-drawing characters
(`print("\n── Drupal ingestion ──…")`), and `print(f"\nAssistant: {answer}")` emits the
model's answer verbatim — MOSDAC satellite content routinely contains `°`, `µ`, `²`,
en/em dashes, and Greek letters. When stdout is **not** an interactive console (redirected
to a file or pipe: `python main.py chat > out.txt`, CI logs, Docker logs) Python encodes
with `locale.getpreferredencoding()`, which is **cp1252** on a default Windows install.
Any character outside cp1252 raises `UnicodeEncodeError: 'charmap' codec can't encode
character …` and the `ingest` / `chat` / `test` command aborts mid-run.

**Fix (pick one — recommend A+C):**

- **A. Force UTF-8 stdio at entry.** At the top of `main.py` (`chat_api/main.py` is fine
  since FastAPI returns bytes), before any `print`:
  ```python
  import sys
  for stream in (sys.stdout, sys.stderr):
      if hasattr(stream, "reconfigure"):
          stream.reconfigure(encoding="utf-8", errors="replace")
  ```
  `errors="replace"` guarantees no crash even on exotic glyphs.
- **B. Replace the two box-drawing banners** in `main.py` with ASCII (`---`), removing the
  most likely offender. Cheap, but does not protect `print(answer)`.
- **C. Document `PYTHONUTF8=1`** (PEP 540 UTF-8 mode) in the README and `.env.example` as
  the recommended Windows shell setting; the Docker image is already UTF-8 (Linux locale).

**Functionality risk:** none — output is identical on Linux/macOS; Windows simply stops
crashing.

---

### 2. [HIGH] Test fixture leaks/fails on Windows (Chroma/SQLite file locks)

**File:** [tests/conftest.py:22-30](tests/conftest.py#L22-L30) — the `tmp_chroma_dir` fixture.

**Root cause.** The fixture wraps a ChromaDB persist dir in
`with tempfile.TemporaryDirectory() as tmp:`. On exit, `TemporaryDirectory` does a
**strict** recursive delete. ChromaDB's SQLite backend keeps the `chroma.sqlite3` handle
open, and **Windows refuses to delete a file that is still open** → the fixture teardown
raises `PermissionError: [WinError 32] The process cannot access the file because it is
being used by another process`. Every test using this fixture errors on Windows even when
the assertions passed. (POSIX allows unlinking open files, so the author never saw it.)

Note the repo already knows the right pattern — [tests/test_chroma.py:37-48](tests/test_chroma.py#L37-L48)
uses `tempfile.mkdtemp()` + `shutil.rmtree(tmp_dir, ignore_errors=True)`. `conftest.py`
just wasn't updated to match.

**Fix:** make teardown tolerant (Python 3.10+ supports the keyword directly):
```python
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    ...
```
or mirror `test_chroma.py`:
```python
import shutil, tempfile
tmp = tempfile.mkdtemp()
try:
    ...
    yield tmp
finally:
    shutil.rmtree(tmp, ignore_errors=True)
```

**Functionality risk:** none — on POSIX the directory is still cleaned; on Windows the
leftover temp dir is reclaimed by the OS temp sweeper instead of crashing the test.

---

### 3. [MEDIUM] Python version is inconsistent across the repo

**Files:** [pyproject.toml](pyproject.toml) (`requires-python = ">=3.13"`),
[README.md:69](README.md#L69) ("Python 3.13+"), [Dockerfile.api:1](Dockerfile.api#L1)
(`FROM python:3.11-slim`).

**Root cause.** Not strictly an *OS* problem, but it blocks reproducible cross-platform
installs: `requires-python>=3.13` makes `pip`/`uv` refuse to install on the many
Linux/macOS machines (and the project's own Docker image) running 3.11/3.12. A contributor
on Ubuntu 22.04 (system Python 3.10/3.11) cannot install at all.

**Fix:** pick one floor and apply it everywhere. Recommend **`>=3.11`** (matches the
Docker base image and is widely available on all three OSes). Update `pyproject.toml`,
`README.md`, and keep the Dockerfile — or bump Docker to `python:3.13-slim` if 3.13 is a
hard requirement. The fixture fix in §2 uses `ignore_cleanup_errors` (3.10+), so any floor
≥3.10 is safe.

---

### 4. [MEDIUM] OCR comment is Windows-centric; setup undocumented for Linux/macOS

**Files:** [graph_rag/config.py:28-30](graph_rag/config.py#L28-L30),
[.env.example:55-57](.env.example#L55).

**Root cause.** The config comment says *"OCR — Tesseract + Poppler paths (Windows; …)"*
and `.env.example` only shows Windows paths
(`TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe`). The **code** is already
portable (empty → use `PATH`), but a Linux/macOS user has no hint on how to enable OCR.

**Fix (docs only — no code change):** broaden the comment and add install notes:
- Linux: `apt-get install tesseract-ocr poppler-utils` (already in `Dockerfile.api`),
  then leave `TESSERACT_CMD`/`POPPLER_PATH` empty.
- macOS: `brew install tesseract poppler`, leave both empty.
- Windows: install the binaries and set the two vars (forward slashes already used in the
  example, which Python accepts on Windows — good).

---

### 5. [LOW] Windows-only path examples in comments

**Files:** [docker-compose.yml:14](docker-compose.yml#L14) (`-v D:\AI_agents\neo4j_data:/data`),
[README.md:78](README.md#L78) (already shows both venv-activate forms — fine, keep it).

**Root cause.** A `D:\…` bind-mount example in a compose comment can mislead non-Windows
users (and `\A` is an escape hazard if anyone copy-pastes it into a different context). The
*active* config uses relative `./neo4j_data`, so nothing breaks — it's purely the comment.

**Fix:** rewrite the comment to a relative/OS-neutral path
(`-v ./neo4j_data:/data` or `$(pwd)/neo4j_data:/data`).

---

### 6. [LOW] No line-ending normalization (`.gitattributes` missing)

**Root cause.** With no `.gitattributes`, a Windows clone under `core.autocrlf=true`
rewrites checked-out files to CRLF. Today there are no shell scripts (which would break on
CRLF), and Python tolerates CRLF, so impact is currently nil — but the moment someone adds
a `.sh` entrypoint or a CRLF-sensitive fixture, it breaks silently and diffs get noisy.

**Fix (preventive):** add a `.gitattributes`:
```gitattributes
* text=auto eol=lf
*.png binary
*.pdf binary
*.ico binary
```
This pins LF in the repo while letting Windows working copies stay CRLF, keeping any future
`*.sh` runnable inside the Linux Docker image.

---

### 7. [INFO] Confirmed NON-issues (no action needed)

- `host: str = "0.0.0.0"` ([chat_api/config.py:44](chat_api/config.py#L44)) — binds on all
  OSes; intentional for containers.
- `neo4j_store.py:275` `text.replace("\\", "\\\\")` — Cypher string escaping, not a
  filesystem path.
- `tempfile.mkstemp` + `os.replace` atomic writes in `drupal_ingest.py` — cross-platform.
- `pathlib` `rglob`, `.suffix`, `.name`, `.stem`, `Path.mkdir(parents=True)` throughout —
  all portable.
- All `open(...)` / `read_text` / `write_text` calls pass `encoding="utf-8"` — no cp1252
  decode surprises.

---

## Execution order (smallest blast radius first)

1. **§2** — fix `tests/conftest.py` teardown (1-line, unblocks the Windows test suite).
2. **§1** — add UTF-8 stdio reconfigure at the top of `main.py` (1 block, unblocks the CLI).
3. **§3** — align Python version floor across `pyproject.toml` / `README` / Dockerfile.
4. **§6** — add `.gitattributes` (preventive).
5. **§4, §5** — documentation/comment cleanups.

## Verification checklist (run on Windows **and** Linux)

- [ ] `pytest -q` — full suite green, no `WinError 32` teardown errors.
- [ ] `python main.py test` — runs to "ALL CHECKS PASSED" with stdout redirected to a file
      (`python main.py test > out.txt`) — proves the UTF-8 fix.
- [ ] `python main.py chat` then ask a question whose answer contains `°`/`µ`/em-dash —
      no `UnicodeEncodeError`.
- [ ] `python main.py ingest --skip-drupal` — the `──` banner prints without crashing.
- [ ] Fresh `pip install -r requirement.txt` succeeds on a Python 3.11 environment.
- [ ] `docker compose up --build` — unchanged (Linux container already UTF-8).

---

## One-line summary

The app is already ~95% OS-independent; the only true cross-OS breakers are **(1)** raw
`print()` of non-ASCII to a non-UTF-8 Windows stdout and **(2)** a strict temp-dir teardown
that collides with Windows' file-locking on the Chroma SQLite file. Fix those two, align the
Python version, and add `.gitattributes`, and it runs clean everywhere.
