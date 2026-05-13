# MOSDAC Graph RAG Chatbot — Complete Integration Guide

> **Scope:** Integrate the Graph RAG chatbot with the MOSDAC web portal
> (`mosdac.gov.in`) as a floating side-panel widget, powered by a **local
> Qwen multimodal LLM on Docker**, with a screenshot-to-chat feature.
> Everything runs **100% offline** — no internet required at runtime.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Backend Changes — Swap to Local Qwen LLM](#2-backend-changes--swap-to-local-qwen-llm)
3. [FastAPI Gateway (New File)](#3-fastapi-gateway-new-file)
4. [Docker Compose — Full Stack](#4-docker-compose--full-stack)
5. [Frontend Widget — JavaScript Injection](#5-frontend-widget--javascript-injection)
6. [Screenshot Pipeline](#6-screenshot-pipeline)
7. [System Prompt Configuration](#7-system-prompt-configuration)
8. [.env Changes for Offline Mode](#8-env-changes-for-offline-mode)
9. [NGINX Reverse Proxy (Recommended)](#9-nginx-reverse-proxy-recommended)
10. [Deployment Checklist](#10-deployment-checklist)
11. [File Structure Summary](#11-file-structure-summary)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  MOSDAC Portal  (mosdac.gov.in — existing server, no src changes)│
│                                                                    │
│  ┌──────────────────────────────────┐  ┌────────────────────────┐│
│  │  Existing Portal Pages           │  │  Chatbot Side Panel    ││
│  │  (Satellite Images, SCORPIO,     │  │  (injected via 1 JS    ││
│  │   Eddy Tracker, Catalog…)        │  │   snippet + CSS)       ││
│  └──────────────────────────────────┘  └────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
                              │  REST  (same-server or LAN)
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│  FastAPI Gateway  :8000  (new — chat_api/main.py)                │
│  • /chat      POST  — text query → hybrid RAG → LLM response     │
│  • /chat/image POST — text + base64 screenshot → LLM response    │
│  • /health    GET                                                  │
└───────────┬──────────────────────┬───────────────────────────────┘
            │                      │
   ┌────────▼──────┐      ┌────────▼──────────────────────────────┐
   │  ChromaDB     │      │  Neo4j  :7687                         │
   │  (local dir)  │      │  (knowledge graph, Docker)            │
   └───────────────┘      └───────────────────────────────────────┘
            │
   ┌────────▼────────────────────────────────────────────────────┐
   │  Qwen2.5-VL (vision-language) via Ollama  :11434            │
   │  — OR — vLLM  :8080  (higher throughput)                    │
   │  Runs fully on local GPU/CPU inside Docker                  │
   └─────────────────────────────────────────────────────────────┘
```

**Integration philosophy:** Because the MOSDAC portal source is confidential,
**zero changes are made to the portal's server-side code.** The chatbot widget
is injected through **one of three methods** (choose based on your access):

| Method | What you touch | Best when |
|--------|---------------|-----------|
| A. `<script>` tag in portal's base template | Add 2 lines to a shared HTML footer/header | You can add to a base template |
| B. NGINX `sub_filter` injection | NGINX config only | You control the reverse proxy |
| C. Internal browser extension | Browser only | Zero server changes needed |

All three methods load the same `mosdac-chat-widget.js` file.

---

## 2. Backend Changes — Swap to Local Qwen LLM

### 2a. Create `graph_rag/llm/qwen_client.py`

Create this file alongside the existing `longcat_client.py`:

```python
# graph_rag/llm/qwen_client.py
"""
LangChain client pointing at a local Qwen model served by Ollama or vLLM.
Qwen2.5-VL supports vision — screenshots are passed as base64 image blocks.

Set in .env:
    QWEN_API_BASE=http://localhost:11434/v1      # Ollama
    QWEN_MODEL=qwen2.5vl:7b                     # or qwen2.5:14b for text-only
    QWEN_API_KEY=ollama                          # Ollama ignores this but needs it
"""
from __future__ import annotations

from functools import lru_cache
from langchain_openai import ChatOpenAI
from graph_rag.config import settings


@lru_cache(maxsize=1)
def get_llm(temperature: float = 0.2, max_tokens: int = 2048) -> ChatOpenAI:
    """Returns a ChatOpenAI instance pointed at the local Qwen endpoint."""
    return ChatOpenAI(
        model=settings.qwen_model,
        api_key=settings.qwen_api_key,          # "ollama" for Ollama, any string for vLLM
        base_url=settings.qwen_api_base,
        temperature=temperature,
        max_tokens=max_tokens,
        streaming=False,
    )
```

### 2b. Update `graph_rag/config.py`

Add the following fields to the `Settings` class (alongside the existing ones):

```python
# Local Qwen LLM (Ollama or vLLM — OpenAI-compatible endpoint)
qwen_api_base: str = "http://localhost:11434/v1"
qwen_model: str = "qwen2.5vl:7b"
qwen_api_key: str = "ollama"                    # Ollama ignores the key value

# System prompt file path (change this to reconfigure LLM behaviour)
system_prompt_path: str = "./prompts/system_prompt.txt"
```

### 2c. Update `graph_rag/llm/__init__.py`

```python
# graph_rag/llm/__init__.py
from graph_rag.llm.qwen_client import get_llm
__all__ = ["get_llm"]
```

### 2d. Update `graph_rag/chain/graph_rag_chain.py`

Replace the hardcoded `SYSTEM_PROMPT` string with a **file-loaded prompt** so
you can update behaviour without touching code:

```python
# graph_rag/chain/graph_rag_chain.py  — MODIFIED sections only

from pathlib import Path
from graph_rag.config import settings

def _load_system_prompt() -> str:
    """Load system prompt from file. Falls back to default if file not found."""
    path = Path(settings.system_prompt_path)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    # Fallback — identical to original default
    return _DEFAULT_SYSTEM_PROMPT

_DEFAULT_SYSTEM_PROMPT = """You are an expert assistant with access to a knowledge
graph and document database about the MOSDAC portal and ISRO satellite data.

Use the provided context to answer the user's question accurately and concisely.

KNOWLEDGE GRAPH (entity relationships extracted from source documents):
{graph_context}

DOCUMENT PASSAGES (semantically relevant text excerpts):
{vector_context}

Rules:
- Only use facts grounded in the context above.
- Cite the [Source: ...] when stating specific facts from passages.
- If the answer is not in the context, say "I don't have enough information to answer that."
- Prefer relationship-based reasoning when graph paths are present.
"""

def build_graph_rag_chain(retriever=None, llm=None):
    retriever = retriever or HybridRetriever()
    llm = llm or get_llm()

    # Load prompt dynamically so changes to the file take effect on next request
    system_text = _load_system_prompt()

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_text),
        ("human", HUMAN_TEMPLATE),
    ])
    # ... rest of function unchanged
```

---

## 3. FastAPI Gateway (New File)

Create `chat_api/main.py`. This is the HTTP bridge between the JS widget and
your Python RAG backend.

```
chat_api/
├── main.py          ← FastAPI app (create this)
├── models.py        ← Pydantic request/response models (create this)
└── __init__.py
```

### `chat_api/models.py`

```python
# chat_api/models.py
from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    session_id: str                     # browser tab UUID, used for history
    message: str
    screenshot_base64: Optional[str] = None   # base64-encoded PNG/JPEG
    screenshot_mime: Optional[str] = "image/png"

class ChatResponse(BaseModel):
    answer: str
    session_id: str
```

### `chat_api/main.py`

```python
# chat_api/main.py
"""
FastAPI gateway.

Run:
    uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload

Endpoints:
    POST /chat          — text-only or text+screenshot
    GET  /health        — liveness probe
    DELETE /chat/{sid}  — clear session history
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage

from chat_api.models import ChatRequest, ChatResponse
from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
from graph_rag.retrieval.hybrid_retriever import HybridRetriever
from graph_rag.llm.qwen_client import get_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("chat_api")

app = FastAPI(title="MOSDAC Graph RAG Chatbot API", version="1.0.0")

# ── CORS ─────────────────────────────────────────────────────────────────────
# Allow the MOSDAC portal origin. Add your portal's exact origin below.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://www.mosdac.gov.in",
        "http://localhost",           # for local testing
        "http://127.0.0.1",
    ],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ── Shared singletons ─────────────────────────────────────────────────────────
_retriever = HybridRetriever()
_chain = build_graph_rag_chain(retriever=_retriever)
_llm = get_llm()

# Simple in-memory session history: {session_id: [{"role": ..., "content": ...}]}
_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
MAX_HISTORY = 10   # turns kept per session


def _trim_history(session_id: str) -> None:
    if len(_sessions[session_id]) > MAX_HISTORY * 2:
        _sessions[session_id] = _sessions[session_id][-MAX_HISTORY * 2:]


def _build_history_prefix(session_id: str) -> str:
    turns = _sessions[session_id]
    if not turns:
        return ""
    lines = []
    for t in turns:
        role = "User" if t["role"] == "user" else "Assistant"
        content = t["content"] if isinstance(t["content"], str) else "[image]"
        lines.append(f"{role}: {content}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\nNew question: "


def _answer_with_image(message: str, screenshot_b64: str, mime: str, session_id: str) -> str:
    """
    Call Qwen VL directly with text + image.
    The RAG context is prepended to the user message so the LLM sees both
    the retrieved knowledge and the screenshot.
    """
    # 1. Retrieve RAG context using the text query
    ctx = _retriever.retrieve(message)
    rag_preamble = (
        f"KNOWLEDGE GRAPH:\n{ctx['graph_context']}\n\n"
        f"DOCUMENT PASSAGES:\n{ctx['vector_context']}\n\n"
        f"User question about the attached screenshot: {message}"
    )

    # 2. Build multimodal message for Qwen VL
    history_prefix = _build_history_prefix(session_id)
    content = []
    if history_prefix:
        content.append({"type": "text", "text": history_prefix})
    content.append({"type": "text", "text": rag_preamble})
    content.append({
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime};base64,{screenshot_b64}"
        }
    })

    # 3. Call LLM directly (bypass LangChain chain for multimodal)
    response = _llm.invoke([HumanMessage(content=content)])
    return response.content if hasattr(response, "content") else str(response)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        _trim_history(req.session_id)

        if req.screenshot_base64:
            # Multimodal path: screenshot attached
            answer = _answer_with_image(
                message=req.message,
                screenshot_b64=req.screenshot_base64,
                mime=req.screenshot_mime or "image/png",
                session_id=req.session_id,
            )
        else:
            # Text-only path: use the full RAG chain
            history_prefix = _build_history_prefix(req.session_id)
            answer = _chain.invoke({
                "question": req.message,
                "history": history_prefix,
            })

        # Save to history
        _sessions[req.session_id].append({"role": "user", "content": req.message})
        _sessions[req.session_id].append({"role": "assistant", "content": answer})

        return ChatResponse(answer=answer, session_id=req.session_id)

    except Exception as exc:
        logger.exception("Chat error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/chat/{session_id}")
def clear_session(session_id: str):
    _sessions.pop(session_id, None)
    return {"cleared": session_id}
```

---

## 4. Docker Compose — Full Stack

Create `docker-compose.yml` in the project root:

```yaml
# docker-compose.yml
version: "3.9"

services:

  # ── 1. Qwen LLM via Ollama ──────────────────────────────────────────────────
  ollama:
    image: ollama/ollama:latest          # Pull once, then offline
    container_name: mosdac_ollama
    restart: unless-stopped
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama        # Model weights persist here
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]        # Remove if CPU-only server

  # ── 2. Neo4j Knowledge Graph ────────────────────────────────────────────────
  neo4j:
    image: neo4j:2025.04.0-community
    container_name: mosdac_neo4j
    restart: unless-stopped
    ports:
      - "7474:7474"                      # Browser UI
      - "7687:7687"                      # Bolt
    environment:
      NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
      NEO4J_server_memory_heap_max__size: "2G"
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs

  # ── 3. FastAPI Chat Gateway ─────────────────────────────────────────────────
  chat_api:
    build:
      context: .
      dockerfile: Dockerfile.api
    container_name: mosdac_chat_api
    restart: unless-stopped
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      - ollama
      - neo4j
    volumes:
      - ./chroma_db:/app/chroma_db       # ChromaDB persistence
      - ./prompts:/app/prompts           # System prompts — edit without rebuild
      - ${DOWNLOADS_DIR}:/app/downloads:ro
      - ${ATLASES_DIR}:/app/atlases:ro

volumes:
  ollama_data:
  neo4j_data:
  neo4j_logs:
```

### `Dockerfile.api`

```dockerfile
# Dockerfile.api
FROM python:3.11-slim

WORKDIR /app

# System deps for spaCy, pytesseract, lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirement.txt .
RUN pip install --no-cache-dir -r requirement.txt \
    fastapi "uvicorn[standard]" \
 && python -m spacy download en_core_web_sm

COPY . .

CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 5. Frontend Widget — JavaScript Injection

### 5a. Create `static/mosdac-chat-widget.js`

This single file creates the entire floating chat panel shown in your UI mockup.
Host it at `/static/mosdac-chat-widget.js` on the same server as the portal,
or from any LAN-accessible path.

```javascript
// static/mosdac-chat-widget.js
// MOSDAC Graph RAG Chatbot Widget
// Injects a Copilot-style side panel into any MOSDAC portal page.
// No framework required — vanilla JS + inline CSS.

(function () {
  'use strict';

  // ── Configuration ────────────────────────────────────────────────────────
  const API_BASE   = '/chatapi';        // proxied via NGINX (see §9)
  const BOT_TITLE  = 'MOSDAC Assistant';
  const BOT_LOGO   = '/favicon.ico';

  // Generate a stable session ID per browser tab
  const SESSION_ID = sessionStorage.getItem('mosdac_chat_sid') || (() => {
    const id = 'sess_' + Math.random().toString(36).slice(2);
    sessionStorage.setItem('mosdac_chat_sid', id);
    return id;
  })();

  // ── Inject CSS ────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #mosdac-chat-toggle {
      position: fixed; bottom: 28px; right: 28px; z-index: 9998;
      width: 54px; height: 54px; border-radius: 50%;
      background: #1565c0; border: none; cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,0.35);
      display: flex; align-items: center; justify-content: center;
      transition: background 0.2s;
    }
    #mosdac-chat-toggle:hover { background: #0d47a1; }
    #mosdac-chat-toggle svg { width: 26px; height: 26px; fill: #fff; }

    #mosdac-chat-panel {
      position: fixed; top: 0; right: -420px; width: 420px; height: 100vh;
      background: #1a1a2e; color: #e0e0e0;
      box-shadow: -4px 0 20px rgba(0,0,0,0.5);
      display: flex; flex-direction: column; z-index: 9999;
      transition: right 0.3s ease; font-family: 'Segoe UI', sans-serif;
      border-left: 2px solid #1565c0;
    }
    #mosdac-chat-panel.open { right: 0; }

    #mosdac-chat-header {
      background: #0d1b4b; padding: 14px 16px;
      display: flex; align-items: center; gap: 10px;
      border-bottom: 1px solid #1565c0;
    }
    #mosdac-chat-header img { width: 28px; height: 28px; border-radius: 4px; }
    #mosdac-chat-header span { font-weight: 600; font-size: 15px; flex: 1; }
    #mosdac-chat-close {
      background: none; border: none; color: #90caf9; font-size: 20px;
      cursor: pointer; padding: 0 4px; line-height: 1;
    }
    #mosdac-chat-close:hover { color: #fff; }

    #mosdac-chat-messages {
      flex: 1; overflow-y: auto; padding: 14px;
      display: flex; flex-direction: column; gap: 12px;
    }
    .mc-msg {
      max-width: 88%; padding: 10px 14px; border-radius: 12px;
      font-size: 13.5px; line-height: 1.55; word-break: break-word;
    }
    .mc-msg.user {
      align-self: flex-end; background: #1565c0; color: #fff;
      border-bottom-right-radius: 3px;
    }
    .mc-msg.bot {
      align-self: flex-start; background: #263054; color: #e0e0e0;
      border-bottom-left-radius: 3px; white-space: pre-wrap;
    }
    .mc-msg.error { background: #4a1010; color: #ff8a80; }
    .mc-msg img.thumb {
      max-width: 100%; border-radius: 6px; margin-top: 6px;
      display: block; border: 1px solid #1565c0;
    }
    .mc-typing { color: #90caf9; font-style: italic; font-size: 12px; }

    #mosdac-chat-attach-preview {
      margin: 0 14px; padding: 8px 10px;
      background: #263054; border-radius: 8px; font-size: 12px;
      display: none; align-items: center; gap: 8px;
    }
    #mosdac-chat-attach-preview img { height: 44px; border-radius: 4px; }
    #mc-remove-attach { background: none; border: none; color: #f44336;
      font-size: 16px; cursor: pointer; margin-left: auto; }

    #mosdac-chat-input-row {
      padding: 10px 12px; border-top: 1px solid #263054;
      display: flex; gap: 8px; align-items: flex-end;
    }
    #mosdac-chat-input {
      flex: 1; background: #263054; border: 1px solid #1565c0;
      color: #e0e0e0; border-radius: 8px; padding: 9px 12px;
      font-size: 13.5px; resize: none; outline: none;
      min-height: 38px; max-height: 120px; overflow-y: auto;
    }
    #mosdac-chat-input::placeholder { color: #7986cb; }
    .mc-icon-btn {
      background: #1565c0; border: none; border-radius: 8px;
      width: 38px; height: 38px; cursor: pointer; flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      color: #fff; transition: background 0.2s;
    }
    .mc-icon-btn:hover { background: #0d47a1; }
    .mc-icon-btn svg { width: 18px; height: 18px; fill: currentColor; }
    .mc-icon-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  `;
  document.head.appendChild(style);

  // ── Build HTML ─────────────────────────────────────────────────────────────
  document.body.insertAdjacentHTML('beforeend', `
    <button id="mosdac-chat-toggle" title="Open MOSDAC Assistant">
      <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
    </button>

    <div id="mosdac-chat-panel">
      <div id="mosdac-chat-header">
        <img src="${BOT_LOGO}" alt="MOSDAC" onerror="this.style.display='none'">
        <span>${BOT_TITLE}</span>
        <button id="mosdac-chat-close" title="Close">&#x2715;</button>
      </div>

      <div id="mosdac-chat-messages">
        <div class="mc-msg bot">Hello! I am the MOSDAC Assistant. Ask me anything about satellite data, products, cyclones, ocean state, or click the camera icon to attach a screenshot of what you see.</div>
      </div>

      <div id="mosdac-chat-attach-preview">
        <img id="mc-attach-img" src="" alt="screenshot">
        <span id="mc-attach-label">Screenshot attached</span>
        <button id="mc-remove-attach" title="Remove">&#x2715;</button>
      </div>

      <div id="mosdac-chat-input-row">
        <button class="mc-icon-btn" id="mc-btn-screenshot" title="Take screenshot of current page">
          <svg viewBox="0 0 24 24"><path d="M9 3L7.17 5H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2h-3.17L15 3H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"/><circle cx="12" cy="13" r="2.5" fill="currentColor" opacity=".6"/></svg>
        </button>
        <textarea id="mosdac-chat-input" placeholder="Ask about MOSDAC data…" rows="1"></textarea>
        <button class="mc-icon-btn" id="mc-btn-send" title="Send">
          <svg viewBox="0 0 24 24"><path d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
        </button>
      </div>
    </div>
  `);

  // ── State ──────────────────────────────────────────────────────────────────
  let attachedScreenshot = null;   // { base64: string, mime: string }
  let isWaiting = false;

  // ── DOM refs ───────────────────────────────────────────────────────────────
  const panel      = document.getElementById('mosdac-chat-panel');
  const toggleBtn  = document.getElementById('mosdac-chat-toggle');
  const closeBtn   = document.getElementById('mosdac-chat-close');
  const messages   = document.getElementById('mosdac-chat-messages');
  const input      = document.getElementById('mosdac-chat-input');
  const sendBtn    = document.getElementById('mc-btn-send');
  const ssBtn      = document.getElementById('mc-btn-screenshot');
  const preview    = document.getElementById('mosdac-chat-attach-preview');
  const previewImg = document.getElementById('mc-attach-img');
  const removeBtn  = document.getElementById('mc-remove-attach');

  // ── Open / close ───────────────────────────────────────────────────────────
  toggleBtn.addEventListener('click', () => panel.classList.add('open'));
  closeBtn.addEventListener('click',  () => panel.classList.remove('open'));

  // ── Auto-resize textarea ───────────────────────────────────────────────────
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });

  // ── Send on Enter (Shift+Enter = newline) ──────────────────────────────────
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  sendBtn.addEventListener('click', sendMessage);

  // ── Screenshot ─────────────────────────────────────────────────────────────
  ssBtn.addEventListener('click', takeScreenshot);
  removeBtn.addEventListener('click', clearAttachment);

  // ── Core functions ─────────────────────────────────────────────────────────

  function appendMessage(role, text, imgDataUrl) {
    const div = document.createElement('div');
    div.className = 'mc-msg ' + role;
    div.textContent = text;
    if (imgDataUrl) {
      const img = document.createElement('img');
      img.className = 'thumb';
      img.src = imgDataUrl;
      div.appendChild(img);
    }
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function setWaiting(state) {
    isWaiting = state;
    sendBtn.disabled = state;
    ssBtn.disabled   = state;
    input.disabled   = state;
  }

  function clearAttachment() {
    attachedScreenshot = null;
    preview.style.display = 'none';
    previewImg.src = '';
  }

  async function sendMessage() {
    if (isWaiting) return;
    const text = input.value.trim();
    if (!text && !attachedScreenshot) return;

    const userText    = text || '(screenshot attached — please analyse)';
    const displayUrl  = attachedScreenshot
      ? 'data:' + attachedScreenshot.mime + ';base64,' + attachedScreenshot.base64
      : null;

    appendMessage('user', userText, displayUrl);
    input.value = '';
    input.style.height = 'auto';

    const typing = appendMessage('bot', 'Thinking…', null);
    typing.classList.add('mc-typing');
    setWaiting(true);

    try {
      const body = { session_id: SESSION_ID, message: userText };
      if (attachedScreenshot) {
        body.screenshot_base64 = attachedScreenshot.base64;
        body.screenshot_mime   = attachedScreenshot.mime;
      }

      const res = await fetch(API_BASE + '/chat', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });

      if (!res.ok) throw new Error('Server ' + res.status + ': ' + await res.text());

      const data = await res.json();
      typing.remove();
      appendMessage('bot', data.answer);

    } catch (err) {
      typing.remove();
      appendMessage('error', 'Error: ' + err.message);
    } finally {
      setWaiting(false);
      clearAttachment();
    }
  }

  async function takeScreenshot() {
    if (isWaiting) return;

    // Method 1: html2canvas (served locally — see §5b)
    if (window.html2canvas) {
      try {
        panel.style.display = 'none';
        const canvas = await html2canvas(document.body, {
          useCORS: true, allowTaint: true, scale: 1, logging: false,
        });
        panel.style.display = 'flex';
        storeScreenshot(canvas.toDataURL('image/png'));
        return;
      } catch (e) {
        panel.style.display = 'flex';
        console.warn('html2canvas failed, trying Screen Capture API', e);
      }
    }

    // Method 2: Screen Capture API (requires user gesture, works on LAN HTTPS)
    if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
      try {
        const stream  = await navigator.mediaDevices.getDisplayMedia({ video: true });
        const track   = stream.getVideoTracks()[0];
        const capture = new ImageCapture(track);
        const bitmap  = await capture.grabFrame();
        track.stop();
        const canvas  = document.createElement('canvas');
        canvas.width  = bitmap.width;
        canvas.height = bitmap.height;
        canvas.getContext('2d').drawImage(bitmap, 0, 0);
        storeScreenshot(canvas.toDataURL('image/png'));
      } catch (e) {
        alert('Screenshot permission denied. You can also paste a screenshot with Ctrl+V.');
      }
      return;
    }

    alert('Screenshot not available in this browser. Paste an image with Ctrl+V instead.');
  }

  function storeScreenshot(dataUrl) {
    const [header, base64] = dataUrl.split(',');
    const mime = header.replace('data:', '').replace(';base64', '');
    attachedScreenshot = { base64, mime };
    previewImg.src     = dataUrl;
    preview.style.display = 'flex';
  }

  // ── Clipboard paste support (Ctrl+V image into input) ─────────────────────
  input.addEventListener('paste', e => {
    for (const item of e.clipboardData.items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const blob   = item.getAsFile();
        const reader = new FileReader();
        reader.onload = ev => storeScreenshot(ev.target.result);
        reader.readAsDataURL(blob);
        break;
      }
    }
  });

})();
```

### 5b. Serve html2canvas offline

Download once (while internet is available) and serve from your static folder:

```bash
# On a machine with internet access (one-time setup):
curl -o static/html2canvas.min.js \
  https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js
```

### 5c. Inject the widget (choose one method)

**Method A — Add to portal's base HTML template (recommended if you have template access):**

```html
<!-- Add before </body> in the portal's shared base template -->
<script src="/static/html2canvas.min.js"></script>
<script src="/static/mosdac-chat-widget.js"></script>
```

**Method B — NGINX sub_filter (zero portal code changes):**

See Section 9.

**Method C — Internal browser extension (zero server changes):**

Create a minimal Chrome extension with a `content_script` that injects the
widget JS into all `mosdac.gov.in` pages. Distribute the unpacked extension
to ISRO workstations via shared drive.

```json
// manifest.json
{
  "manifest_version": 3,
  "name": "MOSDAC Assistant",
  "version": "1.0",
  "content_scripts": [{
    "matches": ["*://www.mosdac.gov.in/*"],
    "js": ["html2canvas.min.js", "mosdac-chat-widget.js"],
    "run_at": "document_end"
  }]
}
```

---

## 6. Screenshot Pipeline

This section explains exactly how a screenshot travels from the browser to
the LLM and back.

```
User clicks camera button
          │
          ▼
html2canvas renders the visible DOM → PNG canvas
          │  (panel is hidden during capture)
          ▼
canvas.toDataURL() → "data:image/png;base64,iVBOR..."
          │
          ▼
JS splits into { mime: "image/png", base64: "iVBOR..." }
Thumbnail shown in attach-preview bar
          │
          ▼
User types question (or leaves blank) and clicks Send
          │
          ▼
POST /chat  {
  session_id: "sess_abc123",
  message: "What does this cyclone track show?",
  screenshot_base64: "iVBOR...",
  screenshot_mime: "image/png"
}
          │
          ▼
FastAPI  _answer_with_image()
  Step 1: HybridRetriever.retrieve(message)
          └─ ChromaDB semantic search  →  relevant document passages
          └─ Neo4j graph query         →  entity relationship paths
  Step 2: Build multimodal LangChain HumanMessage:
          [
            { type: "text",      text: "KNOWLEDGE GRAPH:\n...\nDOCUMENT PASSAGES:\n...\nUser question: ..." },
            { type: "image_url", image_url: { url: "data:image/png;base64,iVBOR..." } }
          ]
  Step 3: Send to Qwen2.5-VL at http://ollama:11434/v1/chat/completions
          (OpenAI-compatible format — works unchanged with Ollama)
          │
          ▼
Qwen2.5-VL processes both text context AND image simultaneously
          │
          ▼
JSON response → FastAPI → JS widget → appended as bot message
```

**Privacy note:** Screenshots are never written to disk. They exist only in
memory for the duration of the HTTP request, then garbage-collected.

---

## 7. System Prompt Configuration

Create `prompts/system_prompt.txt`. Edit this file to change LLM behaviour
without any code changes or Docker rebuilds.

```
# prompts/system_prompt.txt
# ─────────────────────────────────────────────────────────────────────────────
# MOSDAC Graph RAG Chatbot — System Prompt
# Edit this file to reconfigure LLM behaviour.
# Changes take effect on the NEXT chat request — no restart required.
# ─────────────────────────────────────────────────────────────────────────────

You are the MOSDAC (Meteorological & Oceanographic Satellite Data Archival Centre)
Expert Assistant, developed by the Space Applications Centre (SAC), ISRO.

You have access to:
1. A KNOWLEDGE GRAPH of entities extracted from MOSDAC documentation, product
   catalogs, satellite mission details, and oceanographic/meteorological reports.
2. DOCUMENT PASSAGES retrieved from MOSDAC PDFs and HTML pages.
3. A SCREENSHOT of the current portal page the user is looking at (when provided).

────────────────────────────────────────────────────────────────────────────────
KNOWLEDGE GRAPH (entity relationships):
{graph_context}

DOCUMENT PASSAGES (relevant text from MOSDAC documents):
{vector_context}
────────────────────────────────────────────────────────────────────────────────

RESPONSE RULES:
- Answer only from the context above. Do not invent satellite parameters or data.
- If the user provides a screenshot, describe what you see FIRST, then answer
  their question in relation to what is visible on screen.

SCREENSHOT ANALYSIS INSTRUCTIONS:
When a screenshot is attached, follow this structure:
  1. PAGE IDENTIFICATION: Name the MOSDAC tool or page visible
     (e.g., SCORPIO Cyclone Tracker, Eddy Current Map, Satellite Catalog,
      OceanState Viewer, Satellite Image Viewer, Rainfall Nowcast).
  2. VISIBLE DATA SUMMARY: Describe key elements — timestamps, geographic region,
     satellite/sensor name, colour scale legend, any overlaid data layers,
     anomalies, markers, or warning indicators.
  3. ANSWER: Respond to the user's specific question using both the screenshot
     content and the RAG knowledge base above.
  4. NEXT STEPS (optional): Suggest portal actions the user can take next,
     e.g., "You can change the date using the timeline slider at the bottom."

GENERAL RULES:
- Cite document sources using [Source: filename] when quoting from passages.
- If you cannot answer from the provided context, say:
  "I do not have enough information in my knowledge base to answer that.
   Please refer to mosdac.gov.in or contact the MOSDAC helpdesk."
- Keep answers concise. Use bullet points for multi-step instructions.
- Do not answer questions unrelated to MOSDAC, ISRO satellites, meteorology,
  or oceanography.
- Always respond in the same language the user used.
```

### How to update behaviour by editing the prompt

| Goal | What to add/change in `system_prompt.txt` |
|------|-------------------------------------------|
| Support Hindi responses | Add: `Always respond in Hindi.` |
| Restrict to cyclone queries only | Add: `Only answer questions about cyclone tracking and related satellite data.` |
| Add context about a new satellite | Append a paragraph: `INSAT-3DS was launched in 2024 and provides...` |
| Change screenshot analysis depth | Edit the `SCREENSHOT ANALYSIS INSTRUCTIONS` section |
| Change citation format | Edit the `Cite document sources` rule |
| Add a disclaimer | Add a `DISCLAIMER:` paragraph at the end |

---

## 8. .env Changes for Offline Mode

Add/update these keys in your `.env` file:

```dotenv
# ── LLM: Local Qwen via Ollama (fully offline) ──────────────────────────────
QWEN_API_BASE=http://ollama:11434/v1       # use container name inside Docker Compose
QWEN_MODEL=qwen2.5vl:7b                   # vision + text model
QWEN_API_KEY=ollama                        # ignored by Ollama

# ── Disable cloud APIs (not needed offline) ──────────────────────────────────
LONGCAT_API_KEY=disabled
GEMINI_API_KEY=disabled
NVIDIA_API_KEY=disabled

# ── System prompt file ───────────────────────────────────────────────────────
SYSTEM_PROMPT_PATH=/app/prompts/system_prompt.txt

# ── Neo4j (Docker service — use container name) ──────────────────────────────
NEO4J_URI=bolt://neo4j:7687
NEO4J_PASSWORD=your_secure_password_here

# ── ChromaDB ─────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR=/app/chroma_db

# ── Data folders (mounted into Docker) ──────────────────────────────────────
DOWNLOADS_DIR=/path/to/mosdac/html/downloads
ATLASES_DIR=/path/to/mosdac/atlases/pdfs
```

### Offline embeddings with Ollama (no NVIDIA key needed)

If NVIDIA NIM is not available offline, replace the embedder with Ollama:

```python
# graph_rag/embeddings/ollama_embedder.py  (new file)
from functools import lru_cache
from langchain_community.embeddings import OllamaEmbeddings
from graph_rag.config import settings

@lru_cache(maxsize=1)
def get_embedder():
    """Offline embedder via Ollama — pull nomic-embed-text once."""
    base = settings.qwen_api_base.replace("/v1", "")  # http://ollama:11434
    return OllamaEmbeddings(model="nomic-embed-text", base_url=base)
```

Update `graph_rag/embeddings/__init__.py`:

```python
from graph_rag.embeddings.ollama_embedder import get_embedder
__all__ = ["get_embedder"]
```

Pull the embedding model once (while internet is available):

```bash
docker exec mosdac_ollama ollama pull nomic-embed-text
docker exec mosdac_ollama ollama pull qwen2.5vl:7b
```

After this, all model weights are stored in the `ollama_data` Docker volume and
the system runs fully offline.

---

## 9. NGINX Reverse Proxy (Recommended)

Using NGINX avoids CORS issues entirely and injects the widget with no portal
code changes. Add this inside your existing MOSDAC `server {}` block:

```nginx
# /etc/nginx/conf.d/mosdac.conf — add inside existing server block

    # ── Inject widget JS before </body> on every HTML page ──────────────────
    sub_filter '</body>'
        '<script src="/static/html2canvas.min.js"></script>
         <script src="/static/mosdac-chat-widget.js"></script>
         </body>';
    sub_filter_once on;
    sub_filter_types text/html;

    # ── Serve widget static files ────────────────────────────────────────────
    location /static/ {
        alias /var/www/mosdac-chatbot/static/;
        add_header Cache-Control "public, max-age=3600";
    }

    # ── Proxy chat API (eliminates CORS, hides backend port) ────────────────
    location /chatapi/ {
        proxy_pass         http://127.0.0.1:8000/;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
        proxy_buffering    off;
    }
```

Copy static files to the alias path:

```bash
mkdir -p /var/www/mosdac-chatbot/static
cp static/mosdac-chat-widget.js   /var/www/mosdac-chatbot/static/
cp static/html2canvas.min.js      /var/www/mosdac-chatbot/static/
nginx -s reload
```

With this setup, the widget JS should use `API_BASE = '/chatapi'` (already set
in the widget code above).

---

## 10. Deployment Checklist

### One-time setup (requires internet)

```bash
# 1. Clone the repo onto the ISRO server
git clone <your-repo> /opt/mosdac-chatbot
cd /opt/mosdac-chatbot

# 2. Fill in .env
cp .env.example .env
nano .env     # set NEO4J_PASSWORD, DOWNLOADS_DIR, ATLASES_DIR

# 3. Pull Docker images (needs internet — do this before going offline)
docker compose pull
docker pull ollama/ollama:latest
docker pull neo4j:2025.04.0-community
docker pull python:3.11-slim

# 4. Pull LLM and embedding models into Ollama
docker compose up ollama -d
docker exec mosdac_ollama ollama pull qwen2.5vl:7b
docker exec mosdac_ollama ollama pull nomic-embed-text

# 5. Download html2canvas for offline use
curl -o static/html2canvas.min.js \
  https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js

# 6. Build the API container
docker compose build chat_api
```

### First ingest — build the knowledge base

```bash
# Place MOSDAC HTML files in DOWNLOADS_DIR and PDFs in ATLASES_DIR, then:
docker compose up -d
docker exec mosdac_chat_api python main.py ingest
# This populates ChromaDB (vectors) and Neo4j (knowledge graph)
# Expected output: "Ingestion summary: documents loaded: N, chunks indexed: N"
```

### Normal operation (fully offline)

```bash
# Start all services
docker compose up -d

# Check health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# View logs
docker compose logs -f chat_api

# Reload NGINX after widget file updates
nginx -s reload
```

### Re-ingesting after new documents are added

```bash
# Add new files to DOWNLOADS_DIR or ATLASES_DIR, then:
docker exec mosdac_chat_api python main.py ingest
# ChromaDB deduplicates by chunk_id — only new content is indexed
```

### Updating the system prompt

```bash
# Edit the prompt file — changes apply on next request, no restart needed:
nano /opt/mosdac-chatbot/prompts/system_prompt.txt
```

---

## 11. File Structure Summary

```
mosdac-chatbot/
│
├── .env                           ← secrets & config (never commit)
├── .env.example                   ← template
├── docker-compose.yml             ← full stack (Ollama + Neo4j + API)
├── Dockerfile.api                 ← FastAPI container
├── main.py                        ← CLI: ingest / chat / test
├── requirement.txt                ← add: fastapi, uvicorn
│
├── prompts/
│   └── system_prompt.txt          ← EDIT THIS to change LLM behaviour
│
├── static/
│   ├── mosdac-chat-widget.js      ← inject into MOSDAC portal (1 script tag)
│   └── html2canvas.min.js         ← offline screenshot library
│
├── chat_api/                      ← NEW: FastAPI HTTP gateway
│   ├── __init__.py
│   ├── main.py
│   └── models.py
│
├── graph_rag/
│   ├── config.py                  ← add qwen_* + system_prompt_path fields
│   ├── chain/
│   │   └── graph_rag_chain.py     ← load system prompt from file
│   ├── embeddings/
│   │   ├── ollama_embedder.py     ← NEW: offline embedding via Ollama
│   │   └── nvidia_embedder.py     ← keep for optional NIM use
│   ├── llm/
│   │   ├── qwen_client.py         ← NEW: local Qwen via Ollama
│   │   └── longcat_client.py      ← keep for non-offline use
│   └── ... (rest unchanged)
│
├── chroma_db/                     ← auto-created, Docker volume
└── tests/                         ← existing tests (unchanged)
```

---

## Quick Reference Card

| Task | How |
|------|-----|
| Change LLM personality/behaviour | Edit `prompts/system_prompt.txt` |
| Add MOSDAC documents to knowledge base | Copy to `DOWNLOADS_DIR`/`ATLASES_DIR`, run `python main.py ingest` |
| Add widget to portal | Inject 2 `<script>` tags or configure NGINX `sub_filter` |
| Change Qwen model size | Set `QWEN_MODEL=qwen2.5:14b` in `.env`, restart `chat_api` |
| View interactive API docs | `http://localhost:8000/docs` |
| Check what's in Neo4j | `http://localhost:7474` (Neo4j Browser) |
| Clear and re-index everything | Stop stack, delete `chroma_db/` and Neo4j volume, re-run ingest |
| Test without portal | `docker exec mosdac_chat_api python main.py chat` |

---

## 12. Modularization — Deploying to Alternate Domains

The chat backend is **domain-agnostic**. Every visual or behavioural difference
between portals is captured in environment variables and a small widget
config object. No Python source changes are needed to ship to a new portal.

### Architecture for multi-domain deployment

```
chat_api/
├── __init__.py    ← exports app, create_app, ChatService
├── main.py        ← create_app() factory + module-level uvicorn `app`
├── config.py      ← ChatAPISettings (CHAT_API_* env vars: title, CORS, …)
├── session.py     ← InMemorySessionStore + RedisSessionStore + factory
├── service.py     ← ChatService — pure business logic, transport-agnostic
├── routes.py      ← build_router(service) — wires HTTP endpoints
└── models.py      ← Pydantic ChatRequest / ChatResponse

deployments/
├── README.md                 ← per-domain setup guide
├── mosdac.env                ← MOSDAC defaults
├── generic.env               ← starter for any new portal
└── widget-snippets/
    ├── mosdac.html
    └── generic.html

static/
├── graph-rag-chat-widget.js  ← generic widget (configurable)
└── mosdac-chat-widget.js     ← thin shim with MOSDAC branding
```

### Per-domain knobs

All of these can be set in `.env` for a deployment:

| Variable | What it controls |
|---|---|
| `CHAT_API_TITLE` | FastAPI title & default widget title |
| `CHAT_API_BOT_NAME` | Display name for the assistant |
| `CHAT_API_ALLOWED_ORIGINS` | Comma-separated CORS list |
| `CHAT_API_SESSION_BACKEND` | `memory` or `redis` |
| `CHAT_API_REDIS_URL` | Redis connection string when backend=redis |
| `CHAT_API_MAX_HISTORY_TURNS` | History window per session |
| `CHAT_API_ENABLE_SCREENSHOT` | `true`/`false` |
| `CHAT_API_MAX_SCREENSHOT_BYTES` | Reject larger uploads |
| `SYSTEM_PROMPT_PATH` | Persona file path |

The widget can be re-themed entirely in the page:

```html
<script>
  window.GRAPH_RAG_CHAT_CONFIG = {
    apiBase:       '/chatapi',
    title:         'Sandbox Assistant',
    accent:        '#28a745',
    elementPrefix: 'sandbox',
  };
</script>
<script src="/static/graph-rag-chat-widget.js"></script>
```

### New endpoint: `GET /config`

The widget calls this on boot to pick up backend-defined branding, so a page
that only references `<script src="...graph-rag-chat-widget.js">` still
displays the correct title and screenshot toggle.

### Scaling across replicas

Set `CHAT_API_SESSION_BACKEND=redis` and put NGINX in front of N replicas.
Neo4j, Ollama, and ChromaDB can each move to dedicated hosts by editing the
corresponding env vars — no code changes.
