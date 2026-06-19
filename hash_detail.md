# Hash-Based Incremental Ingestion — A Beginner's Guide

This document explains, step by step, **how the hashing setup works** in this project,
**why** each piece exists, and **every library** used to build it. It assumes no prior
knowledge — if you know a little Python, you can follow along.

At the very end there is a **Limitations** section.

---

## 1. The Problem (Why We Built This)

When you run:

```bash
python main.py ingest
```

the program walks through your document folders (`downloads/`, `atlases_pdfs/`), and for
**every** file it:

1. **Loads** it (opens the PDF/HTML/text, sometimes runs slow OCR on scanned PDFs),
2. **Splits** it into small text "chunks",
3. **Embeds** each chunk into the vector database (ChromaDB),
4. **Extracts** knowledge from each chunk using an **LLM** (one AI call *per chunk* — this
   is the slowest, most expensive step), and stores it in the graph database (Neo4j).

The problem: **even if nothing changed**, running `ingest` again re-does all of that work
for files that were already processed. If you have 1,000 files and add just 1 new file, you
don't want to re-process all 1,001 files.

**The goal:** remember which files we already ingested, and on the next run **skip them** —
only process genuinely new files. This is called **incremental ingestion**.

---

## 2. The Core Idea: A "Fingerprint" for Each File

To know "have I already processed this exact file?", we need a way to identify a file's
*content*. We use a **hash**.

### What is a hash?

A **hash function** takes any data (the bytes of a file) and produces a short, fixed-length
string called a **hash** (or *digest*). We use **SHA-256**, which always produces a
64-character hexadecimal string, e.g.:

```
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

Two key properties make this perfect for our job:

| Property | What it means for us |
|----------|----------------------|
| **Deterministic** | The same file content **always** gives the same hash. |
| **Sensitive to change** | Change even **one byte** → a completely different hash. |

### Why hash the *content*, not the file path?

We could have remembered file **paths** (e.g. `downloads/report.pdf`) instead. But content
hashing is smarter:

- **Rename or move a file** → path changes, but **content hash stays the same** → we
  correctly recognise it as "already done" and skip it.
- **Edit a file's content** → content hash **changes** → we correctly treat it as new and
  re-ingest it.

A path-based approach gets both of these wrong. That's why we hash content.

---

## 3. The "Manifest": Where We Remember Hashes

We store the list of "already ingested" hashes in a simple file called the **manifest**:
`ingest_manifest.json`. It looks like this (a real SHA-256 hash would be 64 characters):

```json
{
  "version": 1,
  "files": {
    "e3b0c44298fc1c149afbf4c8996fb924...": {
      "source": "downloads/insat3d_handbook.pdf",
      "file_name": "insat3d_handbook.pdf",
      "chunk_count": 42,
      "ingested_at": "2026-06-11T09:30:00+00:00"
    }
  }
}
```

- The **keys** under `"files"` are the SHA-256 hashes.
- Each value stores helpful metadata: where the file came from, its name, how many chunks it
  produced, and **when** it was ingested (in UTC time).
- `"version": 1` lets us evolve the format safely in the future.

The logic is simply: **"is this file's hash a key in `files`? → yes = skip, no = ingest."**

---

## 4. Every Library Used (and Why)

The entire hashing setup uses **only Python's standard library** — nothing extra to install.
Here is each library, what it does, and exactly how we use it.

### 4.1 `hashlib` — computing the SHA-256 hash

`hashlib` is Python's built-in library for cryptographic hash functions (MD5, SHA-1,
SHA-256, etc.). We use **SHA-256**.

```python
import hashlib

sha256 = hashlib.sha256()        # create an empty SHA-256 "accumulator"
sha256.update(b"some bytes")     # feed it bytes (can be called many times)
digest = sha256.hexdigest()      # get the final 64-char hex string
```

**Why feed it in pieces with `.update()`?** Some PDFs are hundreds of megabytes. Reading the
whole file into memory at once would be wasteful and could crash on huge files. Instead we
read the file in small **64 KB blocks** and feed each block to `.update()`. The final hash is
identical to hashing the whole file at once, but we never hold more than 64 KB in memory.

### 4.2 `pathlib` — working with file paths

`pathlib` gives us the `Path` object — a modern, cross-platform way to handle file paths
(works the same on Windows, macOS, Linux).

```python
from pathlib import Path

p = Path("downloads/report.pdf")
p.suffix         # ".pdf"   → the file extension
p.name           # "report.pdf"
p.is_file()      # True if it exists and is a file
p.rglob("*")     # recursively yield EVERY item under a folder
p.parent.mkdir(parents=True, exist_ok=True)  # create folders if missing
```

`rglob("*")` is the **"recursively handling file documents"** part from the request — it
walks every subfolder and gives us every file, no matter how deeply nested.

### 4.3 `json` — saving and loading the manifest

`json` reads and writes JSON text. JSON is a simple, human-readable format that maps cleanly
to Python dictionaries.

```python
import json

text = json.dumps({"a": 1}, indent=2)   # Python dict  → pretty JSON text
data = json.loads(text)                  # JSON text    → Python dict
```

We use `json.dumps(..., indent=2)` so the manifest is **nicely formatted** and easy for a
human to read. We use `json.loads(...)` to read it back. If the file is corrupted, `loads`
raises `json.JSONDecodeError`, which we catch (see §5.2).

### 4.4 `dataclasses` — a clean container for the manifest

A **dataclass** is a shortcut for writing a class that mainly holds data. Instead of writing
a long `__init__`, you just declare the fields.

```python
from dataclasses import dataclass, field

@dataclass
class IngestionManifest:
    path: Path                                   # where the manifest file lives
    entries: dict[str, dict] = field(default_factory=dict)  # hash -> metadata
```

- `field(default_factory=dict)` means "if no value is given, start with an empty `{}`".
  (We must use `default_factory` for mutable defaults like lists/dicts — a common Python
  gotcha. Using `= {}` directly would share one dict across all instances.)

### 4.5 `datetime` — recording *when* a file was ingested

`datetime` handles dates and times. We record an **ingested-at timestamp** so you can later
see when each file was processed.

```python
from datetime import datetime, timezone

datetime.now(timezone.utc).isoformat()
# -> "2026-06-11T09:30:00.123456+00:00"
```

We use **UTC** (`timezone.utc`) — a single, universal time zone — so timestamps are
unambiguous no matter where the machine is. `.isoformat()` produces the standard **ISO 8601**
string, which is sortable and machine-readable.

### 4.6 `logging` — printing helpful progress messages

`logging` is Python's standard way to emit messages (instead of bare `print`). It lets us
show progress like *"skipped 998 already-ingested files"* and control how much detail is
shown.

```python
import logging
logger = logging.getLogger(__name__)
logger.info("Loaded %d documents", count)   # normal progress
logger.debug("Skipping %s", path.name)       # detailed, only shown in debug mode
logger.warning("Source folder does not exist: %s", folder)
```

### 4.7 `typing` — describing what functions return

`typing` provides **type hints** — annotations that document what a function expects and
returns. They don't change behaviour; they make code easier to read and let editors catch
mistakes.

```python
from typing import Iterator
from pathlib import Path

def iter_source_files(...) -> Iterator[Path]:
    ...
    yield path   # an Iterator[Path] = "produces Path objects one at a time"
```

A function that `yield`s values is a **generator** — it produces items lazily, one at a time,
instead of building a giant list in memory all at once. Good for walking thousands of files.

### 4.8 `langchain_core.documents.Document` — the only non-stdlib piece

This is the **one** external type involved (it comes from LangChain, already used throughout
the project). A `Document` holds page text plus a `metadata` dictionary:

```python
doc.page_content          # the text
doc.metadata              # a plain dict, e.g. {"source": "...", "file_name": "..."}
doc.metadata["file_hash"] = "e3b0c4..."   # we ADD the file's hash here
```

We "tag" each loaded `Document` with its `file_hash`. This tag travels with the text through
splitting and lets the pipeline know, at the end, **which files** to record in the manifest.

---

## 5. Step-by-Step: How the Code Fits Together

There are four code files involved. Here is the journey of a single `ingest` run.

### 5.1 `graph_rag/ingestion/manifest.py` — the toolkit

This new file holds two things:

**(a) `compute_file_hash(path)`** — turns a file into its SHA-256 fingerprint:

```python
_HASH_CHUNK_SIZE = 65536  # 64 KB

def compute_file_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as fh:                       # "rb" = read raw bytes
        for block in iter(lambda: fh.read(_HASH_CHUNK_SIZE), b""):
            sha256.update(block)
    return sha256.hexdigest()
```

> `iter(lambda: fh.read(65536), b"")` is a neat idiom: keep calling `fh.read(65536)` until it
> returns an empty bytes object `b""` (end of file), feeding each block to the hash.

**(b) `IngestionManifest`** — the dataclass that loads, queries, records, and saves:

- `load(path)` — reads the JSON. **If the file is missing or corrupt, it returns an empty
  manifest instead of crashing** (see §5.2).
- `is_ingested(file_hash)` — returns `True` if the hash is already known.
- `record(file_hash, source=, file_name=, chunk_count=)` — adds an entry with a UTC timestamp.
- `save()` — writes the manifest back to disk as pretty JSON, creating parent folders if needed.

### 5.2 Why "corruption = empty manifest"?

If `ingest_manifest.json` somehow gets damaged (half-written, hand-edited, disk error),
crashing the whole ingestion would be bad. Instead:

```python
try:
    data = json.loads(p.read_text(encoding="utf-8"))
    ...
except (json.JSONDecodeError, OSError, ValueError) as exc:
    logger.warning("Manifest unreadable (%s); starting fresh.", exc)
return cls(path=p, entries={})   # behave as if nothing was ingested yet
```

The worst case is that we re-ingest everything once (safe), rather than skip work we
shouldn't or crash. This is called **graceful degradation**.

### 5.3 `graph_rag/ingestion/loader.py` — skip during the folder walk

This is the **heart of the feature**. Two functions:

**`iter_source_files(folders)`** — recursively yields every supported file:

```python
SUPPORTED_SUFFIXES = PDF_SUFFIXES | HTML_SUFFIXES | TEXT_SUFFIXES  # {.pdf, .html, .txt, .md, ...}

def iter_source_files(folders=None) -> Iterator[Path]:
    for folder in folders:
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                yield path
```

**`load_all_documents(folders, *, manifest=None, force=False)`** — the decision point:

```python
for path in iter_source_files(folders):
    file_hash = None
    if manifest is not None:
        file_hash = compute_file_hash(path)            # fingerprint the file
        if not force and manifest.is_ingested(file_hash):
            skipped += 1
            continue                                    # ⭐ SKIP — never even open it

    loaded = load_file(path)                            # only NEW files reach here
    for d in loaded:
        d.metadata["file_hash"] = file_hash             # tag it for later recording
    all_docs.extend(loaded)
```

Two important design choices:

- **The skip happens *before* loading.** A skipped file is never opened, parsed, OCR'd,
  embedded, or sent to the LLM — that's where all the time savings come from.
- **Backwards compatible.** If you call `load_all_documents(folders)` with **no** manifest
  (the default), it behaves exactly as before — no hashing, no tagging. Existing tests and
  other callers are unaffected.

### 5.4 `graph_rag/ingestion/pipeline.py` — load the manifest, record at the end

At the **start** of a run, the pipeline loads the manifest and passes it to the loader:

```python
manifest = None if self.force else IngestionManifest.load(settings.ingest_manifest_path)
documents = load_all_documents(self.folders, manifest=manifest, force=self.force)
```

At the **end** of a run, it records the new files — but only if the run was **complete and
clean**:

```python
if (manifest is not None and not stats.errors
        and not self.skip_vector and not self.skip_graph):
    # group the chunks by their file_hash to get a per-file chunk_count
    for file_hash, info in files.items():
        manifest.record(file_hash, source=..., file_name=..., chunk_count=...)
    manifest.save()
```

**Why only record at the end of a clean full run?**

- If the program **crashes** halfway, nothing is recorded → next run safely retries the new
  files. We never mark a file "done" unless it truly finished.
- If you used `--skip-graph` (vectors only), the file isn't *fully* ingested, so we don't
  record it — otherwise a later full run would wrongly skip building its graph.

### 5.5 `graph_rag/config.py` and `main.py` — configuration & the `--force` switch

- **`config.py`** adds one setting: `ingest_manifest_path` (default `./ingest_manifest.json`),
  which you can override in `.env` via `INGEST_MANIFEST_PATH=...`.
- **`main.py`** adds a `--force` flag:

```bash
python main.py ingest           # incremental: skip files already in the manifest
python main.py ingest --force   # ignore the manifest: re-ingest EVERYTHING
```

---

## 6. A Complete Example Walkthrough

Imagine `downloads/` has `a.pdf` and `b.pdf`.

1. **First run:** `python main.py ingest`
   - No manifest exists yet → both files are new → both are loaded, embedded, extracted.
   - At the end, the manifest records two entries (one hash each).
2. **Second run (nothing changed):** `python main.py ingest`
   - Both hashes are already in the manifest → **both skipped**.
   - Log: `Loaded 0 documents ... skipped 2 already-ingested file(s)`.
   - Finishes in **seconds** — no embedding, no LLM calls.
3. **You add `c.pdf` and run again:**
   - `a.pdf` and `b.pdf` → skipped; only `c.pdf` is processed.
   - The manifest gains exactly one new entry.
4. **You edit `a.pdf`:**
   - Its content changed → new hash → not in the manifest → it is re-ingested as if new.
5. **Force a full rebuild:** `python main.py ingest --force`
   - The manifest is ignored; all files are processed again.

---

## 7. How We Tested It

The tests in `tests/test_manifest.py` need **no external services** (no database, no AI):

- Same content (different paths) → same hash; one byte changed → different hash.
- Save a manifest, load it back, confirm `is_ingested` works.
- A corrupt manifest loads as empty (no crash).
- The loader skips a file whose hash is pre-seeded, loads new files, and tags them.
- With no manifest, everything loads exactly as before (backwards compatibility).

All pass alongside the existing loader and pipeline tests.

---

## 8. Limitations

This setup is intentionally simple. Be aware of the following:

1. **No cleanup of old data when a file changes.** If you edit a file in place, the new
   version is re-ingested (good), but the **old** version's chunks (in ChromaDB) and graph
   nodes (in Neo4j) are **left behind** as orphans. Over time, heavy editing of the same
   files can accumulate stale data. (This was a deliberate, approved scope decision.)

2. **Deleting a source file does not remove its data.** If you delete `a.pdf` from disk, its
   chunks/graph nodes and its manifest entry remain. The manifest only ever grows; it is not
   pruned when files disappear.

3. **All-or-nothing recording per run.** Hashes are saved **only at the very end of a clean
   full run**. If the run errors out or you interrupt it (Ctrl+C) after processing 900 of
   1,000 new files, **none** of those 900 are recorded, so the next run reprocesses all
   1,000. (Safe, but not maximally efficient on partial failures.)

4. **`--skip-vector` / `--skip-graph` runs never update the manifest.** Because such a run
   doesn't fully ingest a file, nothing is recorded — by design. Don't expect those runs to
   "mark files done".

5. **Hashing cost on every run.** To decide whether to skip a file, we must read its bytes and
   compute SHA-256 **every run**. For very large files this disk read is fast but not free —
   it is far cheaper than re-embedding + LLM extraction, but it is not zero.

6. **Whole-file granularity only.** If a 500-page PDF changes by one word, the whole file is
   re-ingested — there is no per-page or per-chunk change detection at the file level.

7. **No concurrency protection.** The manifest is a single JSON file written at the end of a
   run. Running two `ingest` processes at the same time on the same manifest could cause one
   to overwrite the other's entries. Run ingestion one process at a time.

8. **Theoretical hash collisions.** Two *different* files producing the same SHA-256 hash is
   possible in theory, but astronomically unlikely (1 in 2²⁵⁶). In practice this is not a
   concern.

9. **Content-only identity.** Two files with **identical content** but different names are
   treated as the same file — only the first is ingested, the second is skipped. This is
   usually what you want (it avoids duplicate work), but be aware of it.
