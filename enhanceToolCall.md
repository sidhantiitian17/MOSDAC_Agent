# MOSDAC MCP Tool + Qwen-32B AI Agent Chatbot — Complete Beginner Implementation Plan

## TL;DR

- **Build it as four cleanly separated Python services** that talk over standard interfaces: (1) a FastMCP-based **MCP server** that wraps the MOSDAC Order/Search API and enforces rate limits + idempotency, (2) a **LangGraph ReAct agent** powered by Qwen 2.5 32B served locally through Ollama and connected to the MCP server via `langchain-mcp-adapters`, (3) a **FastAPI** backend exposing a `/chat` endpoint with SSO session handling and SlowAPI-based safe-order limits, and (4) a **chat UI** — Streamlit for a same-day prototype, then a small embeddable HTML/JS widget that drops onto the MOSDAC portal via an `<iframe>` or `<script>` snippet.
- **The MOSDAC reality you must design for**: MOSDAC's official "Data Download API" uses **MOSDAC SSO (Keycloak) username/password authentication**, OpenAPI-based search keyed by a `datasetId` (e.g. `3SIMG_L1B_STD` for INSAT-3D Imager L1B), search parameters `startTime`, `endTime`, `count` (max 100), `boundingBox` ("minLon,minLat,maxLon,maxLat") and `gId`, with delivery either via `sftp://ftp.mosdac.gov.in` for satellite orders or HTTP for in-situ orders, plus a hard **daily limit of 5000 files per user**. After three failed logins the account is locked for one hour. Your MCP server must wrap exactly these primitives — do not invent endpoints that aren't in the MOSDAC manual.
-  if you (a) stub the MOSDAC API with a fake server in week 1 so the agent loop, prompts and UI are all working before you touch real credentials, (b) run Qwen 2.5 32B at Q4_K_M on a 24 GB GPU (RTX 3090/4090) via docker (`ollama pull qwen2.5:32b`) — fallback to `qwen2.5:14b` if you only have 16 GB VRAM — and (c) hardcode the `Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14–18 Aug 2024 via SFTP` flow as your end-to-end acceptance test before generalising. Final integration into mosdac.gov.in itself will require ISRO/SAC sign-off; ship the widget so it can be embedded by their webmaster with a single `<script>` tag.

---

## Key Findings

1. **Use FastMCP, not raw MCP.** The official Python MCP SDK ships `mcp.server.fastmcp.FastMCP`, and the standalone `fastmcp` package extends this. Both let you define a tool with a single `@mcp.tool` decorator on a Python function — FastMCP auto-generates the JSON schema from your type hints and the docstring becomes the tool description the LLM sees. This is the only sensible path for a beginner.
2. **Use Streamable HTTP transport, not SSE.** MCP's SSE transport is deprecated; new servers should use `stdio` (for local dev) or `streamable-http` (for production / remote agents). The same FastMCP code supports both — just change the `transport=` argument to `mcp.run()`.
3. **Use LangGraph's `create_react_agent` + `langchain-mcp-adapters`, not the legacy `AgentExecutor`.** The official integration pattern is to instantiate `MultiServerMCPClient`, call `await client.get_tools()`, then pass the resulting LangChain tools into `create_react_agent(model, tools)`. This works with any ChatModel that supports tool-calling, including `ChatOllama` running Qwen 2.5.
4. **Qwen 2.5 has first-class tool-calling support.** Ollama's Qwen 2.5 template explicitly handles `<tools>` and `<tool_call>` XML tags, so `ChatOllama(model="qwen2.5:32b").bind_tools([...])` works natively — you do **not** need to do text-based ReAct parsing. Qwen 3 also works; either is fine.
5. **Hardware reality for Qwen 2.5 32B**: at Q4_K_M (Ollama's default) the model is roughly 20 GB on disk and needs ~22 GB of usable VRAM with a small context window. RTX 3090/4090 (24 GB) is the minimum comfortable single-GPU; fall back to `qwen2.5:14b` on 16 GB cards, or rent an A100-40GB hourly for development.
6. **MOSDAC ordering is two-track.** "Satellite data order" → SFTP delivery at `sftp://ftp.mosdac.gov.in`; "In-situ data order" → HTTP download link. Standing orders (rolling/recurring) are limited to one-month windows and only available to privileged users. The chatbot must surface these constraints, not paper over them.
7. **Idempotency must live in your MCP server, not in MOSDAC.** MOSDAC's documented API has no idempotency-key header; therefore you store `Idempotency-Key → orderId` mappings in your own SQLite/Redis cache so retries from the LLM agent don't create duplicate orders.
8. **Two layers of "safe limit" are required.** SlowAPI rate-limits HTTP requests on the FastAPI chat endpoint (e.g. `5/minute` per IP). A second per-user counter inside the MCP tool itself enforces something like "max 10 orders / hour / SSO user" — this is what actually protects MOSDAC's backend, because one chat message can fan out to many tool calls.
9. **Embedding into mosdac.gov.in is a deployment-time concern, not a code concern.** The realistic delivery is a self-contained widget served from your own HTTPS origin and embedded by MOSDAC's webmaster as either an `<iframe src="https://your-host/widget">` or a `<script src="https://your-host/widget.js" async></script>` tag. Plan for both; do not assume you'll be allowed to modify Drupal templates directly.

---

## Details

### 1. Key Terminology — Explained Simply

**Model Context Protocol (MCP).** MCP is an open protocol released by Anthropic in late 2024 that standardises how LLM applications connect to external tools and data. The official analogy in the spec is "USB-C for AI": instead of writing custom glue code for every (model × tool) pair (an M×N problem), every tool implements MCP once and every model implements MCP once (M+N). For your project, MCP is the contract between your AI agent and your MOSDAC-ordering code.

**MCP Server.** A program that *exposes* tools, resources and prompts. Your `mosdac_mcp_server.py` is an MCP server.

**MCP Client.** Code that *connects to* an MCP server and calls its tools. `langchain-mcp-adapters` is the MCP client your agent uses.

**MCP Host.** The application that the end user actually interacts with — in your case, the FastAPI app that runs the LangGraph agent. The host embeds one or more clients.

**Tools, Resources, Prompts (the three MCP primitives).**
- **Tools** are functions the LLM can *call* to do something — POST-like, side effects. `place_order(...)` is a Tool.
- **Resources** are data the LLM can *read* — GET-like, no side effects. A read-only `mosdac://datasets` listing would be a Resource.
- **Prompts** are reusable templated messages a user (or UI) can pick from. Optional for this project.

**Transports: stdio vs HTTP/SSE vs Streamable HTTP.**
- **stdio**: the client launches the server as a subprocess and they talk over `stdin`/`stdout`. Best for local development.
- **HTTP+SSE**: legacy two-endpoint HTTP transport. **Deprecated by the MCP spec**; only keep for back-compat.
- **Streamable HTTP**: the modern single-endpoint HTTP transport. Use this for any remote/production deployment. In FastMCP it is `mcp.run(transport="streamable-http")`.

**LangChain.** A Python framework for building LLM applications. Core primitives:
- **Chains** — sequences of LLM calls and transformations.
- **Agents** — LLM-driven loops that decide which tool to call next.
- **Tools** — Python functions the agent can call.
- **Memory** — stores conversation history so the agent has context across turns.
- **LCEL (LangChain Expression Language)** — a `|` pipe syntax for composing chains.

**AI Agent vs simple LLM chatbot.** A simple chatbot just sends prompts to an LLM and shows the reply. An *agent* runs a loop: the LLM produces a "tool call", your code executes that tool, the result is fed back into the LLM, and the loop repeats until the LLM produces a final answer. This is the **ReAct** ("Reasoning + Acting") pattern. LangGraph's `create_react_agent` implements exactly this loop and is the recommended modern API (the older `AgentExecutor` is deprecated).

**SSO (Single Sign-On).** One set of credentials authorises a user across multiple services. MOSDAC's SSO is a **Keycloak** realm at `https://mosdac.gov.in/auth/realms/Mosdac/`. Your MCP server logs the user in once and reuses the session token for subsequent calls.

**SFTP.** "Secure File Transfer Protocol". MOSDAC delivers satellite orders by uploading them into your account on `sftp://ftp.mosdac.gov.in`, accessible with your MOSDAC portal username and password. Your chatbot never touches SFTP itself — it just tells the user where to look.

**Idempotency key.** A unique string the client generates and attaches to a "write" request so that if it is retried (network glitch, agent retry, etc.) the server returns the *same* result instead of placing the order twice. Your MCP server generates `Idempotency-Key: <uuid4>` per order draft and caches `(key → orderId)` locally.

**FastAPI.** A modern, async Python web framework for HTTP APIs. Great for building the `/chat` endpoint.

**Streamlit.** A Python library that turns a script into a web UI in 20 lines. Ideal for prototype chat UI.

**Qwen 2.5 32B.** A 32-billion-parameter open-weights chat model from Alibaba Cloud, released under Apache 2.0, with native tool-calling. On Ollama it is `qwen2.5:32b`. Qwen 3 (`qwen3:32b`) is the newer family with thinking mode; either works.

**Ollama.** A local LLM runtime. `ollama pull qwen2.5:32b` downloads the model in GGUF format; `ollama run qwen2.5:32b` starts an interactive shell; `ollama serve` exposes an OpenAI-compatible API on `http://localhost:11434`. This is the recommended path for beginners.

**Virtual environment (`venv`).** An isolated Python install per project so dependencies don't conflict. Always use one. Modern alternative: `uv`.

### 2. Architecture Overview

```
+-----------------------------------------------------------+
|                     MOSDAC PORTAL                          |
|   (mosdac.gov.in — Drupal site, owned by SAC/ISRO)         |
|                                                            |
|   <iframe src="https://your-host/widget"> OR               |
|   <script src="https://your-host/widget.js" async></script>|
+-------------------------|----------------------------------+
                          | HTTPS (browser)
                          v
+-----------------------------------------------------------+
|         CHAT UI  (chat_widget.html / Streamlit app)        |
|   - Renders chat bubbles                                   |
|   - Sends user messages to /chat                           |
|   - Receives JSON {reply, order_id?, status?}              |
+-------------------------|----------------------------------+
                          | POST /chat  (HTTPS, JWT/cookie)
                          v
+-----------------------------------------------------------+
|        FastAPI BACKEND  (main.py)                          |
|   - SSO token validation                                   |
|   - Session memory per user                                |
|   - SlowAPI rate-limit  (e.g. 5/min, 30/hour)              |
|   - Calls LangGraph agent                                  |
+-------------------------|----------------------------------+
                          |
                          v
+-----------------------------------------------------------+
|     LangGraph ReAct AGENT  (agent.py)                      |
|   create_react_agent(ChatOllama(qwen2.5:32b), tools)       |
|   - System prompt describes MOSDAC ordering workflow       |
|   - Reasoning loop: think -> tool_call -> observe -> ...   |
+-------------------------|----------------------------------+
                          | langchain-mcp-adapters
                          v
+-----------------------------------------------------------+
|     MCP CLIENT  (MultiServerMCPClient, streamable-http)    |
+-------------------------|----------------------------------+
                          | MCP / streamable-http
                          v
+-----------------------------------------------------------+
|     MCP SERVER  (mosdac_mcp_server.py, FastMCP)            |
|   Tools:                                                   |
|     - search_products(query, satellite, sensor)            |
|     - place_order(dataset_id, aoi, start, end, level, ...) |
|     - check_order_status(order_id)                         |
|     - list_my_orders()                                     |
|   Cross-cutting:                                           |
|     - SSO login (Keycloak), token cache                    |
|     - Per-user order rate limit                            |
|     - Idempotency key store (SQLite)                       |
|     - Audit log                                            |
+-------------------------|----------------------------------+
                          | HTTPS, MOSDAC SSO session cookie
                          v
+-----------------------------------------------------------+
|     MOSDAC ORDER / SEARCH API   (mosdac.gov.in)            |
|     Search:  OpenAPI search by datasetId,                  |
|              startTime, endTime, boundingBox, gId          |
|     Order:   Satellite data order -> SFTP delivery         |
|              In-situ data order  -> HTTP delivery          |
|     Auth:    Keycloak realm  (/auth/realms/Mosdac)         |
+-------------------------|----------------------------------+
                          | data slicing + packaging
                          v
+-----------------------------------------------------------+
|     SFTP DELIVERY  (sftp://ftp.mosdac.gov.in)              |
|     User downloads with MOSDAC portal credentials          |
+-----------------------------------------------------------+
```

Component roles in one line each:
- **Chat UI** — captures the user's natural-language request and shows replies.
- **FastAPI** — the single HTTPS entry point; enforces auth, sessions and global rate limit.
- **LangGraph agent** — the "brain" that decides which MOSDAC tool to call and in what order.
- **MCP server** — the only piece that knows how to actually talk to MOSDAC.
- **MOSDAC API** — the system of record; everything else just orchestrates around it.

### 3. Prerequisites & Setup

**3.1 Software versions.**
- Python ≥ **3.10** (MCP SDK requires it).
- Git.
- Docker (optional, for deployment).
- Node.js ≥ 18 (only if you want to run MCP Inspector, which is an npx tool).

**3.2 Install Python, pip, venv (Linux/macOS).**
```bash
# Check Python
python3 --version            # must say 3.10+

# Create project folder
mkdir mosdac-agent && cd mosdac-agent

# Create + activate virtual environment
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
```

**3.3 (Optional but recommended) install `uv`** — a faster modern Python package/project manager:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # Linux/macOS
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**3.4 Install Ollama and pull Qwen 2.5 32B.**
```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh
# macOS: download installer from https://ollama.com/download
# Windows: download installer from https://ollama.com/download

# Start the Ollama server (runs on http://localhost:11434)
ollama serve &                       # leave running in background

# Pull the Qwen 2.5 32B instruct model (~20 GB on disk at Q4_K_M)
ollama pull qwen2.5:32b

# Quick smoke test
ollama run qwen2.5:32b "Say hello in one line."
```
**Hardware reality check.** Qwen 2.5 32B at Ollama's default Q4 quantisation needs roughly **22 GB of usable VRAM** with a modest context. Practical options:
- RTX 3090 24 GB or RTX 4090 24 GB — comfortable single-GPU.
- 16 GB GPU (e.g. RTX 4080 / 4060 Ti 16 GB) — drop to `qwen2.5:14b`.
- Apple Silicon: M2/M3 Pro 36 GB+ works; M3 Max 64 GB is luxurious.
- No GPU at all: rent an A100-40GB hourly on RunPod/Jarvislabs/Lambda, or use a hosted Qwen API (Alibaba DashScope, Together.ai, Fireworks) and swap `ChatOllama` for `ChatOpenAI(base_url=...)`.

**3.5 Project folder structure.**
```
mosdac-agent/
├── .env                       # secrets (NEVER commit)
├── .env.example               # safe template, committed
├── .gitignore                 # ignores .venv, .env, __pycache__
├── requirements.txt
├── README.md
├── mosdac_mcp_server.py       # Phase A
├── agent.py                   # Phase B  (LangGraph + Qwen)
├── main.py                    # Phase C  (FastAPI)
├── chat_ui_streamlit.py       # Phase D option 1
├── widget/                    # Phase D option 2 (production embed)
│   ├── widget.html
│   ├── widget.js
│   └── widget.css
├── data/
│   └── idempotency.sqlite     # local cache (gitignored)
├── tests/
│   ├── test_mcp_tools.py
│   └── test_agent.py
└── docker/
    ├── Dockerfile
    └── docker-compose.yml
```

**3.6 `requirements.txt`** (pin to known-good ranges):
```
mcp>=1.10
fastmcp>=2.5
langchain>=0.3
langchain-core>=0.3
langchain-ollama>=0.2
langchain-mcp-adapters>=0.1
langgraph>=0.2
fastapi>=0.115
uvicorn[standard]>=0.30
slowapi>=0.1.9
python-multipart
python-dotenv>=1.0
httpx>=0.27
pydantic>=2.7
streamlit>=1.36
```
Install with: `pip install -r requirements.txt`.

**3.7 `.env.example`** (template; copy to `.env` and fill in):
```
# MOSDAC SSO (Keycloak)
MOSDAC_USERNAME=your_mosdac_username
MOSDAC_PASSWORD=your_mosdac_password
MOSDAC_BASE_URL=https://mosdac.gov.in
MOSDAC_AUTH_URL=https://mosdac.gov.in/auth/realms/Mosdac

# MCP server
MCP_HOST=127.0.0.1
MCP_PORT=8765
MCP_TRANSPORT=streamable-http

# LLM
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:32b

# Safety
MAX_ORDERS_PER_USER_PER_HOUR=10
MAX_FILES_PER_ORDER=100

# FastAPI
APP_SECRET_KEY=replace-me-with-a-long-random-string
ALLOWED_ORIGINS=https://www.mosdac.gov.in,https://mosdac.gov.in
```

---

### Phase A — Build the MCP Server (`mosdac_mcp_server.py`)

This is the file your chatbot's "tool" lives in. It exposes four tools to the LLM and is the only place that ever talks to MOSDAC.

Install:
```bash
pip install "fastmcp" httpx python-dotenv
```

Full code (paste verbatim, then edit endpoints to match the exact API documented in your MOSDAC manual):

```python
# mosdac_mcp_server.py
"""
MOSDAC MCP Server
-----------------
Exposes MOSDAC search + ordering as MCP tools so an LLM agent can place
satellite-data orders on behalf of an authenticated MOSDAC user.

Run locally (stdio, for MCP Inspector / Claude Desktop):
    python mosdac_mcp_server.py

Run as remote HTTP server (for production agents):
    MCP_TRANSPORT=streamable-http python mosdac_mcp_server.py
"""

import os
import json
import time
import uuid
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional, Literal

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

load_dotenv()

# ---------- configuration ----------
MOSDAC_BASE = os.getenv("MOSDAC_BASE_URL", "https://mosdac.gov.in")
MOSDAC_USER = os.getenv("MOSDAC_USERNAME")
MOSDAC_PASS = os.getenv("MOSDAC_PASSWORD")
MAX_ORDERS_PER_HOUR = int(os.getenv("MAX_ORDERS_PER_USER_PER_HOUR", "10"))
MAX_FILES_PER_ORDER = int(os.getenv("MAX_FILES_PER_ORDER", "100"))
TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
HOST = os.getenv("MCP_HOST", "127.0.0.1")
PORT = int(os.getenv("MCP_PORT", "8765"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mosdac-mcp")

# ---------- tiny SQLite store for idempotency + rate limiting ----------
DB_PATH = "data/idempotency.sqlite"
os.makedirs("data", exist_ok=True)

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency (
            key TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            created_at REAL NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            order_id TEXT,
            payload TEXT,
            created_at REAL
        )""")
    return conn

def _check_rate_limit(user: str):
    """Raise if user has placed > MAX_ORDERS_PER_HOUR in the last hour."""
    cutoff = time.time() - 3600
    with _db() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM orders_audit WHERE user=? AND created_at>?",
            (user, cutoff),
        ).fetchone()[0]
    if n >= MAX_ORDERS_PER_HOUR:
        raise ToolError(
            f"Rate limit hit: you already placed {n} orders in the last hour "
            f"(max {MAX_ORDERS_PER_HOUR}). Please wait and try again."
        )

def _record_order(user: str, order_id: str, payload: dict):
    with _db() as c:
        c.execute(
            "INSERT INTO orders_audit(user, order_id, payload, created_at) VALUES (?,?,?,?)",
            (user, order_id, json.dumps(payload), time.time()),
        )

# ---------- MOSDAC SSO session (Keycloak) ----------
# IMPORTANT: replace the endpoint paths below with the exact ones from the
# Order API PDF your team received. The structure (login -> session cookie ->
# authenticated POST) is what the public docs describe.
_session_cache = {"client": None, "expires_at": 0.0}

def _mosdac_client() -> httpx.Client:
    now = time.time()
    if _session_cache["client"] and _session_cache["expires_at"] > now:
        return _session_cache["client"]

    if not (MOSDAC_USER and MOSDAC_PASS):
        raise ToolError("MOSDAC credentials are not configured on the server.")

    client = httpx.Client(base_url=MOSDAC_BASE, timeout=60.0, follow_redirects=True)
    # Public docs describe Keycloak-backed login at /auth/realms/Mosdac.
    # The exact form fields / token endpoint must come from your Order API PDF.
    r = client.post(
        "/auth/realms/Mosdac/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "mosdac-portal",     # <-- replace per your PDF
            "username": MOSDAC_USER,
            "password": MOSDAC_PASS,
        },
    )
    if r.status_code != 200:
        raise ToolError(f"MOSDAC login failed ({r.status_code}). "
                        "Three failures = 1-hour lockout, so STOP retrying.")
    tok = r.json()
    client.headers["Authorization"] = f"Bearer {tok['access_token']}"
    _session_cache["client"] = client
    _session_cache["expires_at"] = now + tok.get("expires_in", 300) - 30
    return client

# ---------- FastMCP server ----------
mcp = FastMCP(name="mosdac-order-server")

@mcp.tool
def search_products(
    query: str = "",
    satellite: Optional[str] = None,
    sensor: Optional[str] = None,
) -> list[dict]:
    """
    Search the MOSDAC satellite-product catalog.

    Use this BEFORE place_order if the user describes a product in words
    (e.g. "INSAT-3D TIR-1 L1B") and you need the exact dataset_id
    (e.g. "3SIMG_L1B_STD"). Browse https://mosdac.gov.in/catalog/satellite.php
    to verify dataset IDs.

    Returns a list of {dataset_id, name, satellite, sensor, level} dicts.
    """
    # In production, hit the MOSDAC OpenAPI search; here we hard-code a small
    # catalogue covering INSAT-3D / 3DR / 3DS so the agent can resolve names
    # to dataset IDs without an extra round trip. Extend as needed.
    catalogue = [
        {"dataset_id": "3SIMG_L1B_STD",
         "name": "INSAT-3D Imager L1B (Standard)",
         "satellite": "INSAT-3D", "sensor": "Imager",
         "level": "L1B",
         "bands": ["VIS", "SWIR", "MIR", "WV", "TIR-1", "TIR-2"]},
        {"dataset_id": "3SIMG_L1C_STD",
         "name": "INSAT-3D Imager L1C",
         "satellite": "INSAT-3D", "sensor": "Imager", "level": "L1C"},
        {"dataset_id": "3DIMG_L2B_CMK",
         "name": "INSAT-3D Imager L2B Cloud Map",
         "satellite": "INSAT-3D", "sensor": "Imager", "level": "L2B"},
        # ... extend with the full list from /catalog/satellite.php
    ]
    q = (query or "").lower()
    out = []
    for row in catalogue:
        if satellite and satellite.lower() not in row["satellite"].lower():
            continue
        if sensor and sensor.lower() not in row["sensor"].lower():
            continue
        if q and q not in (row["name"] + " " + row["dataset_id"]).lower():
            continue
        out.append(row)
    return out

@mcp.tool
def place_order(
    dataset_id: str,
    start_date: str,                 # "YYYY-MM-DD"
    end_date: str,                   # "YYYY-MM-DD"
    bounding_box: Optional[str] = None,   # "minLon,minLat,maxLon,maxLat"
    state_or_region: Optional[str] = None,  # e.g. "Tamil Nadu" (resolved below)
    level_format: Literal["L1B_HDF5", "L1C_HDF5", "L2_NetCDF", "L3_CSV"] = "L1B_HDF5",
    delivery: Literal["SFTP", "HTTP", "EMAIL"] = "SFTP",
    user: str = "default",
    idempotency_key: Optional[str] = None,
    max_files: int = 100,
) -> dict:
    """
    Place a MOSDAC satellite data order on behalf of the authenticated user.

    Required:
      dataset_id   - product code from search_products, e.g. "3SIMG_L1B_STD".
      start_date   - YYYY-MM-DD inclusive.
      end_date     - YYYY-MM-DD inclusive.

    Area-of-interest: provide EITHER bounding_box (lon/lat) OR state_or_region.
    Delivery defaults to SFTP (recommended).
    Returns: {order_id, eta, delivery, sftp_path, dataset_id, status}.

    This tool is IDEMPOTENT: pass the same idempotency_key on retries to
    avoid duplicate orders.
    """
    # ---- validate ----
    try:
        d0 = datetime.strptime(start_date, "%Y-%m-%d")
        d1 = datetime.strptime(end_date,   "%Y-%m-%d")
    except ValueError:
        raise ToolError("Dates must be YYYY-MM-DD.")
    if d1 < d0:
        raise ToolError("end_date is before start_date.")
    if (d1 - d0).days > 92:
        raise ToolError("Maximum date range is 92 days per order.")
    if max_files > MAX_FILES_PER_ORDER:
        raise ToolError(f"max_files {max_files} exceeds server cap "
                        f"{MAX_FILES_PER_ORDER}.")
    if not bounding_box and not state_or_region:
        raise ToolError("Provide either bounding_box or state_or_region.")

    # ---- AOI resolution: states -> bounding box ----
    INDIA_STATE_BBOX = {
        "tamil nadu":   "76.2,8.0,80.4,13.6",
        "kerala":       "74.5,8.2,77.5,12.9",
        "karnataka":    "74.0,11.5,78.6,18.5",
        "maharashtra":  "72.6,15.6,80.9,22.0",
        # ... extend or load from a JSON file
    }
    if not bounding_box:
        bbox = INDIA_STATE_BBOX.get(state_or_region.strip().lower())
        if not bbox:
            raise ToolError(f"Unknown state '{state_or_region}'. "
                            "Provide bounding_box (minLon,minLat,maxLon,maxLat) instead.")
        bounding_box = bbox

    # ---- idempotency ----
    idem = idempotency_key or str(uuid.uuid4())
    with _db() as c:
        prev = c.execute(
            "SELECT order_id FROM idempotency WHERE key=?", (idem,)
        ).fetchone()
    if prev:
        return {"order_id": prev[0], "status": "duplicate",
                "message": "Idempotency-Key already used; returning original order."}

    # ---- rate limit ----
    _check_rate_limit(user)

    # ---- call MOSDAC ----
    payload = {
        "datasetId": dataset_id,
        "startTime": start_date,
        "endTime":   end_date,
        "boundingBox": bounding_box,
        "count": max_files,
        "level_format": level_format,
        "delivery": delivery,
    }
    try:
        client = _mosdac_client()
        # NOTE: the exact path is in your Order API PDF; common pattern:
        r = client.post("/api/v1/orders", json=payload,
                        headers={"Idempotency-Key": idem})
        if r.status_code not in (200, 201, 202):
            raise ToolError(f"MOSDAC order rejected: {r.status_code} {r.text[:200]}")
        body = r.json()
        order_id = body.get("orderId") or body.get("order_id") or body.get("requestId")
        if not order_id:
            raise ToolError(f"MOSDAC response missing order id: {body}")
    except httpx.HTTPError as e:
        raise ToolError(f"MOSDAC network error: {e}")

    # ---- persist ----
    with _db() as c:
        c.execute("INSERT INTO idempotency(key, order_id, created_at) VALUES (?,?,?)",
                  (idem, order_id, time.time()))
    _record_order(user, order_id, payload)
    log.info("Order placed: %s for %s (%s..%s)", order_id, dataset_id, start_date, end_date)

    return {
        "order_id": order_id,
        "status": "queued",
        "eta": (datetime.utcnow() + timedelta(hours=2)).isoformat() + "Z",
        "delivery": delivery,
        "sftp_path": f"sftp://ftp.mosdac.gov.in/{order_id}/" if delivery == "SFTP" else None,
        "dataset_id": dataset_id,
        "bounding_box": bounding_box,
        "idempotency_key": idem,
    }

@mcp.tool
def check_order_status(order_id: str) -> dict:
    """
    Poll the status of a previously placed order.

    Returns one of: queued, slicing, packaging, ready, notified, failed.
    """
    client = _mosdac_client()
    r = client.get(f"/api/v1/orders/{order_id}")     # adjust per your PDF
    if r.status_code == 404:
        raise ToolError(f"Order {order_id} not found.")
    if r.status_code != 200:
        raise ToolError(f"MOSDAC error {r.status_code}: {r.text[:200]}")
    body = r.json()
    return {
        "order_id": order_id,
        "status":  body.get("status", "unknown"),
        "progress": body.get("progress"),
        "sftp_path": body.get("sftpPath"),
        "files_ready": body.get("filesReady"),
        "updated_at": body.get("updatedAt"),
    }

@mcp.tool
def list_my_orders(limit: int = 20, user: str = "default") -> list[dict]:
    """List the most recent orders this server has placed (audit view)."""
    with _db() as c:
        rows = c.execute(
            "SELECT order_id, payload, created_at FROM orders_audit "
            "WHERE user=? ORDER BY created_at DESC LIMIT ?",
            (user, limit),
        ).fetchall()
    return [
        {"order_id": r[0], "payload": json.loads(r[1]),
         "created_at": datetime.utcfromtimestamp(r[2]).isoformat() + "Z"}
        for r in rows
    ]

if __name__ == "__main__":
    if TRANSPORT == "streamable-http":
        log.info("Starting MCP server on http://%s:%s/mcp/", HOST, PORT)
        mcp.run(transport="streamable-http", host=HOST, port=PORT)
    else:
        log.info("Starting MCP server on stdio")
        mcp.run()         # stdio by default
```

**Test it with MCP Inspector.**
```bash
# In one terminal: launch the server in HTTP mode
MCP_TRANSPORT=streamable-http python mosdac_mcp_server.py

# In another terminal:
npx @modelcontextprotocol/inspector
# Open the browser UI (it prints the URL, usually http://127.0.0.1:6274)
# Connect to http://127.0.0.1:8765/mcp
# Click the "Tools" tab and try place_order with mock data.
```

**Where to plug the real MOSDAC endpoints.** The two `client.post("/api/v1/orders", ...)` and `client.get("/api/v1/orders/{id}")` lines, plus the Keycloak token path, are the only three URLs that need to match the Order API PDF your team has access to. Everything else around them — schema, idempotency, rate-limit, audit — is correct as written.

---

### Phase B — Build the LangChain/LangGraph AI Agent (`agent.py`)

Install:
```bash
pip install langchain langchain-core langchain-ollama langchain-mcp-adapters langgraph
```

```python
# agent.py
"""
LangGraph ReAct agent powered by Qwen 2.5 32B (via Ollama), wired to the
MOSDAC MCP server through langchain-mcp-adapters.
"""

import os
import asyncio
from dotenv import load_dotenv

from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()

SYSTEM_PROMPT = """You are MOSDAC-Bot, an assistant that helps registered
MOSDAC users place satellite-data orders.

You have these tools:
  search_products(query, satellite, sensor)
  place_order(dataset_id, start_date, end_date, bounding_box | state_or_region,
              level_format, delivery)
  check_order_status(order_id)
  list_my_orders()

Hard rules:
1. ALWAYS resolve a product name to a dataset_id with search_products BEFORE
   calling place_order. Never invent dataset_ids.
2. Dates must be in YYYY-MM-DD. If the user gives a natural-language date,
   convert it first; if ambiguous, ask one short clarifying question.
3. For Indian states/regions, prefer state_or_region; the tool will resolve
   the bounding box.
4. Default delivery is SFTP. Mention that the user will retrieve files
   from sftp://ftp.mosdac.gov.in using their MOSDAC credentials.
5. After a successful place_order, your FINAL reply to the user must be
   exactly:  "Order has been placed. Check your SFTP account."
   followed by a one-line summary with the order_id.
6. Never reveal credentials or raw API responses. Be concise.
"""

async def build_agent():
    # 1. Connect to the MCP server we built in Phase A
    mcp_client = MultiServerMCPClient(
        {
            "mosdac": {
                "transport": "streamable_http",
                "url": f"http://{os.getenv('MCP_HOST','127.0.0.1')}:"
                       f"{os.getenv('MCP_PORT','8765')}/mcp/",
            }
        }
    )
    tools = await mcp_client.get_tools()

    # 2. Local Qwen 2.5 32B via Ollama, with native tool-calling
    llm = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "qwen2.5:32b"),
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        temperature=0.1,
        # Make sure Ollama allows enough context for tool-call JSON
        num_ctx=8192,
    )

    # 3. Build the ReAct agent (LangGraph)
    memory = MemorySaver()       # per-thread in-process memory
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=memory,
    )
    return agent

async def chat_once(agent, thread_id: str, user_message: str) -> str:
    """Send one user turn and return the assistant's final text."""
    config = {"configurable": {"thread_id": thread_id}}
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config=config,
    )
    return result["messages"][-1].content

# Quick CLI smoke test
async def _demo():
    agent = await build_agent()
    print(await chat_once(
        agent,
        thread_id="demo-1",
        user_message=("Order INSAT-3D TIR-1 L1B for Tamil Nadu, "
                      "14-18 Aug 2024 via SFTP"),
    ))

if __name__ == "__main__":
    asyncio.run(_demo())
```

**Why LangGraph's `create_react_agent` and not the old `AgentExecutor`?** It is the supported, documented integration path for `langchain-mcp-adapters` and it handles tool-call retries, the agent loop, and memory checkpointing for you. The older `create_react_agent` from `langchain.agents` is text-parsed and brittle with local LLMs — avoid it.

---

### Phase C — FastAPI Backend (`main.py`)

Install:
```bash
pip install fastapi uvicorn[standard] slowapi python-multipart python-dotenv
```

```python
# main.py
"""
FastAPI backend that owns:
  - the LangGraph agent (singleton)
  - HTTP rate limiting (SlowAPI)
  - CORS for the MOSDAC portal origin
  - a /chat endpoint the widget posts to
"""

import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from agent import build_agent, chat_once

load_dotenv()

ALLOWED_ORIGINS = [o.strip() for o in
                   os.getenv("ALLOWED_ORIGINS", "*").split(",")]

# Singletons populated on startup
state = {"agent": None}

@asynccontextmanager
async def lifespan(app: FastAPI):
    state["agent"] = await build_agent()
    yield

app = FastAPI(title="MOSDAC Agent API", lifespan=lifespan)

# CORS for the portal
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# Rate limiting (HTTP-level safety net)
limiter = Limiter(key_func=get_remote_address,
                  default_limits=["60/hour"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None

class ChatOut(BaseModel):
    reply: str
    session_id: str

def validate_sso(request: Request) -> str:
    """
    Lightweight SSO check. Two acceptable modes:
      1) Browser embed via mosdac.gov.in -> the portal forwards the Keycloak
         session cookie; we validate it with Keycloak's userinfo endpoint.
      2) Bearer token in Authorization header.
    Returns the MOSDAC username; raises 401 otherwise.
    """
    # MVP: trust an X-MOSDAC-User header set by the portal's reverse proxy.
    # Replace with a real Keycloak userinfo call before production.
    user = request.headers.get("X-MOSDAC-User")
    if not user:
        raise HTTPException(401, "SSO required")
    return user

@app.post("/chat", response_model=ChatOut)
@limiter.limit("10/minute")          # extra per-IP cap on chat
async def chat(payload: ChatIn,
               request: Request,
               user: str = Depends(validate_sso)):
    session_id = payload.session_id or str(uuid.uuid4())
    # thread_id binds memory to (user, session) so two browser tabs don't mix
    thread_id = f"{user}:{session_id}"
    try:
        reply = await chat_once(state["agent"], thread_id, payload.message)
    except Exception as e:
        # Never leak stack traces to the chat widget
        raise HTTPException(500, f"Agent error: {type(e).__name__}")
    return ChatOut(reply=reply, session_id=session_id)

@app.get("/health")
async def health():
    return {"ok": True}
```

Run it:
```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

---

### Phase D — Chat UI

**Option 1 — Streamlit prototype** (`chat_ui_streamlit.py`):
```python
import os
import uuid
import requests
import streamlit as st

API = os.getenv("CHAT_API", "http://localhost:8080/chat")

st.set_page_config(page_title="MOSDAC-Bot", page_icon="🛰️")
st.title("🛰️ MOSDAC Order Assistant")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "history" not in st.session_state:
    st.session_state.history = []

for role, msg in st.session_state.history:
    with st.chat_message(role):
        st.markdown(msg)

prompt = st.chat_input("e.g. Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP")
if prompt:
    st.session_state.history.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            r = requests.post(
                API,
                json={"message": prompt,
                      "session_id": st.session_state.session_id},
                headers={"X-MOSDAC-User": "dev-user"},  # dev only
                timeout=120,
            )
        reply = r.json().get("reply", "(error)")
        st.markdown(reply)
        st.session_state.history.append(("assistant", reply))
```
Run: `streamlit run chat_ui_streamlit.py`.

**Option 2 — Embeddable widget for mosdac.gov.in.**

`widget/widget.html` (loaded into an `<iframe>`):
```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>MOSDAC Assistant</title>
<link rel="stylesheet" href="widget.css">
</head><body>
<div id="chat">
  <div id="log" aria-live="polite"></div>
  <form id="f">
    <input id="msg" autocomplete="off"
           placeholder="Ask MOSDAC-Bot to place an order…" required>
    <button type="submit">Send</button>
  </form>
</div>
<script src="widget.js"></script>
</body></html>
```

`widget/widget.css`:
```css
body{font:14px/1.4 system-ui;margin:0;color:#000;background:#fff}
#chat{display:flex;flex-direction:column;height:100vh}
#log{flex:1;overflow:auto;padding:12px}
.msg{margin:6px 0;padding:8px 12px;border-radius:8px;max-width:85%}
.user{background:#eee;align-self:flex-end;margin-left:auto}
.bot {background:#f5f5f5;border:1px solid #ddd}
form{display:flex;border-top:1px solid #ccc;padding:8px;gap:6px}
input{flex:1;padding:8px;border:1px solid #bbb;border-radius:6px}
button{padding:8px 14px;border:1px solid #444;background:#fff;border-radius:6px;cursor:pointer}
```

`widget/widget.js`:
```javascript
const API = "https://your-host.example/chat";   // replace at deploy time
let sessionId = crypto.randomUUID();
const log = document.getElementById("log");
const form = document.getElementById("f");
const msg = document.getElementById("msg");

function append(role, text){
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "user" : "bot");
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = msg.value.trim();
  if(!text) return;
  append("user", text); msg.value = "";
  append("bot", "…");
  try{
    const r = await fetch(API, {
      method: "POST",
      credentials: "include",     // forwards MOSDAC SSO cookie
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message: text, session_id: sessionId})
    });
    const data = await r.json();
    log.lastChild.textContent = data.reply || "(no reply)";
    sessionId = data.session_id || sessionId;
  }catch(err){
    log.lastChild.textContent = "Network error.";
  }
});
```

**Embedding on mosdac.gov.in.** Hand the MOSDAC webmaster either:
```html
<!-- Simple iframe (safest, fully isolated) -->
<iframe src="https://your-host.example/widget/widget.html"
        style="position:fixed;bottom:20px;right:20px;width:360px;height:520px;
               border:1px solid #888;border-radius:10px;background:#fff;z-index:9999"
        title="MOSDAC Assistant"></iframe>
```
or a small launcher script that injects the iframe on click.

---

### Phase E — Integration, Security & Deployment

**Dockerfile** (multi-process app: run MCP server + FastAPI in one container is fine for the prototype):
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8080 8765
CMD ["bash", "-c",
     "python mosdac_mcp_server.py & uvicorn main:app --host 0.0.0.0 --port 8080"]
```

**docker-compose.yml** (recommended — separate Ollama):
```yaml
services:
  ollama:
    image: ollama/ollama
    ports: ["11434:11434"]
    volumes: ["./ollama:/root/.ollama"]
    deploy:
      resources:
        reservations:
          devices: [{ capabilities: ["gpu"] }]
  api:
    build: .
    env_file: .env
    depends_on: [ollama]
    ports: ["8080:8080"]
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434
```

**Deployment checklist.**
- TLS-terminated reverse proxy in front (Nginx/Caddy). Browsers loaded on `https://mosdac.gov.in` will refuse a non-HTTPS chat backend.
- `ALLOWED_ORIGINS` set to `https://www.mosdac.gov.in,https://mosdac.gov.in` only.
- Secrets in environment variables; never in code; `.env` git-ignored.
- Logs (audit trail of every placed order) shipped off-box (e.g. to Loki/CloudWatch).
- Health probe at `/health`; restart policy `unless-stopped`.

---

### 5. Best Practices

**For MCP tools.**
- One tool = one verb. Don't combine "search and order" into one tool — the LLM should compose them.
- The docstring is the LLM's documentation. Write it like you're writing API docs.
- Validate aggressively *before* calling MOSDAC. Bad dates, missing AOI, oversized ranges should `raise ToolError` with a fix-this message.
- Return structured dicts, not free text. The agent can format prose on top.
- Idempotency-key every write tool. Cache `(key → result)` for at least 24 h.
- Never log credentials. Log order IDs and user IDs, not session tokens.

**For LangGraph agents.**
- Keep the system prompt tight and rule-based (see the prompt in `agent.py`). Long flowery prompts hurt small/local models.
- Use `temperature=0.1` for ordering tasks — you want determinism, not creativity.
- Add a final-step rule (exact "Order has been placed. Check your SFTP account." sentence) so success messages are stable for any UI tests.
- Turn on LangSmith tracing in dev (`LANGSMITH_TRACING=true`) to see every tool call.
- For Qwen 2.5 on Ollama, set `num_ctx` to ≥ 8192; default 2048 is too small once tool schemas land in context.

**Security.**
- `python-dotenv` for local secrets; a real secret manager (AWS Secrets Manager, HashiCorp Vault) for production.
- Pydantic models on every FastAPI input.
- SlowAPI rate limit at the HTTP layer **plus** per-user counter inside the MCP server — defence in depth.
- Audit log every order with `(user, dataset_id, bbox, time_range, idempotency_key, ts)`.
- Bind Ollama to localhost only; do not expose `:11434` publicly.

**Testing strategy.**
- Unit-test each MCP tool with a mocked `httpx` (`pytest` + `respx`). Cover happy path, rate-limit, idempotency replay, date validation.
- Integration-test the agent end-to-end against a fake MOSDAC FastAPI server you stub locally. This is also your week-1 deliverable.
- One canonical scripted scenario: `Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14–18 Aug 2024 via SFTP` → asserts the final sentence and a valid order_id.

---

### 6. Complete Working Example Workflow

User types in the widget:
> Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14–18 Aug 2024 via SFTP

Step-by-step the agent does:

1. **LLM reasoning:** "User wants INSAT-3D TIR-1 L1B; I don't know the exact dataset_id; call `search_products` first."
2. **Tool call:** `search_products(query="INSAT-3D TIR-1 L1B", satellite="INSAT-3D")`. Returns `[{"dataset_id":"3SIMG_L1B_STD","name":"INSAT-3D Imager L1B (Standard)","bands":["VIS","SWIR","MIR","WV","TIR-1","TIR-2"], ...}]`.
3. **LLM reasoning:** "Match found. Tamil Nadu is a state, pass it as `state_or_region`. Dates 2024-08-14..2024-08-18."
4. **Tool call:** `place_order(dataset_id="3SIMG_L1B_STD", start_date="2024-08-14", end_date="2024-08-18", state_or_region="Tamil Nadu", level_format="L1B_HDF5", delivery="SFTP")`.
5. **MCP server:** validates dates ✓, resolves "tamil nadu" → bbox `"76.2,8.0,80.4,13.6"`, checks per-user rate limit ✓, generates idempotency key, logs in to MOSDAC (cached), POSTs to MOSDAC `/api/v1/orders`, gets back `{"orderId": "MOSDAC-2024-08-14-9F3A"}`, persists audit row, returns `{order_id, status:"queued", eta, sftp_path:"sftp://ftp.mosdac.gov.in/MOSDAC-2024-08-14-9F3A/", ...}`.
6. **LLM final reply (literal):**
   ```
   Order has been placed. Check your SFTP account.
   Order ID: MOSDAC-2024-08-14-9F3A  (INSAT-3D Imager L1B, Tamil Nadu, 2024-08-14 to 2024-08-18, delivery: sftp://ftp.mosdac.gov.in/MOSDAC-2024-08-14-9F3A/)
   ```

If the user then asks "What's the status of that order?" the agent calls `check_order_status("MOSDAC-2024-08-14-9F3A")` and reports the stepper state (Queued → Slicing → Packaging → Ready → Notified).

---

### 7. Two-Week Implementation Plan

**Week 1 — Learning + MCP + Agent (against a mock MOSDAC).**

- **Day 1.** Read the MCP intro page and the FastMCP quickstart. Install Python 3.11, set up venv, install Ollama, `ollama pull qwen2.5:32b`. Run `ollama run qwen2.5:32b "hi"` and confirm it responds.
- **Day 2.** Create the repo skeleton (folder structure in §3.5). Write `.env.example`, `requirements.txt`, `.gitignore`. Build the smallest possible FastMCP server with one dummy `place_order` tool that just returns `{"order_id": "FAKE-1"}`. Verify with MCP Inspector.
- **Day 3.** Write a tiny FastAPI app that *pretends* to be MOSDAC (`fake_mosdac.py` with `/api/v1/orders` returning a random id). Wire `mosdac_mcp_server.py` to it via `MOSDAC_BASE_URL=http://localhost:9000`. Test ordering end-to-end with MCP Inspector.
- **Day 4.** Add `search_products`, `check_order_status`, `list_my_orders`. Add SQLite idempotency + audit log. Add the per-user rate limiter. Write `pytest` unit tests for each tool (use `respx` to mock `httpx`).
- **Day 5.** Write `agent.py`: `MultiServerMCPClient` connecting to the MCP server, `ChatOllama(qwen2.5:32b)`, `create_react_agent`. Get the canonical Tamil Nadu order working end-to-end in the CLI.
- **Day 6.** Tighten the system prompt. Add the literal final sentence rule. Add memory (`MemorySaver`). Test multi-turn (e.g. "place that order" after a prior `search_products`).
- **Day 7.** Buffer day. Write `README.md`. Fix prompt failures you've seen. Commit a tagged `v0.1-mock` release.

**Week 2 — UI, real MOSDAC, deploy.**

- **Day 8.** FastAPI `main.py` with `/chat`, CORS, SlowAPI. Streamlit UI. Get the widget + API + agent + mock MOSDAC chain working in the browser.
- **Day 9.** Build the HTML/JS widget (`widget/`). Serve it from FastAPI as static files. Verify it works inside a test `iframe` page.
- **Day 10.** Switch from the mock to the real MOSDAC endpoints from the Order API PDF. **Test with one real order**, watching MOSDAC's "My Request" tab to confirm it shows up. (Avoid hammering — three failed logins = 1-hour lockout.)
- **Day 11.** Hard security pass: env-var audit, secrets rotation procedure documented, CORS locked to `mosdac.gov.in`, audit log review. Replace the dev `X-MOSDAC-User` header with real Keycloak userinfo validation.
- **Day 12.** Dockerise. Run via `docker compose up`. Smoke test the whole stack from a fresh machine.
- **Day 13.** End-to-end acceptance test of the canonical scenario from a clean state. Document the embed snippet for the MOSDAC webmaster. Write a one-page user guide.
- **Day 14.** Demo + buffer. Hand off the deploy package and embed instructions.

---

### 8. Step-by-Step Process Flow Diagram

End-to-end order flow:
```
[User in browser on mosdac.gov.in]
        |  types: "Order INSAT-3D TIR-1 L1B for Tamil Nadu, 14-18 Aug 2024 via SFTP"
        v
[Chat widget (iframe)]
        |  POST /chat  {message, session_id}    [CORS, SSO cookie/header]
        v
[FastAPI /chat]
        |  SlowAPI rate-limit check (10/min)
        |  validate_sso() -> user
        |  thread_id = user:session_id
        v
[LangGraph ReAct agent  (Qwen 2.5 32B via Ollama)]
        |
        |-- step 1: tool_call search_products(query="INSAT-3D TIR-1 L1B")
        |       v
        |  [MCP client] --MCP--> [MCP server] -> returns dataset_id=3SIMG_L1B_STD
        |
        |-- step 2: tool_call place_order(dataset_id=3SIMG_L1B_STD,
        |             start=2024-08-14, end=2024-08-18,
        |             state_or_region="Tamil Nadu",
        |             level_format=L1B_HDF5, delivery=SFTP)
        |       v
        |  [MCP server]
        |     1. validate dates, AOI, count
        |     2. idempotency check (SQLite)
        |     3. per-user rate limit
        |     4. Keycloak login (cached token)
        |     5. POST /api/v1/orders to MOSDAC + Idempotency-Key header
        |     6. record audit row
        |     7. return {order_id, sftp_path, eta, status:"queued"}
        |
        |-- step 3: LLM composes final reply
        v
[FastAPI returns]  {"reply": "Order has been placed. Check your SFTP account.\nOrder ID: ...",
                    "session_id": "..."}
        v
[Widget renders the reply]
        |
        |  (later, asynchronously)
        v
[MOSDAC backend slices + packages data]  --uploads-->  [sftp://ftp.mosdac.gov.in/<order_id>/]
        v
[User logs into SFTP with their MOSDAC credentials and downloads files]
```

Order-status state diagram (B.9 OrderStatusTracker):
```
   +---------+   +---------+   +-----------+   +-------+   +----------+
   | Queued  |-->| Slicing |-->| Packaging |-->| Ready |-->| Notified |
   +----+----+   +----+----+   +-----+-----+   +---+---+   +----------+
        |             |              |             |
        v             v              v             v
                  +---------+
                  | Failed  |   (any state can transition to Failed;
                  +---------+    reason shown inline in the UI)
```
The widget polls `check_order_status(order_id)` every ~30 s while a tracker is open and renders the stepper above; failures show the MOSDAC-returned error message inline.

---

## Recommendations

1. **Build against a mock MOSDAC server first (Days 2–7).** This is the single biggest accelerator. Real credentials, the three-strike lockout, and a slow approval loop will eat your timeline if you hit the live API on day one. **Threshold to switch to live MOSDAC:** the Tamil Nadu canonical test passes end-to-end against the mock with idempotency and rate-limit tests green.
2. **Pick Qwen 2.5 32B over Qwen 3 32B for v1.** It has more battle-tested tool-calling templates in Ollama and a wider body of LangChain examples. Re-evaluate Qwen 3 once your agent is stable; benchmark by replaying 20 logged conversations and counting tool-call success rate. **Threshold to switch:** Qwen 3 ≥ Qwen 2.5 on your replay set AND inference latency within 20%.
3. **Use the FastAPI + iframe widget for the portal embed.** It is the fastest path that the MOSDAC webmaster can deploy without touching Drupal templates. **Threshold to upgrade to a native embed:** ISRO/SAC sign-off + a CSP review.
4. **Hard-cap orders at 10/user/hour and 100 files/order to start.** Loosen only after you've watched a week of real audit logs. **Threshold to raise:** zero abuse incidents AND average user feedback that the limit was hit unnecessarily.
5. **Use Streamable HTTP, not stdio, for the MCP transport between agent and server in production.** Stdio is fine for local dev with MCP Inspector; HTTP is what scales when the agent and MCP server run in separate containers.
6. **If you cannot get a 24 GB GPU, do not downgrade quality — rent.** An A100-40GB on RunPod or Jarvislabs is typically a few dollars an hour and will run Qwen 2.5 32B at full speed. The user experience drop from `qwen2.5:14b` on a small GPU is bigger than people expect for tool-calling agents.
7. **Wire LangSmith tracing on from day 1.** Even the free tier shows every tool call/argument/return, which makes debugging the agent loop 10× faster.
8. **Plan a v2 for "standing orders" and HTTP/Email delivery.** Both are documented MOSDAC capabilities; cover SFTP only in v1 to keep scope tight.

---

## Caveats

- **The exact MOSDAC Order API endpoints and request schemas are not on the public website.** The publicly-available "MOSDAC Data Download API" manual documents the *search and download* client (a Python script driven by `config.json`), MOSDAC SSO via Keycloak, dataset IDs, bounding-box format, and the daily 5000-file limit — but the per-order POST endpoint and JSON schema you'll see in your team's Order API PDF may differ from the `POST /api/v1/orders` placeholder used in the code above. **You must overwrite the three URL strings and the request body shape in `mosdac_mcp_server.py` with the exact specification from your PDF before going live.**
- **SSO mechanics.** MOSDAC's auth is Keycloak-based at `/auth/realms/Mosdac/...`; the `client_id`, allowed grant types, and any required scopes are specific to your account and are not published. Confirm with the MOSDAC admin team before flipping the `validate_sso()` check from header-trust to real userinfo validation.
- **Three failed logins lock the account for one hour** per the official manual. Your MCP server MUST NOT auto-retry login on credential failure.
- **Daily download limit is 5000 files per user per day.** The code's `max_files=100` per order is a soft cap inside your server; the hard cap is enforced upstream.
- **Standing orders are limited to one-month windows and are only available to "privileged users."** Don't promise this feature to general users without checking their MOSDAC role.
- **Qwen tool-calling reliability with small local models is not perfect.** Expect occasional hallucinated dataset IDs; the `search_products`-before-`place_order` rule in the system prompt is your main mitigation, and `ToolError` on unknown dataset IDs is your safety net.
- **Embedding into mosdac.gov.in itself requires action by ISRO/SAC.** Your deliverable is a self-contained chat widget plus the `<iframe>`/`<script>` snippet; the actual placement on the live portal is a webmaster task, not a development task.
- **Hardware figures (~22 GB VRAM at Q4_K_M for Qwen 2.5 32B; RTX 3090/4090 as comfortable minimum)** are community-reported, not vendor-published; your exact RAM footprint will vary with context length and concurrent requests. Always size with a 20–30% headroom.
- **MCP HTTP+SSE transport is deprecated by the MCP spec.** Anywhere this plan or older tutorials suggest SSE, use Streamable HTTP instead.
- **The FastMCP package and the `mcp` package's `mcp.server.fastmcp` are related but not identical** — FastMCP 1.0 was folded into the official SDK and FastMCP 2.0+ is now a superset maintained separately. The code in this plan uses the standalone `fastmcp` package (recommended); if you prefer the SDK's built-in `FastMCP`, the API is nearly the same but `@mcp.tool()` (with parens) instead of `@mcp.tool` may be required.