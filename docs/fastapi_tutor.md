# FastAPI Complete Tutorial — From Zero to This Project

> **Who is this for?** A complete beginner who has never used FastAPI before.
> By the end you will understand every FastAPI file in this project AND be able
> to build your own API from scratch.

---

## Table of Contents

1. [What is FastAPI?](#1-what-is-fastapi)
2. [Installation and Your First API](#2-installation-and-your-first-api)
3. [Core Concepts — the Building Blocks](#3-core-concepts--the-building-blocks)
4. [This Project's Architecture — Big Picture](#4-this-projects-architecture--big-picture)
5. [File-by-File: models.py](#5-file-by-file-modelspy)
6. [File-by-File: config.py](#6-file-by-file-configpy)
7. [File-by-File: session.py](#7-file-by-file-sessionpy)
8. [File-by-File: service.py](#8-file-by-file-servicepy)
9. [File-by-File: routes.py](#9-file-by-file-routespy)
10. [File-by-File: main.py](#10-file-by-file-mainpy)
11. [Running the API Locally](#11-running-the-api-locally)
12. [Running the API in Docker](#12-running-the-api-in-docker)
13. [The Auto-Generated Docs (/docs)](#13-the-auto-generated-docs-docs)
14. [Testing FastAPI Endpoints](#14-testing-fastapi-endpoints)
15. [Build Your Own API — Templates and Recipes](#15-build-your-own-api--templates-and-recipes)
16. [Environment Variables and .env Files](#16-environment-variables-and-env-files)
17. [CORS — Letting Browsers Talk to Your API](#17-cors--letting-browsers-talk-to-your-api)
18. [Common Errors and How to Fix Them](#18-common-errors-and-how-to-fix-them)
19. [Quick Reference Cheat Sheet](#19-quick-reference-cheat-sheet)

---

## 1. What is FastAPI?

### The Simple Explanation

Imagine you have a smart chatbot that knows things about satellite data. Now imagine you want a web browser, a mobile app, AND a Python script to all be able to talk to that chatbot. You need a **middleman** — a program that sits on a server, listens for requests ("What is INSAT-3D?"), and sends back answers.

That middleman is called a **web API**, and **FastAPI** is the Python tool you use to build one.

### What FastAPI gives you for free

| Thing | What it means |
|-------|--------------|
| **Automatic documentation** | Visit `/docs` in your browser and see a clickable UI for every endpoint |
| **Automatic validation** | If a client sends bad data, FastAPI rejects it with a helpful error — you write no validation code |
| **Type safety** | Uses Python type hints — your IDE catches bugs before you even run the code |
| **High performance** | As fast as NodeJS, much faster than Flask/Django for I/O-heavy workloads |
| **Async support** | Can handle thousands of simultaneous requests without blocking |

### FastAPI vs Flask vs Django

```
Flask:   Tiny, bare-bones. You build everything yourself.
Django:  Huge, does everything (ORM, auth, admin). Heavy.
FastAPI: Modern middle ground. Fast, typed, auto-documented.
         Best for: AI backends, microservices, REST APIs.
```

This project uses FastAPI because:
- The chatbot needs fast async I/O (LLM calls can take seconds)
- The auto-docs make it easy to test the `/chat` endpoint
- Pydantic validation ensures bad requests are caught before hitting the LLM

---

## 2. Installation and Your First API

### Step 1 — Install FastAPI and a server

```bash
pip install fastapi "uvicorn[standard]"
```

`fastapi` is the framework. `uvicorn` is the web server that runs it (like Apache, but for Python async apps).

### Step 2 — Create your first API (hello.py)

```python
from fastapi import FastAPI

app = FastAPI()          # create the application

@app.get("/")            # this is a ROUTE — GET request to "/"
def read_root():
    return {"message": "Hello, World!"}

@app.get("/greet/{name}")   # {name} is a PATH PARAMETER
def greet(name: str):
    return {"greeting": f"Hello, {name}!"}
```

### Step 3 — Run it

```bash
uvicorn hello:app --reload
```

- `hello` = the filename (`hello.py`)
- `app` = the variable name of your FastAPI instance
- `--reload` = auto-restarts when you save changes (only use in development)

### Step 4 — Test it

Open your browser:
- `http://localhost:8000/` → `{"message": "Hello, World!"}`
- `http://localhost:8000/greet/Alice` → `{"greeting": "Hello, Alice!"}`
- `http://localhost:8000/docs` → Interactive API documentation (Swagger UI)

### What just happened?

```
Browser sends:   GET http://localhost:8000/greet/Alice
                         |
Uvicorn receives the request and passes it to FastAPI
                         |
FastAPI sees "/greet/Alice" matches @app.get("/greet/{name}")
                         |
FastAPI extracts name="Alice" and calls greet("Alice")
                         |
FastAPI converts {"greeting": "Hello, Alice!"} to JSON
                         |
Browser receives: {"greeting": "Hello, Alice!"}
```

---

## 3. Core Concepts — the Building Blocks

Before reading this project's code, you need to understand these 9 building blocks.

---

### 3.1 Routes and HTTP Methods

A **route** = a URL pattern + an HTTP method (GET, POST, DELETE, etc.)

```python
@app.get("/items")          # browser can fetch a list
@app.post("/items")         # browser can create a new item
@app.delete("/items/{id}")  # browser can delete an item
@app.put("/items/{id}")     # browser can replace an item
@app.patch("/items/{id}")   # browser can partially update an item
```

**Rule of thumb:**
- `GET` = read data (no side effects)
- `POST` = create something or send data
- `DELETE` = remove something
- `PUT/PATCH` = update something

---

### 3.2 Path Parameters

Values embedded IN the URL:

```python
@app.get("/users/{user_id}/orders/{order_id}")
def get_order(user_id: int, order_id: str):
    # user_id is automatically converted to int
    # order_id stays as str
    return {"user": user_id, "order": order_id}
```

`GET /users/42/orders/ORD-001` → `user_id=42, order_id="ORD-001"`

---

### 3.3 Query Parameters

Values after the `?` in the URL:

```python
@app.get("/search")
def search(q: str, limit: int = 10, offset: int = 0):
    # q is required (no default)
    # limit and offset are optional (have defaults)
    return {"query": q, "limit": limit, "offset": offset}
```

`GET /search?q=INSAT&limit=5` → `q="INSAT", limit=5, offset=0`

---

### 3.4 Request Body (Pydantic Models)

For POST/PUT requests, the client sends data in the request body as JSON. You describe the expected shape with a **Pydantic model**:

```python
from pydantic import BaseModel
from typing import Optional

class CreateOrderRequest(BaseModel):
    dataset_id: str
    start_date: str
    end_date: str
    region: Optional[str] = None   # optional field

@app.post("/orders")
def create_order(req: CreateOrderRequest):
    # FastAPI automatically:
    # 1. Reads JSON from request body
    # 2. Validates it matches CreateOrderRequest
    # 3. Returns a 422 error if validation fails
    # 4. Passes the validated object to your function
    return {"received": req.dataset_id}
```

Client sends:
```json
POST /orders
Content-Type: application/json

{
  "dataset_id": "3SIMG_L1B_STD",
  "start_date": "2024-08-14",
  "end_date": "2024-08-18"
}
```

---

### 3.5 Response Models

You can declare what the response looks like. FastAPI validates and filters the output:

```python
class OrderResponse(BaseModel):
    order_id: str
    status: str

@app.post("/orders", response_model=OrderResponse)
def create_order(req: CreateOrderRequest):
    # Even if you return extra fields, FastAPI will only include
    # order_id and status in the response
    return {"order_id": "MOCK-123", "status": "queued", "internal_secret": "hidden"}
    # client receives: {"order_id": "MOCK-123", "status": "queued"}
```

---

### 3.6 HTTP Exceptions

When something goes wrong, raise `HTTPException`:

```python
from fastapi import HTTPException

@app.get("/orders/{order_id}")
def get_order(order_id: str):
    if not order_id.startswith("MOCK-"):
        raise HTTPException(status_code=404, detail="Order not found")
    return {"order_id": order_id, "status": "ready"}
```

Common status codes:
- `200` = OK (default)
- `400` = Bad Request (client sent wrong data)
- `401` = Unauthorized (not logged in)
- `403` = Forbidden (logged in but no permission)
- `404` = Not Found
- `422` = Unprocessable Entity (Pydantic validation failed — FastAPI adds this automatically)
- `500` = Internal Server Error

---

### 3.7 APIRouter — Splitting Routes Across Files

When your app grows, you don't want all routes in one file. `APIRouter` lets you define routes in separate files and then combine them:

```python
# orders_router.py
from fastapi import APIRouter
router = APIRouter(prefix="/orders", tags=["orders"])

@router.get("/")
def list_orders():
    return []

@router.post("/")
def create_order():
    return {"created": True}
```

```python
# main.py
from fastapi import FastAPI
from orders_router import router as orders_router

app = FastAPI()
app.include_router(orders_router)
# Now your app has GET /orders/ and POST /orders/
```

The `prefix="/orders"` means every route in that router is automatically prefixed with `/orders`.

---

### 3.8 Middleware (CORS)

**Middleware** is code that runs on EVERY request before it reaches your route function. The most important one for this project is **CORS middleware**.

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],   # which websites can call this API
    allow_methods=["GET", "POST"],              # which HTTP methods are allowed
    allow_headers=["*"],                        # which request headers are allowed
)
```

More details in [Section 18](#18-cors--letting-browsers-talk-to-your-api).

---

### 3.9 Dependency Injection and Headers

FastAPI can automatically extract special values like HTTP headers:

```python
from fastapi import Header
from typing import Optional

@app.post("/chat")
def chat(
    req: ChatRequest,
    x_user_id: Optional[str] = Header(default=None),
    # FastAPI maps "X-User-Id" header to x_user_id variable
):
    return {"user": x_user_id, "message": req.message}
```

---

## 4. This Project's Architecture — Big Picture

Here's how all the files connect:

```
HTTP Request from browser / JS widget
         |
         v
+----------------------------------------------------------+
|                   chat_api/main.py                       |
|  create_app() assembles everything into one FastAPI app  |
|  app = FastAPI(title=...) + CORS middleware              |
|  app.include_router(build_router(service))               |
+----------------------------------------------------+-----+
                                                     |
                                          +------------+----------+
                                          |   chat_api/           |
                                          |   routes.py           |
                                          |                       |
                                          |  GET  /health         |
                                          |  GET  /config         |
                                          |  POST /chat           |
                                          |  DELETE /chat/{id}    |
                                          +----------+------------+
                                                     | calls
                                          +----------v------------+
                                          |  chat_api/            |
                                          |  service.py           |
                                          |  ChatService          |
                                          +----------+------------+
                                                     | uses
                            +-------------+------------------+
                            |             |                  |
                            v             v                  v
                     HybridRetriever  LangChain Chain   SessionStore
                     (Neo4j+ChromaDB) (RAG + LLM)      (memory/redis)
                                                            ^
                                                  chat_api/session.py

Supporting files:
  chat_api/models.py  -- Pydantic request/response shapes
  chat_api/config.py  -- Settings loaded from environment variables
```

**Flow of a single chat request:**

```
1. Client POSTs to /chat with {"session_id": "abc", "message": "what is INSAT-3D?"}
2. FastAPI reads routes.py → calls service.chat()
3. service.py builds history prefix from session.py
4. service.py calls chain.invoke() → retriever fetches from Neo4j + ChromaDB
5. LLM generates answer
6. service.py saves message + answer to session store
7. routes.py returns ChatResponse to client
```

---

## 5. File-by-File: models.py

**File:** `chat_api/models.py`
**Purpose:** Defines the shape of data going IN and OUT of the API.

```python
from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    session_id: str                           # required — identifies the conversation
    message: str                              # required — the user's question
    screenshot_base64: Optional[str] = None  # optional — base64-encoded image
    screenshot_mime: Optional[str] = "image/png"  # defaults to PNG

class ChatResponse(BaseModel):
    answer: str      # the chatbot's reply
    session_id: str  # echoed back so the client knows which session this belongs to
```

### Why Pydantic?

When a client sends this JSON:
```json
{"session_id": "user123", "message": "What is INSAT-3D?"}
```

FastAPI automatically:
1. Parses the JSON
2. Checks all required fields are present (`session_id` and `message`)
3. Checks types match (`session_id` must be a string, not a number)
4. Creates a `ChatRequest` object you can use in your function

If the client sends:
```json
{"message": "What is INSAT-3D?"}
```
(missing `session_id`)

FastAPI returns a `422 Unprocessable Entity` error automatically — you write zero validation code.

### Field Validation (extra power)

You can add constraints using `Field`:
```python
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    # "..." means required
    # min_length/max_length are validated automatically
```

### How to add a new field

Say you want to accept a `language` field:
```python
class ChatRequest(BaseModel):
    session_id: str
    message: str
    language: str = "en"   # optional, defaults to English
    screenshot_base64: Optional[str] = None
    screenshot_mime: Optional[str] = "image/png"
```

The client can now send `{"session_id": "x", "message": "hi", "language": "fr"}` and
`req.language` will be `"fr"` in your route function.

---

## 6. File-by-File: config.py

**File:** `chat_api/config.py`
**Purpose:** All configurable settings for the API, loaded from environment variables.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class ChatAPISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",           # read from a .env file
        env_prefix="CHAT_API_",    # ALL env vars must start with CHAT_API_
        extra="ignore",            # ignore unknown env vars
        case_sensitive=False,      # CHAT_API_TITLE and chat_api_title are the same
    )

    # Branding
    title: str = "Graph RAG Chatbot API"   # default value if no env var set
    version: str = "1.0.0"
    bot_name: str = "Assistant"

    # CORS
    allowed_origins: str = "http://localhost,http://127.0.0.1"
    allowed_methods: str = "GET,POST,DELETE,OPTIONS"
    allowed_headers: str = "*"

    # Session
    max_history_turns: int = 10
    session_backend: str = "memory"  # "memory" or "redis"
    redis_url: str = ""

    # Multimodal
    enable_screenshot: bool = True
    max_screenshot_bytes: int = 8 * 1024 * 1024  # 8 MB

chat_api_settings = ChatAPISettings()   # singleton — import this everywhere
```

### How the env_prefix works

The prefix `CHAT_API_` means:
- `title` field reads from `CHAT_API_TITLE` env var
- `bot_name` field reads from `CHAT_API_BOT_NAME` env var
- `max_history_turns` field reads from `CHAT_API_MAX_HISTORY_TURNS` env var

So in your `.env` file:
```env
CHAT_API_TITLE=MOSDAC Chatbot
CHAT_API_BOT_NAME=SatBot
CHAT_API_MAX_HISTORY_TURNS=20
CHAT_API_SESSION_BACKEND=redis
CHAT_API_REDIS_URL=redis://localhost:6379
```

No code changes needed — just change the `.env` file to deploy with different settings.

### How to add a new setting

```python
class ChatAPISettings(BaseSettings):
    # ... existing fields ...
    max_message_length: int = 2000    # add your field with a default
    enable_debug_mode: bool = False   # booleans work: "true"/"false" in .env
```

Then in `.env`:
```env
CHAT_API_MAX_MESSAGE_LENGTH=5000
CHAT_API_ENABLE_DEBUG_MODE=true
```

### Helper methods on the settings class

The settings class has methods that parse comma-separated strings:
```python
def origins_list(self) -> List[str]:
    return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
```

So `CHAT_API_ALLOWED_ORIGINS=http://localhost,http://mysite.com` becomes
`["http://localhost", "http://mysite.com"]`.

---

## 7. File-by-File: session.py

**File:** `chat_api/session.py`
**Purpose:** Stores the conversation history for each user session.

### What is a session?

When you chat with an AI, each exchange needs to be remembered so the AI can answer follow-up questions in context. A **session** is one continuous conversation identified by a unique ID.

### The Protocol (interface)

```python
from typing import Protocol, List, Dict, Any

class SessionStore(Protocol):
    """Every backend must implement these four methods."""

    def get(self, session_id: str) -> List[Dict[str, Any]]: ...
    # Returns: [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]

    def append(self, session_id: str, role: str, content: str) -> None: ...
    # Adds one message to the session

    def clear(self, session_id: str) -> None: ...
    # Deletes the entire session

    def trim(self, session_id: str, max_turns: int) -> None: ...
    # Keeps only the last N turns (prevents session from growing forever)
```

A **Protocol** in Python is like a contract — any class that has these four methods is considered a valid `SessionStore`. This means you can swap backends without changing the routes or service.

### Backend 1: InMemorySessionStore

```python
from collections import defaultdict

class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, List] = defaultdict(list)
        # defaultdict(list) means accessing a key that doesn't exist
        # automatically creates an empty list instead of raising KeyError

    def get(self, session_id: str) -> List:
        return self._sessions[session_id]

    def append(self, session_id: str, role: str, content: str) -> None:
        self._sessions[session_id].append({"role": role, "content": content})

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)  # pop with default None = no error if missing

    def trim(self, session_id: str, max_turns: int) -> None:
        if len(self._sessions[session_id]) > max_turns * 2:
            # Keep only the last N turns (each turn = 2 messages: user + assistant)
            self._sessions[session_id] = self._sessions[session_id][-max_turns * 2:]
```

**Limitation:** Data lives in RAM. Restart the server = all sessions lost. Fine for development.

### Backend 2: RedisSessionStore

```python
import redis, json

class RedisSessionStore:
    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._key_prefix = "chat_api:session:"

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"
        # e.g., "chat_api:session:user123"

    def get(self, session_id: str) -> List:
        raw = self._client.lrange(self._key(session_id), 0, -1)
        # lrange = get all items from a Redis list
        return [json.loads(r) for r in raw]

    def append(self, session_id: str, role: str, content: str) -> None:
        self._client.rpush(
            self._key(session_id),
            json.dumps({"role": role, "content": content})
        )
        # rpush = push to the RIGHT end of a Redis list

    def clear(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))

    def trim(self, session_id: str, max_turns: int) -> None:
        self._client.ltrim(self._key(session_id), -max_turns * 2, -1)
        # ltrim keeps only items from index -N to -1 (the last N items)
```

**Advantage:** Survives server restarts. Works across multiple server instances.

### The Factory Function

```python
def build_session_store() -> SessionStore:
    """Reads config and returns the right backend."""
    backend = chat_api_settings.session_backend.lower()
    if backend == "redis":
        if not chat_api_settings.redis_url:
            raise RuntimeError("CHAT_API_REDIS_URL is empty")
        return RedisSessionStore(chat_api_settings.redis_url)
    return InMemorySessionStore()
```

Switch from memory to Redis by changing one line in `.env`:
```env
CHAT_API_SESSION_BACKEND=redis
CHAT_API_REDIS_URL=redis://localhost:6379
```

### How to add a third backend (e.g., SQLite)

```python
import sqlite3, json

class SqliteSessionStore:
    def __init__(self, path: str = "sessions.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT,
                role TEXT,
                content TEXT,
                created_at REAL DEFAULT (unixepoch())
            )
        """)

    def get(self, session_id: str) -> List:
        rows = self._conn.execute(
            "SELECT role, content FROM sessions WHERE session_id=? ORDER BY created_at",
            (session_id,)
        ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]

    def append(self, session_id: str, role: str, content: str) -> None:
        self._conn.execute(
            "INSERT INTO sessions(session_id, role, content) VALUES (?,?,?)",
            (session_id, role, content)
        )
        self._conn.commit()

    def clear(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        self._conn.commit()

    def trim(self, session_id: str, max_turns: int) -> None:
        self._conn.execute("""
            DELETE FROM sessions WHERE session_id=? AND rowid NOT IN (
                SELECT rowid FROM sessions WHERE session_id=?
                ORDER BY created_at DESC LIMIT ?
            )
        """, (session_id, session_id, max_turns * 2))
        self._conn.commit()
```

Add `"sqlite"` to `build_session_store()` and you have a third backend with zero changes to routes or service.

---

## 8. File-by-File: service.py

**File:** `chat_api/service.py`
**Purpose:** Business logic — coordinates the retriever, chain, LLM, and session store to produce an answer.

### Why a service layer?

This is the key architectural decision: **routes know nothing about business logic; the service knows nothing about HTTP**.

```
routes.py  -- handles HTTP (parse request, return response, catch exceptions)
service.py -- handles logic (build history, call retriever, call LLM, save session)
```

This means:
- You can test `ChatService` with no HTTP server running
- You can reuse `ChatService` from a CLI or gRPC server, not just FastAPI
- Routes stay thin and readable

### The constructor

```python
class ChatService:
    def __init__(
        self,
        retriever,       # HybridRetriever — fetches context from Neo4j + ChromaDB
        chain,           # LangChain LCEL chain — formats prompt + calls LLM
        llm,             # Raw LLM for multimodal (image) calls
        sessions: SessionStore,
        max_history: Optional[int] = None,
    ) -> None:
        self._retriever = retriever
        self._chain = chain
        self._llm = llm
        self._sessions = sessions
        self._max_history = max_history or chat_api_settings.max_history_turns
```

All dependencies are **injected** — passed in, not created inside. This makes testing easy.

### Building conversation history

```python
def _build_history_prefix(self, session_id: str) -> str:
    turns = self._sessions.get(session_id)
    if not turns:
        return ""
    lines = []
    for t in turns:
        role = "User" if t["role"] == "user" else "Assistant"
        content = t["content"] if isinstance(t["content"], str) else "[image]"
        lines.append(f"{role}: {content}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\nNew question: "
```

This builds a text block like:
```
Conversation so far:
User: What is INSAT-3D?
Assistant: INSAT-3D is a geostationary satellite...
User: What bands does it have?
Assistant: It has visible, infrared, and water vapour bands.

New question:
```

This is then prepended to the new question so the LLM has context.

### Text-only path

```python
def _answer_text_only(self, message: str, session_id: str) -> str:
    history_prefix = self._build_history_prefix(session_id)
    return self._chain.invoke({
        "question": message,
        "history": history_prefix
    })
```

### Image (multimodal) path

```python
def _answer_with_image(self, message, screenshot_b64, mime, session_id) -> str:
    self._validate_screenshot(screenshot_b64)  # size + base64 format check

    # Retrieve RAG context (same as text path)
    ctx = self._retriever.retrieve(message)
    rag_preamble = (
        f"KNOWLEDGE GRAPH:\n{ctx['graph_context']}\n\n"
        f"DOCUMENT PASSAGES:\n{ctx['vector_context']}\n\n"
        f"User question about the attached screenshot: {message}"
    )

    # Build a multimodal message: text + image
    content = []
    if history_prefix:
        content.append({"type": "text", "text": history_prefix})
    content.append({"type": "text", "text": rag_preamble})
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{screenshot_b64}"}
    })

    # Call the VL (vision-language) LLM directly
    response = self._llm.invoke([HumanMessage(content=content)])
    return response.content
```

### The main entry point

```python
def chat(self, session_id, message, screenshot_b64=None, screenshot_mime="image/png") -> str:
    # 1. Trim old history so session doesn't grow forever
    self._sessions.trim(session_id, self._max_history)

    # 2. Pick path based on whether an image was sent
    if screenshot_b64:
        answer = self._answer_with_image(...)
    else:
        answer = self._answer_text_only(message, session_id)

    # 3. Save both the user message and assistant answer to history
    self._sessions.append(session_id, "user", message)
    self._sessions.append(session_id, "assistant", answer)

    return answer
```

---

## 9. File-by-File: routes.py

**File:** `chat_api/routes.py`
**Purpose:** Defines the HTTP endpoints. Thin — just HTTP concerns, delegates everything to `ChatService`.

```python
from fastapi import APIRouter, HTTPException
from chat_api.models import ChatRequest, ChatResponse
from chat_api.service import ChatService

def build_router(service: ChatService) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health():
        return {
            "status": "ok",
            "title": chat_api_settings.title,
            "version": chat_api_settings.version,
        }

    @router.get("/config")
    def widget_config():
        """Served to the JS widget for branding."""
        return {
            "title": chat_api_settings.title,
            "bot_name": chat_api_settings.bot_name,
            "screenshot_enabled": chat_api_settings.enable_screenshot,
            "max_screenshot_bytes": chat_api_settings.max_screenshot_bytes,
        }

    @router.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest):
        try:
            answer = service.chat(
                session_id=req.session_id,
                message=req.message,
                screenshot_b64=req.screenshot_base64,
                screenshot_mime=req.screenshot_mime,
            )
            return ChatResponse(answer=answer, session_id=req.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.exception("Chat error: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))

    @router.delete("/chat/{session_id}")
    def clear_session(session_id: str):
        service.clear_session(session_id)
        return {"cleared": session_id}

    return router
```

### Key patterns here

**Why `build_router(service)` instead of a global `service`?**

The `service` is passed in rather than imported as a global. This means:
- Tests can pass a fake `service` with no LLM
- No global state = no test interference

**Exception handling pattern:**

```python
try:
    answer = service.chat(...)
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc))  # client's fault
except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc))  # server's fault
```

`ValueError` = something the client did wrong → 400
`Exception` = unexpected server error → 500

### How to add a new route

```python
@router.get("/history/{session_id}")
def get_history(session_id: str):
    """Return the conversation history for a session."""
    history = service.get_history(session_id)  # add this method to ChatService
    return {"session_id": session_id, "messages": history}
```

---

## 10. File-by-File: main.py

**File:** `chat_api/main.py`
**Purpose:** The application factory — assembles all pieces into one FastAPI app.

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from chat_api.config import chat_api_settings
from chat_api.routes import build_router
from chat_api.service import ChatService
from chat_api.session import build_session_store

def create_app(
    *,                         # all arguments must be named (keyword-only)
    retriever=None,
    chain=None,
    llm=None,
    sessions=None,
    service: ChatService | None = None,
) -> FastAPI:
    """Application factory. Inject test doubles or alternate backends here."""

    # 1. Create the FastAPI app
    app = FastAPI(
        title=chat_api_settings.title,
        version=chat_api_settings.version,
    )

    # 2. Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=chat_api_settings.origins_list(),
        allow_methods=chat_api_settings.methods_list(),
        allow_headers=chat_api_settings.headers_list(),
    )

    # 3. Build the service (unless a test double was injected)
    if service is None:
        # Lazy imports so tests can construct create_app() without LLM
        from graph_rag.chain.graph_rag_chain import build_graph_rag_chain
        from graph_rag.llm.qwen_client import get_llm
        from graph_rag.retrieval.hybrid_retriever import HybridRetriever

        retriever = retriever or HybridRetriever()
        chain = chain or build_graph_rag_chain(retriever=retriever)
        llm = llm or get_llm()
        sessions = sessions or build_session_store()
        service = ChatService(retriever=retriever, chain=chain, llm=llm, sessions=sessions)

    # 4. Register routes
    app.include_router(build_router(service))

    return app


# Module-level singleton -- uvicorn uses this
app = create_app()
```

### Why the App Factory pattern?

The `create_app()` function pattern is the most important architectural decision in this file.

**Problem without it:**
```python
# Bad: global state, impossible to test without real Neo4j
app = FastAPI()
retriever = HybridRetriever()   # connects to Neo4j at import time!
chain = build_chain(retriever)
service = ChatService(retriever, chain, llm, InMemorySessionStore())
```

If a test imports this file, it immediately tries to connect to Neo4j. If Neo4j isn't running, all tests fail.

**Solution — the factory:**
```python
# Good: lazy construction, injectable dependencies
def create_app(*, service=None) -> FastAPI:
    app = FastAPI()
    if service is None:
        service = ChatService(HybridRetriever(), ...)  # only runs in production
    app.include_router(build_router(service))
    return app

# Tests pass a fake service:
from unittest.mock import MagicMock
fake_service = MagicMock()
test_app = create_app(service=fake_service)
```

### The module-level `app = create_app()`

```python
app = create_app()
```

This is the **production singleton**. When uvicorn starts with `uvicorn chat_api.main:app`,
it imports `chat_api.main` and uses the `app` variable. The factory runs once at startup,
connects to all backends, and the resulting `app` handles all requests.

---

## 11. Running the API Locally

### Prerequisites

```bash
# Confirm Python 3.11+
python --version

# Install dependencies
pip install -r requirement.txt
pip install fastapi "uvicorn[standard]"
```

### Option A — Run with a real backend

```bash
# 1. Fill in your .env
cp .env.example .env    # (or create .env manually)

# 2. Start the API
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Test
curl http://localhost:8000/health
```

### Option B — Development with verbose logs

```bash
uvicorn chat_api.main:app --host 0.0.0.0 --port 8000 --reload --log-level debug
```

### Testing with curl

```bash
# Health check
curl http://localhost:8000/health

# Send a chat message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test123", "message": "What is INSAT-3D?"}'

# Clear a session
curl -X DELETE http://localhost:8000/chat/test123
```

---

## 12. Running the API in Docker

### The Dockerfile.api explained line by line

```dockerfile
FROM python:3.11-slim
# Start from Python 3.11 (slim = smaller image, no extras)

WORKDIR /app
# All subsequent commands run in /app directory inside the container

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils libgl1 \
    && rm -rf /var/lib/apt/lists/*
# tesseract-ocr = OCR for reading text from PDFs/images
# poppler-utils = PDF rendering tools (used by pdf2image)
# libgl1 = OpenGL library needed by OpenCV
# rm -rf /var/lib/apt/lists/* = delete apt cache to shrink image size

COPY requirement.txt .
RUN pip install --no-cache-dir -r requirement.txt \
    fastapi "uvicorn[standard]" \
 && python -m spacy download en_core_web_sm
# --no-cache-dir = don't cache pip downloads (smaller image)
# spacy download = download the English language model for NLP

COPY . .
# Copy all project files into the container
# This is LAST so changing code doesn't re-run the slow pip install step

CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
# --host 0.0.0.0 = listen on all interfaces (required inside Docker)
```

### Build and run manually

```bash
# Build the image
docker build -f Dockerfile.api -t mosdac-chat-api .

# Run it
docker run -p 8000:8000 --env-file .env mosdac-chat-api

# Test it
curl http://localhost:8000/health
```

### Run with Docker Compose (all services together)

```bash
# Start everything with Ollama backend
docker compose --profile ollama up

# See logs
docker compose logs chat_api --follow

# Rebuild after code changes
docker compose build chat_api && docker compose --profile ollama up chat_api
```

---

## 13. The Auto-Generated Docs (/docs)

FastAPI generates interactive documentation automatically from your code.

### Swagger UI — /docs

Open `http://localhost:8000/docs` in your browser.

You will see every endpoint listed with:
- The HTTP method and path
- A description (from the docstring)
- Request body schema (from Pydantic models)
- Response schema
- A **"Try it out"** button to send real requests

To test the `/chat` endpoint:
1. Click `POST /chat`
2. Click `Try it out`
3. Fill in the request body:
   ```json
   {
     "session_id": "test123",
     "message": "What is INSAT-3D?"
   }
   ```
4. Click `Execute`
5. See the response

### ReDoc — /redoc

Open `http://localhost:8000/redoc` for a cleaner, read-only version of the docs.

### Adding descriptions to your endpoints

```python
@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Send a chat message",
    description="Send a message to the chatbot. Optionally include a screenshot.",
    response_description="The chatbot's answer",
)
def chat(req: ChatRequest):
    """
    Send a message to the chatbot.

    - **session_id**: Unique identifier for the conversation
    - **message**: The user's question
    - **screenshot_base64**: Optional base64-encoded image
    """
    ...
```

---

## 14. Testing FastAPI Endpoints

### Using httpx TestClient

FastAPI ships with a test client that lets you test endpoints without a real server:

```python
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from chat_api.main import create_app

def test_health_endpoint():
    fake_service = MagicMock()
    app = create_app(service=fake_service)
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_chat_endpoint():
    fake_service = MagicMock()
    fake_service.chat.return_value = "INSAT-3D is a geostationary satellite."
    app = create_app(service=fake_service)
    client = TestClient(app)

    response = client.post("/chat", json={
        "session_id": "test-session",
        "message": "What is INSAT-3D?"
    })

    assert response.status_code == 200
    assert "INSAT-3D" in response.json()["answer"]

def test_chat_returns_400_for_bad_input():
    fake_service = MagicMock()
    fake_service.chat.side_effect = ValueError("Screenshot too large")
    app = create_app(service=fake_service)
    client = TestClient(app)

    response = client.post("/chat", json={
        "session_id": "x",
        "message": "hi",
        "screenshot_base64": "fake-data"
    })

    assert response.status_code == 400
    assert "Screenshot too large" in response.json()["detail"]
```

### Testing with pytest

```bash
# Run all tests
pytest tests/

# Run a specific file
pytest tests/test_chat_api.py

# Run with verbose output
pytest tests/ -v
```

---

## 15. Build Your Own API — Templates and Recipes

### Recipe 1: Minimal Working API (3 files)

**Project structure:**
```
my_api/
  __init__.py
  models.py
  routes.py
  main.py
```

**models.py**
```python
from pydantic import BaseModel
from typing import Optional

class ItemCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float

class ItemResponse(BaseModel):
    id: int
    name: str
    price: float
```

**routes.py**
```python
from fastapi import APIRouter, HTTPException
from my_api.models import ItemCreate, ItemResponse

_fake_db = {}
_next_id = 1

def build_router() -> APIRouter:
    router = APIRouter(prefix="/items", tags=["items"])

    @router.get("/", response_model=list[ItemResponse])
    def list_items():
        return list(_fake_db.values())

    @router.post("/", response_model=ItemResponse)
    def create_item(req: ItemCreate):
        global _next_id
        item = {"id": _next_id, "name": req.name, "price": req.price}
        _fake_db[_next_id] = item
        _next_id += 1
        return item

    @router.get("/{item_id}", response_model=ItemResponse)
    def get_item(item_id: int):
        if item_id not in _fake_db:
            raise HTTPException(status_code=404, detail="Item not found")
        return _fake_db[item_id]

    @router.delete("/{item_id}")
    def delete_item(item_id: int):
        if item_id not in _fake_db:
            raise HTTPException(status_code=404, detail="Item not found")
        del _fake_db[item_id]
        return {"deleted": item_id}

    return router
```

**main.py**
```python
from fastapi import FastAPI
from my_api.routes import build_router

def create_app() -> FastAPI:
    app = FastAPI(title="My Item API", version="1.0.0")
    app.include_router(build_router())
    return app

app = create_app()
```

**Run:**
```bash
uvicorn my_api.main:app --reload
```

---

### Recipe 2: API with SQLite Database

```python
# database.py
import sqlite3

def get_db():
    conn = sqlite3.connect("users.db")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL
        )
    """)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    return conn
```

```python
# routes.py
from fastapi import APIRouter, HTTPException
from .database import get_db

def build_router() -> APIRouter:
    router = APIRouter(prefix="/users", tags=["users"])

    @router.post("/")
    def create_user(username: str, email: str):
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users(username, email) VALUES (?,?)",
                (username, email)
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            return dict(row)
        except Exception:
            raise HTTPException(status_code=409, detail="Username already taken")
        finally:
            db.close()

    return router
```

---

### Recipe 3: API with API Key Authentication

```python
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
import os

API_KEY = os.getenv("MY_API_KEY", "dev-key")
api_key_header = APIKeyHeader(name="X-API-Key")

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key

app = FastAPI()

@app.get("/protected", dependencies=[Depends(verify_api_key)])
def protected_endpoint():
    return {"message": "You are authenticated!"}
```

Client call:
```bash
curl http://localhost:8000/protected -H "X-API-Key: dev-key"
```

---

### Recipe 4: Async Endpoints (for slow I/O operations)

```python
import asyncio, httpx
from fastapi import FastAPI

app = FastAPI()

@app.get("/weather/{city}")
async def get_weather(city: str):
    # async HTTP call — does not block other requests while waiting
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://wttr.in/{city}?format=3")
    return {"city": city, "weather": resp.text}
```

Use `async def` when your function does I/O (database calls, HTTP requests, file reads).
Use plain `def` for CPU-bound work.

---

### Recipe 5: Add a New Feature to This Project

To add a new feature (e.g., a `/feedback` endpoint):

**Step 1: Add a model** in `chat_api/models.py`:
```python
class FeedbackRequest(BaseModel):
    session_id: str
    message_id: str
    rating: int = Field(..., ge=1, le=5)  # 1-5 stars
    comment: Optional[str] = None

class FeedbackResponse(BaseModel):
    accepted: bool
    feedback_id: str
```

**Step 2: Add a method to the service** in `chat_api/service.py`:
```python
def submit_feedback(self, session_id: str, rating: int, comment: str) -> str:
    feedback_id = str(uuid.uuid4())
    # store in database, send to analytics, etc.
    return feedback_id
```

**Step 3: Add the route** in `chat_api/routes.py`:
```python
from chat_api.models import FeedbackRequest, FeedbackResponse

@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest):
    feedback_id = service.submit_feedback(
        session_id=req.session_id,
        rating=req.rating,
        comment=req.comment or ""
    )
    return FeedbackResponse(accepted=True, feedback_id=feedback_id)
```

That's it — the new endpoint appears in `/docs` automatically.

---

## 16. Environment Variables and .env Files

### Why environment variables?

Hard-coding values in code is a security risk:
```python
# BAD — anyone who reads the code gets your password
db_password = "MySecret123"
api_key = "sk-abc123..."
```

Environment variables live outside the code:
```python
# GOOD — value only exists in the environment
import os
db_password = os.getenv("DB_PASSWORD")
```

### The .env file

A `.env` file sets environment variables locally:
```env
# .env file (NEVER commit this to git)
CHAT_API_TITLE=My Chatbot
CHAT_API_BOT_NAME=SatBot
NEO4J_PASSWORD=mypassword
```

Pydantic-Settings reads this automatically when your settings class has `env_file=".env"`.

### .env.example — safe to commit

Create a `.env.example` with placeholder values:
```env
# .env.example — copy to .env and fill in your values
CHAT_API_TITLE=My Chatbot
CHAT_API_BOT_NAME=Assistant
NEO4J_PASSWORD=changeme
LLM_API_BASE=http://ollama:11434/v1
LLM_API_KEY=ollama
```

Add `.env` to `.gitignore`:
```gitignore
.env
*.sqlite
__pycache__/
```

---

## 17. CORS — Letting Browsers Talk to Your API

### What is CORS?

When a browser at `http://mywebsite.com` calls an API at `http://api.example.com`, the browser blocks the request by default. **CORS** (Cross-Origin Resource Sharing) is the mechanism to explicitly allow this.

```
Browser at http://mywebsite.com
  -> "I want to call http://api.example.com/chat"
  -> Browser sends a PREFLIGHT request (OPTIONS method)
  -> API responds: "Yes, I allow requests from http://mywebsite.com"
  -> Browser sends the real POST /chat request
```

Without CORS headers:
```
Access to fetch at 'http://api.example.com/chat' from origin
'http://mywebsite.com' has been blocked by CORS policy.
```

### Adding CORS in FastAPI

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://mywebsite.com", "http://localhost:3000"],
    # "*" allows ALL origins — fine for public APIs, dangerous for private ones

    allow_methods=["GET", "POST", "DELETE"],
    # Which HTTP methods are allowed

    allow_headers=["*"],
    # Which request headers are allowed ("*" = all)
)
```

### In this project

Change origins without touching code — just edit `.env`:
```env
CHAT_API_ALLOWED_ORIGINS=http://localhost,http://mosdac.gov.in,https://app.example.com
```

---

## 18. Common Errors and How to Fix Them

### Error: `422 Unprocessable Entity`
**Cause:** Client sent data that doesn't match your Pydantic model.
```json
{"detail": [{"loc": ["body", "session_id"], "msg": "field required"}]}
```
**Fix:** Check the request body — a required field is missing or has wrong type.

---

### Error: `405 Method Not Allowed`
**Cause:** Called wrong HTTP method (e.g., `GET /chat` but route is `POST /chat`).
**Fix:** Check `/docs` to see which method each endpoint needs.

---

### Error: uvicorn can't find the module
```
ERROR: Could not import module "chat_api.main"
```
**Fix:** Run from the project root:
```bash
cd d:\AI_agents
uvicorn chat_api.main:app --reload
```

---

### Error: `ImportError: No module named 'fastapi'`
**Fix:**
```bash
pip install fastapi "uvicorn[standard]"
```

---

### Error: CORS blocked in browser
```
Access to fetch has been blocked by CORS policy
```
**Fix:** Add your frontend's origin:
```env
CHAT_API_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080
```

---

### Error: Port already in use
```
ERROR: [Errno 10048] error while attempting to bind on address ('0.0.0.0', 8000)
```
**Fix:** Use a different port:
```bash
uvicorn chat_api.main:app --port 8001
```

---

### Error: `500 Internal Server Error` from chat endpoint
**Fix:** Check server logs:
```bash
uvicorn chat_api.main:app --log-level debug
# or in Docker:
docker compose logs chat_api --follow
```

---

## 19. Quick Reference Cheat Sheet

### Project Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Server liveness check |
| GET | `/config` | Branding config for JS widget |
| POST | `/chat` | Send a message to the chatbot |
| DELETE | `/chat/{session_id}` | Clear a conversation |
| GET | `/docs` | Swagger UI (auto-generated) |
| GET | `/redoc` | ReDoc (auto-generated) |

### curl Examples

```bash
# Health check
curl http://localhost:8000/health

# Chat (text only)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","message":"What is INSAT-3D?"}'

# Chat (with screenshot)
SCREENSHOT=$(base64 -w0 screenshot.png)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"session_id\":\"s1\",\"message\":\"What does this show?\",\"screenshot_base64\":\"$SCREENSHOT\"}"

# Clear a session
curl -X DELETE http://localhost:8000/chat/s1
```

### Key Environment Variables

| Variable | Effect | Default |
|----------|--------|---------|
| `CHAT_API_TITLE` | App title in docs | `Graph RAG Chatbot API` |
| `CHAT_API_BOT_NAME` | Bot name shown to users | `Assistant` |
| `CHAT_API_ALLOWED_ORIGINS` | CORS allowed origins | `http://localhost,...` |
| `CHAT_API_SESSION_BACKEND` | `memory` or `redis` | `memory` |
| `CHAT_API_REDIS_URL` | Redis connection string | `""` |
| `CHAT_API_ENABLE_SCREENSHOT` | Allow image uploads | `true` |
| `CHAT_API_MAX_HISTORY_TURNS` | History depth | `10` |
| `LLM_API_BASE` | LLM endpoint URL | `http://ollama:11434/v1` |
| `NEO4J_URI` | Neo4j bolt URL | `bolt://localhost:7687` |

### File Roles at a Glance

| File | Role | Knows about |
|------|------|-------------|
| `chat_api/models.py` | Data shapes | Pydantic only |
| `chat_api/config.py` | Settings | Environment variables |
| `chat_api/session.py` | History storage | Config |
| `chat_api/service.py` | Business logic | Retriever, chain, LLM, sessions |
| `chat_api/routes.py` | HTTP endpoints | Models, service, config |
| `chat_api/main.py` | App assembly | All of the above |
| `Dockerfile.api` | Container build | System deps, Python deps |

### FastAPI Decorators Summary

```python
@app.get("/path")           # GET /path
@app.post("/path")          # POST /path
@app.put("/path/{id}")      # PUT /path/{id}
@app.patch("/path/{id}")    # PATCH /path/{id}
@app.delete("/path/{id}")   # DELETE /path/{id}

# With all options:
@app.post(
    "/path",
    response_model=MyResponse,   # validate and filter response
    status_code=201,             # default success status code
    summary="Short title",       # shows in /docs
    tags=["my-feature"],         # groups endpoints in /docs
)
```
