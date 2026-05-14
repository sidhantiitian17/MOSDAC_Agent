# Complete Docker Guide — AI Agents Codebase

This guide teaches Docker from scratch and then shows you exactly how to run
every service in this project using containers.
No prior Docker knowledge is assumed.

---
# Mode A — Ollama
docker compose --profile ollama up
docker exec mosdac_ollama ollama pull qwen2.5vl:7b   # first run only

# Mode B — vLLM (downloads model ~14 GB on first start)
docker compose --profile vllm up

# Mode C — Cloud/external LLM (no GPU needed)
docker compose up

## Table of Contents

1. [What is Docker and why use it?](#1-what-is-docker-and-why-use-it)
2. [Docker Glossary — key terms explained](#2-docker-glossary)
3. [Install Docker on your machine](#3-install-docker)
4. [The existing Docker files in this project](#4-existing-docker-files)
5. [Environment variables — the .env file](#5-environment-variables)
6. [Run the basic stack (3 services)](#6-run-the-basic-stack)
7. [Add the MCP server, Streamlit UI, and mock MOSDAC](#7-extended-stack)
8. [All essential Docker commands](#8-all-docker-commands)
9. [Understanding volumes and data persistence](#9-volumes-and-data-persistence)
10. [Understanding networks — how containers talk](#10-networks)
11. [How to modify the setup](#11-how-to-modify)
12. [Running individual containers without Compose](#12-run-without-compose)
13. [GPU support for Ollama](#13-gpu-support)
14. [Troubleshooting common problems](#14-troubleshooting)
15. [Production checklist](#15-production-checklist)

---

## 1. What is Docker and why use it?

### The problem Docker solves

Imagine you write code on your laptop with Python 3.12, and it works perfectly.
You then hand it to a colleague who has Python 3.9 — it crashes with errors.
You say "it works on my machine." Docker solves this.

### The solution — containers

A **container** is like a sealed lunchbox.
Inside the lunchbox you pack:
- The exact Python version
- Every library (with the exact version)
- The application code
- All system tools it needs (like `tesseract` for OCR)

When someone else runs your container, they get the exact same lunchbox.
It works everywhere — laptop, server, cloud — identically.

### The difference between a VM and a container

```
Virtual Machine (VM):              Container:
+------------------------+         +------------------------+
| App                    |         | App                    |
| Python 3.12            |         | Python 3.12            |
| Libraries              |         | Libraries              |
| Full OS (Ubuntu, 4 GB) |         | (shared host OS kernel)|
| Virtual hardware       |         | (shared host hardware) |
+------------------------+         +------------------------+
  Slow to start, heavy RAM           Starts in seconds, tiny RAM
```

Containers share the host operating system kernel, so they are much lighter
and start in seconds instead of minutes.

### Why this project uses Docker

This project has many moving parts:
- A Python FastAPI server
- Neo4j graph database
- Ollama LLM server
- ChromaDB vector store
- Optional: Redis, Streamlit, MCP server

Without Docker, you would need to install all of them manually and hope they do not
conflict with other software on your machine. With Docker Compose, you type one
command and they all start, pre-configured to talk to each other.

---

## 2. Docker Glossary

These eight words appear everywhere in Docker documentation.

| Term | Plain English |
|------|---------------|
| **Image** | A blueprint/template. Like a recipe — it describes what goes inside the container. Stored in layers. Read-only. |
| **Container** | A running instance of an image. Like a cooked meal from the recipe. Can be stopped and restarted. |
| **Dockerfile** | A text file with instructions to build an image. `FROM`, `RUN`, `COPY`, `CMD` are the main commands. |
| **Docker Compose** | A tool that starts multiple containers together using a YAML file (`docker-compose.yml`). |
| **Volume** | Persistent storage attached to a container. When the container stops, data in volumes survives. |
| **Network** | A private virtual network that containers use to talk to each other by name (e.g. `neo4j:7687`). |
| **Port mapping** | Connects a port on your laptop to a port inside the container. `-p 8000:8000` means "laptop port 8000 to container port 8000". |
| **Registry** | An online store for images. Docker Hub (hub.docker.com) is the default public registry. `ollama/ollama` means the `ollama` image from user `ollama`. |

---

## 3. Install Docker

### Windows

1. Go to https://www.docker.com/products/docker-desktop/
2. Download **Docker Desktop for Windows**.
3. Run the installer (needs WSL 2 — the installer guides you through it).
4. After install, open Docker Desktop and wait for the whale icon to stop animating.

Verify the install:

```powershell
docker --version
# Docker version 27.x.x, build ...

docker compose version
# Docker Compose version v2.x.x
```

> **Note**: Modern Docker uses `docker compose` (with a space, no hyphen).
> Older versions used `docker-compose` (with a hyphen). Both work here.

### Mac

1. Download Docker Desktop from https://www.docker.com/products/docker-desktop/
2. Drag it to Applications.
3. Open Docker Desktop.

```bash
docker --version
docker compose version
```

### Linux (Ubuntu/Debian)

```bash
# Install Docker Engine
curl -fsSL https://get.docker.com | sh

# Add your user to the docker group (so you do not need sudo every time)
sudo usermod -aG docker $USER
newgrp docker

# Install Docker Compose plugin
sudo apt-get install docker-compose-plugin

# Verify
docker --version
docker compose version
```

---

## 4. Existing Docker Files

This project already has two Docker files. Here is what each line means.

### `Dockerfile.api` — the main application image

```dockerfile
FROM python:3.11-slim
```
Start from an official Python 3.11 image.
`slim` means a stripped-down version — smaller download.

```dockerfile
WORKDIR /app
```
All following commands run from the `/app` directory inside the container.
Think of it as `cd /app` that also creates the folder.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils libgl1 \
    && rm -rf /var/lib/apt/lists/*
```
Install system tools:
- `tesseract-ocr` — reads text from images (OCR)
- `poppler-utils` — reads and converts PDF files
- `libgl1` — a graphics library needed by some Python packages
- `rm -rf /var/lib/apt/lists/*` — delete the apt package cache to keep the image small

```dockerfile
COPY requirement.txt .
RUN pip install --no-cache-dir -r requirement.txt \
    fastapi "uvicorn[standard]" \
 && python -m spacy download en_core_web_sm
```
Copy `requirement.txt` first, then install.
Separating `COPY requirement.txt` from `COPY . .` is intentional:
Docker caches each instruction. If only your code changes (not requirements),
Docker reuses the cached pip layer — much faster rebuilds.

`python -m spacy download en_core_web_sm` downloads the spaCy English NLP model
used for entity extraction.

```dockerfile
COPY . .
```
Copy the rest of your code into `/app`.

```dockerfile
CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```
The default command: start the FastAPI server.
`--host 0.0.0.0` means "listen on all network interfaces" — required inside Docker.

---

### `docker-compose.yml` — the full stack definition

```yaml
version: "3.9"
```
The Compose file format version. `3.9` supports all modern features.

**Service 1 — Ollama**

```yaml
ollama:
  image: ollama/ollama:latest
  container_name: mosdac_ollama
  restart: unless-stopped
  ports:
    - "11434:11434"
  volumes:
    - ollama_data:/root/.ollama
  deploy:
    resources:
      reservations:
        devices:
          - capabilities: [gpu]
```
- `image: ollama/ollama:latest` — use the pre-built Ollama image (no build step needed)
- `container_name: mosdac_ollama` — a human-readable name so logs are easier to read
- `restart: unless-stopped` — if the container crashes, Docker restarts it automatically
- `ports: "11434:11434"` — expose Ollama on your laptop at the same port
- `volumes: ollama_data:/root/.ollama` — Ollama stores downloaded models here; a named volume persists them across container restarts
- `deploy.resources.reservations.devices` — tell Docker to give this container GPU access

**Service 2 — Neo4j**

```yaml
neo4j:
  image: neo4j:2025.04.0-community
  container_name: mosdac_neo4j
  restart: unless-stopped
  ports:
    - "7474:7474"   # Neo4j Browser web UI
    - "7687:7687"   # Bolt protocol (database driver)
  environment:
    NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
    NEO4J_server_memory_heap_max__size: "2G"
  volumes:
    - neo4j_data:/data
    - neo4j_logs:/logs
```
- `environment: NEO4J_AUTH` — Docker reads `NEO4J_PASSWORD` from your `.env` file
  and sets the Neo4j login to `neo4j / <your_password>`.
- `NEO4J_server_memory_heap_max__size: "2G"` — allow Neo4j to use up to 2 GB of RAM.
- Two ports: 7474 for the Neo4j Browser (open in a browser) and 7687 for database connections.

**Service 3 — chat_api**

```yaml
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
    - ./chroma_db:/app/chroma_db
    - ./prompts:/app/prompts
    - ${DOWNLOADS_DIR}:/app/downloads:ro
    - ${ATLASES_DIR}:/app/atlases:ro
```
- `build: context: . dockerfile: Dockerfile.api` — build a custom image from `Dockerfile.api`
- `env_file: .env` — inject all variables from `.env` into the container's environment
- `depends_on` — Docker starts `ollama` and `neo4j` before starting `chat_api`
- `volumes`:
  - `./chroma_db:/app/chroma_db` — the ChromaDB database files live on your laptop and are mounted into the container
  - `./prompts:/app/prompts` — editable system prompts without rebuilding
  - `${DOWNLOADS_DIR}:/app/downloads:ro` — your data folder, mounted read-only (`:ro`)

**Named volumes declaration**

```yaml
volumes:
  ollama_data:
  neo4j_data:
  neo4j_logs:
```
Declares three named volumes. Docker manages their storage location on your laptop.
Named volumes survive `docker compose down` — your data is not deleted when you stop.

---

## 5. Environment Variables

### What is an environment variable?

An environment variable is a key=value setting that a program reads at startup.
Docker passes them into containers so you can change behaviour without editing code.

### The `.env` file

Docker Compose automatically reads `.env` from the project folder.
Variables in `.env` are substituted into `docker-compose.yml` (e.g. `${NEO4J_PASSWORD}`)
and injected into any service that has `env_file: - .env`.

Create your `.env` by copying the template below.
**Never commit `.env` to git** — it contains passwords and API keys.

```dotenv
# -- LLM (which AI brain to use) --------------------------------
# Option A: Local Qwen via Ollama (no internet needed after pull)
QWEN_API_BASE=http://ollama:11434/v1
QWEN_MODEL=qwen2.5:7b
QWEN_API_KEY=ollama

# Option B: LongCat cloud LLM (comment Option A and uncomment these)
# LONGCAT_API_KEY=YOUR_KEY_HERE
# LONGCAT_MODEL=LongCat-Flash-Chat
# LONGCAT_API_BASE=https://api.longcat.chat/openai
# LONGCAT_API_FORMAT=openai

# -- Embeddings --------------------------------------------------
NVIDIA_API_KEY=YOUR_NVIDIA_KEY
NVIDIA_EMBEDDING_MODEL=nvidia/llama-nemotron-embed-1b-v2
GEMINI_API_KEY=YOUR_GEMINI_KEY
GEMINI_EMBEDDING_MODEL=models/gemini-embedding-001

# -- Neo4j -------------------------------------------------------
NEO4J_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=changeme_strong_password
NEO4J_DATABASE=neo4j

# -- ChromaDB ----------------------------------------------------
CHROMA_PERSIST_DIR=./chroma_db
CHROMA_COLLECTION=graph_rag

# -- Data sources ------------------------------------------------
DOWNLOADS_DIR=/path/to/your/downloads
ATLASES_DIR=/path/to/your/atlases_pdfs

# -- Chunking ----------------------------------------------------
CHUNK_SIZE=800
CHUNK_OVERLAP=100

# -- Retrieval ---------------------------------------------------
TOP_K_VECTOR=5
TOP_K_GRAPH=10
GRAPH_DEPTH=2

# -- System prompt -----------------------------------------------
SYSTEM_PROMPT_PATH=./prompts/system_prompt.txt

# -- Chat API ----------------------------------------------------
CHAT_API_TITLE=MOSDAC Graph RAG Chatbot
CHAT_API_BOT_NAME=MOSDAC-Assistant
CHAT_API_MAX_HISTORY_TURNS=10
CHAT_API_ALLOWED_ORIGINS=http://localhost,http://localhost:8501

# -- MOSDAC agent ------------------------------------------------
MOSDAC_USE_MOCK=true
MOSDAC_ENABLE_MOSDAC_ENDPOINT=true
AGENT_LLM_BASE_URL=http://ollama:11434/v1
AGENT_LLM_MODEL=qwen2.5:7b
AGENT_LLM_API_KEY=ollama
AGENT_USE_LOCAL_TOOLS=true
MAX_ORDERS_PER_USER_PER_HOUR=10
MAX_FILES_PER_ORDER=100
```

> **Why `neo4j:7687` and `ollama:11434` instead of `localhost`?**
> Inside Docker networks, containers reach each other by their **service name**, not
> by `localhost`. `neo4j:7687` means "connect to the container named `neo4j` on port 7687".
> When you run the app outside Docker (bare Python), use `localhost:7687` instead.

---

## 6. Run the Basic Stack

### Step 1 — Make sure Docker Desktop is running

Open Docker Desktop. Wait for the whale icon to show "Docker Desktop is running".

### Step 2 — Open a terminal in the project folder

```powershell
# Windows PowerShell
cd D:\AI_agents

# Mac/Linux
cd ~/AI_agents
```

### Step 3 — Build the chat_api image

This downloads base images, installs Python packages, and builds your app image.
Only needed the first time and whenever you change `Dockerfile.api` or `requirement.txt`.

```bash
docker compose build
```

You will see lines like:
```
[+] Building 45.2s (12/12) FINISHED
 => [chat_api] FROM python:3.11-slim
 => [chat_api] RUN apt-get update ...
 => [chat_api] COPY requirement.txt .
 => [chat_api] RUN pip install ...
 => [chat_api] COPY . .
```

### Step 4 — Start all services

```bash
docker compose up -d
```

`-d` means "detached mode" — containers run in the background and your terminal is free.

Check that they are running:

```bash
docker compose ps
```

Expected output:
```
NAME                IMAGE                    STATUS          PORTS
mosdac_chat_api     ai_agents-chat_api       Up 10 seconds   0.0.0.0:8000->8000/tcp
mosdac_neo4j        neo4j:2025.04.0-...      Up 15 seconds   7474/tcp, 7687/tcp
mosdac_ollama       ollama/ollama:latest     Up 20 seconds   0.0.0.0:11434->11434/tcp
```

### Step 5 — Pull the Qwen model into Ollama

Ollama is running but it has no models yet. Pull one:

```bash
# Run a command inside the ollama container:
docker exec mosdac_ollama ollama pull qwen2.5:7b
```

This downloads the model weights (about 4 GB). It only needs to happen once — the weights
are stored in the `ollama_data` volume and survive container restarts.

Check what models Ollama has:

```bash
docker exec mosdac_ollama ollama list
```

### Step 6 — Ingest documents

```bash
# Run the ingest command inside the chat_api container:
docker exec mosdac_chat_api python main.py ingest
```

### Step 7 — Test the endpoints

```bash
# Health check
curl http://localhost:8000/health

# Chat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test","message":"What is MOSDAC?"}'

# MOSDAC agent health (if MOSDAC_ENABLE_MOSDAC_ENDPOINT=true)
curl http://localhost:8000/mosdac/health
```

### Step 8 — Open the Neo4j Browser

Go to http://localhost:7474 in your browser.
Login with: username `neo4j`, password = whatever you set as `NEO4J_PASSWORD` in `.env`.
Run `MATCH (n) RETURN n LIMIT 25` to see the knowledge graph.

### Step 9 — Stop everything

```bash
docker compose down
```

This stops and removes containers. **Volumes (data) are preserved.**

To also delete all data:

```bash
docker compose down -v
```

> **Warning**: `-v` deletes volumes. All Neo4j data, Ollama models, and ChromaDB
> embeddings will be deleted. Only do this if you want a completely clean start.

---

## 7. Extended Stack

The basic `docker-compose.yml` has three services. This section adds three more:
the Streamlit UI, the MCP server, and the mock MOSDAC backend.

### New Dockerfiles needed

Create two additional Dockerfiles in the project root.

#### `Dockerfile.streamlit`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirement.txt .
RUN pip install --no-cache-dir streamlit requests pydantic-settings
COPY mosdac_agent/ ./mosdac_agent/
CMD ["streamlit", "run", "mosdac_agent/streamlit_app.py", \
     "--server.address", "0.0.0.0", \
     "--server.port", "8501"]
```

#### `Dockerfile.mcp`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirement.txt .
RUN pip install --no-cache-dir fastmcp pydantic-settings httpx
COPY mosdac_agent/ ./mosdac_agent/
CMD ["python", "-m", "mosdac_agent.mcp_server"]
```

### Extended `docker-compose.yml`

Replace the existing `docker-compose.yml` with this full version:

```yaml
version: "3.9"

services:

  # -- 1. Qwen LLM via Ollama ------------------------------------------
  ollama:
    image: ollama/ollama:latest
    container_name: mosdac_ollama
    restart: unless-stopped
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  # -- 2. Neo4j Knowledge Graph ----------------------------------------
  neo4j:
    image: neo4j:2025.04.0-community
    container_name: mosdac_neo4j
    restart: unless-stopped
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD}"
      NEO4J_server_memory_heap_max__size: "2G"
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "${NEO4J_PASSWORD}", "RETURN 1"]
      interval: 15s
      timeout: 10s
      retries: 10

  # -- 3. FastAPI Chat Gateway -----------------------------------------
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
      neo4j:
        condition: service_healthy
      ollama:
        condition: service_started
    volumes:
      - ./chroma_db:/app/chroma_db
      - ./prompts:/app/prompts
      - ${DOWNLOADS_DIR}:/app/downloads:ro
      - ${ATLASES_DIR}:/app/atlases:ro
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"

  # -- 4. Fake MOSDAC backend (offline testing) -----------------------
  mock_mosdac:
    build:
      context: .
      dockerfile: Dockerfile.api
    container_name: mosdac_mock_backend
    restart: unless-stopped
    ports:
      - "9000:9000"
    command: ["uvicorn", "mosdac_agent.mock_mosdac:app",
              "--host", "0.0.0.0", "--port", "9000"]
    env_file:
      - .env

  # -- 5. MCP tool server ----------------------------------------------
  mcp_server:
    build:
      context: .
      dockerfile: Dockerfile.mcp
    container_name: mosdac_mcp
    restart: unless-stopped
    ports:
      - "8765:8765"
    env_file:
      - .env
    environment:
      MCP_TRANSPORT: "streamable-http"
      MCP_HOST: "0.0.0.0"
      MCP_PORT: "8765"
    depends_on:
      - chat_api

  # -- 6. Streamlit chat UI --------------------------------------------
  streamlit:
    build:
      context: .
      dockerfile: Dockerfile.streamlit
    container_name: mosdac_streamlit
    restart: unless-stopped
    ports:
      - "8501:8501"
    env_file:
      - .env
    environment:
      CHAT_API: "http://chat_api:8000/mosdac/chat"
      MOSDAC_USER: "dev"
    depends_on:
      - chat_api

volumes:
  ollama_data:
  neo4j_data:
  neo4j_logs:
```

### Start the extended stack

```bash
# Build all new images:
docker compose build

# Start everything:
docker compose up -d

# Check all 6 containers:
docker compose ps
```

Service summary:

| Container | URL in browser | What it is |
|-----------|---------------|------------|
| `mosdac_chat_api` | http://localhost:8000 | FastAPI (GraphRAG + MOSDAC agent) |
| `mosdac_neo4j` | http://localhost:7474 | Neo4j Browser |
| `mosdac_ollama` | http://localhost:11434 | Ollama LLM API |
| `mosdac_mock_backend` | http://localhost:9000 | Fake MOSDAC API |
| `mosdac_mcp` | http://localhost:8765/mcp | MCP tool server |
| `mosdac_streamlit` | http://localhost:8501 | Streamlit chat UI |

---

## 8. All Docker Commands

### Building images

```bash
# Build all services defined in docker-compose.yml
docker compose build

# Build only one service (faster when you change one Dockerfile)
docker compose build chat_api

# Build without using cache (full rebuild, slower but guaranteed fresh)
docker compose build --no-cache

# Build and immediately start
docker compose up -d --build
```

### Starting and stopping containers

```bash
# Start all services in the background
docker compose up -d

# Start only specific services
docker compose up -d neo4j chat_api

# Stop all services (containers removed, volumes kept)
docker compose down

# Stop and delete volumes (WARNING: deletes all data)
docker compose down -v

# Restart a single service
docker compose restart chat_api

# Pause (freeze) a container without removing it
docker compose pause chat_api

# Unpause
docker compose unpause chat_api
```

### Viewing status and logs

```bash
# Show running containers and their status
docker compose ps

# Stream logs from all services (Ctrl+C to stop)
docker compose logs -f

# Stream logs from one service only
docker compose logs -f chat_api

# Show last 100 lines of logs
docker compose logs --tail=100 chat_api

# Show logs with timestamps
docker compose logs --timestamps chat_api
```

### Running commands inside containers

```bash
# Open a bash shell inside the chat_api container
docker exec -it mosdac_chat_api bash

# Run a one-off Python command
docker exec mosdac_chat_api python main.py ingest

# Run pytest inside the container
docker exec mosdac_chat_api pytest tests/ -v

# Pull an Ollama model
docker exec mosdac_ollama ollama pull qwen2.5:7b

# List Ollama models
docker exec mosdac_ollama ollama list

# Run a Cypher query in Neo4j
docker exec mosdac_neo4j cypher-shell -u neo4j -p changeme_strong_password \
  "MATCH (n) RETURN count(n)"
```

### Inspecting images and containers

```bash
# List all images on your machine
docker images

# List all containers (including stopped ones)
docker ps -a

# Show disk usage by Docker
docker system df

# Inspect a container's configuration (JSON)
docker inspect mosdac_chat_api

# See environment variables inside a running container
docker exec mosdac_chat_api env
```

### Cleaning up

```bash
# Remove all stopped containers
docker container prune

# Remove all unused images (images not used by any container)
docker image prune

# Remove everything unused: containers, images, networks, build cache
docker system prune

# Nuclear option: remove EVERYTHING including volumes
docker system prune -a --volumes
```

---

## 9. Volumes and Data Persistence

### Why volumes matter

When a container stops or is deleted, all files written inside it are lost.
Volumes are the solution: they are storage locations managed by Docker that
survive container lifecycle.

### Types of storage in this project

**Named volumes** (managed by Docker, location hidden from you):

```yaml
volumes:
  ollama_data:    # stores Ollama model weights (~4 GB for qwen2.5:7b)
  neo4j_data:     # stores Neo4j graph database
  neo4j_logs:     # stores Neo4j log files
```

Named volumes are created automatically and live somewhere under Docker's storage directory.
You rarely need to know exactly where — Docker manages them.

**Bind mounts** (a folder on your laptop mapped into the container):

```yaml
volumes:
  - ./chroma_db:/app/chroma_db        # ChromaDB vector store
  - ./prompts:/app/prompts            # system prompt files
  - ${DOWNLOADS_DIR}:/app/downloads:ro  # your source files (read-only)
```

Bind mounts use a path on your laptop. If you change files in `./chroma_db` on your laptop,
the change is immediately visible inside the container, and vice versa.

### View and manage volumes

```bash
# List all named volumes
docker volume ls

# Inspect a volume (shows where Docker stores it)
docker volume inspect ai_agents_ollama_data

# Delete a specific volume (ONLY when the container using it is stopped)
docker volume rm ai_agents_neo4j_data

# Delete all volumes not used by any container
docker volume prune
```

### Back up a volume

```bash
# Back up the Neo4j data volume to a tar file on your laptop:
docker run --rm \
  -v ai_agents_neo4j_data:/data \
  -v $(pwd):/backup \
  ubuntu \
  tar czf /backup/neo4j_backup.tar.gz /data
```

### Restore a volume

```bash
# Restore from backup:
docker run --rm \
  -v ai_agents_neo4j_data:/data \
  -v $(pwd):/backup \
  ubuntu \
  tar xzf /backup/neo4j_backup.tar.gz -C /
```

---

## 10. Networks

### How containers talk to each other

When you run `docker compose up`, Docker automatically creates a private network
for your project (named `ai_agents_default` by default).

Every service can reach every other service by its **service name**:

```
chat_api  ---->  neo4j:7687       (connect to neo4j service on port 7687)
chat_api  ---->  ollama:11434     (connect to ollama service on port 11434)
streamlit ---->  chat_api:8000   (connect to chat_api service on port 8000)
```

This is why `.env` uses `NEO4J_URI=bolt://neo4j:7687` and not `localhost:7687`.
`localhost` inside a container means *that container itself*, not Neo4j.

### View networks

```bash
# List all Docker networks
docker network ls

# Inspect the project network (shows which containers are connected)
docker network inspect ai_agents_default
```

### Port mapping explained

```yaml
ports:
  - "8000:8000"   # host_port:container_port
```

This means: when something on your laptop connects to port 8000, Docker routes it
to port 8000 inside the container.

If you change it to `"9999:8000"`, the API is still on port 8000 inside Docker,
but you reach it from your laptop at http://localhost:9999.

---

## 11. How to Modify the Setup

### Change a port

**Scenario**: Port 8000 is already in use by another app on your laptop.

In `docker-compose.yml`, change the left side of the port mapping:

```yaml
chat_api:
  ports:
    - "8080:8000"   # was "8000:8000" -- now access at http://localhost:8080
```

The right side (container port) never changes; only the left side (laptop port) changes.

### Change the LLM model

**Scenario**: You want to use `qwen2.5:14b` instead of `qwen2.5:7b`.

1. Update `.env`:
   ```dotenv
   QWEN_MODEL=qwen2.5:14b
   AGENT_LLM_MODEL=qwen2.5:14b
   ```

2. Pull the new model:
   ```bash
   docker exec mosdac_ollama ollama pull qwen2.5:14b
   ```

3. Restart `chat_api` to pick up the new env var:
   ```bash
   docker compose restart chat_api
   ```

No rebuild needed — the model name comes from env vars, not the image.

### Use a cloud LLM instead of local Ollama

**Scenario**: You want to use OpenAI GPT-4o instead of local Qwen.

1. Update `.env`:
   ```dotenv
   AGENT_LLM_BASE_URL=https://api.openai.com/v1
   AGENT_LLM_MODEL=gpt-4o
   AGENT_LLM_API_KEY=sk-your-openai-key
   ```

2. Restart:
   ```bash
   docker compose restart chat_api
   ```

You can optionally stop the Ollama container to save RAM:

```bash
docker compose stop ollama
```

### Add a new Python package

**Scenario**: You want to add the `redis` package for Redis session storage.

1. Add the package to `requirement.txt`:
   ```
   redis>=5.0
   ```

2. Rebuild the image:
   ```bash
   docker compose build chat_api
   ```

3. Restart the container:
   ```bash
   docker compose up -d chat_api
   ```

### Enable Redis sessions

**Scenario**: You want conversation history to survive server restarts.

1. Add Redis to `docker-compose.yml` under `services:`:

   ```yaml
   redis:
     image: redis:7-alpine
     container_name: mosdac_redis
     restart: unless-stopped
     ports:
       - "6379:6379"
     volumes:
       - redis_data:/data
     command: redis-server --appendonly yes
   ```

   And add to the `volumes:` section at the bottom:
   ```yaml
   volumes:
     ...
     redis_data:
   ```

2. Update `.env`:
   ```dotenv
   CHAT_API_SESSION_BACKEND=redis
   CHAT_API_REDIS_URL=redis://redis:6379/0
   ```

3. Uncomment `redis>=5.0` in `requirement.txt`.

4. Rebuild and restart:
   ```bash
   docker compose build chat_api
   docker compose up -d
   ```

### Change the Neo4j password

1. Update `.env`:
   ```dotenv
   NEO4J_PASSWORD=my_new_strong_password
   ```

2. **If Neo4j has never started yet** — just start it; it will use the new password.

3. **If Neo4j is already running** with the old password, you must delete the
   volume (wiping all graph data) and restart:
   ```bash
   docker compose down
   docker volume rm ai_agents_neo4j_data
   docker compose up -d neo4j
   ```

### Edit the system prompt without rebuilding

The system prompt file is bind-mounted from `./prompts/system_prompt.txt` into
`/app/prompts/system_prompt.txt`. Edit it directly on your laptop:

```bash
# Windows
notepad prompts\system_prompt.txt

# Mac/Linux
nano prompts/system_prompt.txt
```

Then restart only chat_api to reload:

```bash
docker compose restart chat_api
```

### Scale a service to multiple instances

**Scenario**: You expect heavy traffic and want 3 instances of the chat_api.

```bash
docker compose up -d --scale chat_api=3
```

> Note: Remove the fixed `container_name` and change `ports` to not use a fixed
> host port before scaling, because three containers cannot all bind the same host port.

### Change ChromaDB storage location

ChromaDB data is stored in `./chroma_db` by default. To move it:

1. Update `.env`:
   ```dotenv
   CHROMA_PERSIST_DIR=./my_chroma_data
   ```

2. Update the volume in `docker-compose.yml`:
   ```yaml
   volumes:
     - ./my_chroma_data:/app/my_chroma_data
   ```

3. Rebuild and restart.

---

## 12. Run Without Compose (Individual Containers)

Sometimes you want to run just one container manually to test something.

### Run Neo4j alone

```bash
docker run -d \
  --name test_neo4j \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/testpassword \
  neo4j:2025.04.0-community
```

Open http://localhost:7474, login with `neo4j / testpassword`.

Stop and remove when done:

```bash
docker stop test_neo4j
docker rm test_neo4j
```

### Run Ollama alone

```bash
docker run -d \
  --name test_ollama \
  -p 11434:11434 \
  -v ollama_test:/root/.ollama \
  ollama/ollama:latest
```

Pull a model:

```bash
docker exec test_ollama ollama pull qwen2.5:7b
```

### Build and run the chat_api image alone

```bash
# Build the image:
docker build -t mosdac_chat_api:dev -f Dockerfile.api .

# Run it:
docker run -d \
  --name test_chat_api \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/chroma_db:/app/chroma_db \
  mosdac_chat_api:dev
```

> When running standalone, the app cannot reach `neo4j` or `ollama` by name
> because they are not in the same Docker network.
> Either put them in a shared network manually or use Compose instead.

---

## 13. GPU Support for Ollama

By default, Docker containers cannot see your GPU. The `docker-compose.yml`
already requests GPU access for the Ollama service:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - capabilities: [gpu]
```

### Windows — NVIDIA GPU setup

1. Install the latest NVIDIA drivers from https://www.nvidia.com/drivers
2. WSL 2 (required for Docker Desktop on Windows) automatically exposes the GPU.
3. Verify:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi
   ```
   You should see your GPU listed.

### Linux — NVIDIA GPU setup

```bash
# Install NVIDIA Container Toolkit
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

Verify:

```bash
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu20.04 nvidia-smi
```

### Run Ollama without GPU (CPU only)

If you do not have an NVIDIA GPU, remove the `deploy.resources` block from
the Ollama service in `docker-compose.yml`:

```yaml
ollama:
  image: ollama/ollama:latest
  container_name: mosdac_ollama
  restart: unless-stopped
  ports:
    - "11434:11434"
  volumes:
    - ollama_data:/root/.ollama
  # deploy block removed -- runs on CPU only
```

CPU-only inference is much slower but works on any machine.
Use a smaller model:

```bash
docker exec mosdac_ollama ollama pull qwen2.5:3b   # smallest option
```

---

## 14. Troubleshooting Common Problems

### "Port is already allocated"

**Error**:
```
Error starting userland proxy: listen tcp 0.0.0.0:8000: bind: address already in use
```

**Cause**: Something on your laptop is already using that port.

**Fix**:
```bash
# Windows: find what is using port 8000
netstat -ano | findstr :8000
# Note the PID, then:
taskkill /PID <pid> /F

# Mac/Linux:
lsof -i :8000
kill -9 <pid>
```

Or change the port in `docker-compose.yml`:
```yaml
ports:
  - "8080:8000"
```

---

### Container exits immediately

**Error**: Container shows `Exited (1)` in `docker compose ps`.

**Diagnosis**:
```bash
docker compose logs chat_api
```
Read the error. Common causes:
- Missing env var (e.g. `NEO4J_URI` not set)
- Could not connect to Neo4j (Neo4j not fully started yet)
- Python import error in code

---

### "Connection refused" to Neo4j

**Error**: `Failed to establish connection to server bolt://neo4j:7687`

**Cause**: chat_api started before Neo4j was fully ready.

**Fix**:
```bash
# Wait 30 seconds for Neo4j to fully initialize, then restart chat_api:
docker compose restart chat_api
```

Using the health check in the extended `docker-compose.yml` prevents this automatically.

---

### Ollama returns empty responses

**Cause**: No model has been pulled yet.

**Fix**:
```bash
docker exec mosdac_ollama ollama list       # should show at least one model
docker exec mosdac_ollama ollama pull qwen2.5:7b
```

---

### "No space left on device"

**Cause**: Docker images, volumes, and build cache have filled your disk.

**Fix**:
```bash
# See what is taking space:
docker system df

# Remove build cache (safe -- Docker rebuilds on next docker compose build):
docker builder prune

# Remove unused images:
docker image prune -a

# Remove unused volumes:
docker volume prune
```

---

### Changes to code not reflected in the container

**Cause**: Docker is using an old cached image.

**Fix**:
```bash
docker compose build --no-cache chat_api
docker compose up -d chat_api
```

---

### "permission denied" on Linux

**Cause**: The current user is not in the `docker` group.

**Fix**:
```bash
sudo usermod -aG docker $USER
# Log out and log back in, then try again
```

---

### Windows: `.env` variables not loading

**Cause**: Windows line endings (CRLF) in `.env` can confuse some tools.

**Fix**: Open `.env` in VS Code. At the bottom-right, click `CRLF` and switch to `LF`.
Or fix via PowerShell:
```powershell
(Get-Content .env) | Set-Content -Encoding utf8 .env
```

---

### How to explore inside a running container

```bash
# Open a shell:
docker exec -it mosdac_chat_api bash

# Inside the shell you can:
ls /app                    # see the application files
cat /proc/1/environ        # check which env vars were loaded
python -c "import langchain; print(langchain.__version__)"
exit                       # leave the shell
```

---

## 15. Production Checklist

Before deploying to a real server, go through this list.

### Security

- [ ] Change all default passwords (`NEO4J_PASSWORD`, etc.) to strong random strings (min 20 chars)
- [ ] Remove any hardcoded API keys from code (use `.env` only, never commit it)
- [ ] Verify `.env` is in `.gitignore`
- [ ] Set `CHAT_API_ALLOWED_ORIGINS` to only the domains that need access
- [ ] Do NOT expose Neo4j ports (`7474`, `7687`) publicly — only expose `8000`
- [ ] Add authentication to the chat API (implement the `X-MOSDAC-User` SSO header check)
- [ ] Add rate limiting at the reverse proxy (nginx) level

### Performance

- [ ] Use a named volume for `chroma_db` instead of a bind mount (faster I/O on Linux)
- [ ] Set `NEO4J_server_memory_heap_max__size` to at most half your server RAM
- [ ] Use `gunicorn` with multiple workers:
  ```yaml
  chat_api:
    command: ["gunicorn", "chat_api.main:app",
              "-k", "uvicorn.workers.UvicornWorker",
              "-w", "4",
              "--bind", "0.0.0.0:8000"]
  ```

### Reliability

- [ ] All services have `restart: unless-stopped`
- [ ] Neo4j has a health check (included in extended `docker-compose.yml`)
- [ ] `chat_api` uses `condition: service_healthy` for Neo4j dependency
- [ ] Log rotation is configured to prevent disk fill-up

### `.dockerignore` file

Create `.dockerignore` in the project root to exclude unnecessary files from the image.
Smaller images build and start faster:

```
.venv/
__pycache__/
*.pyc
*.pyo
.env
chroma_db/
data/
downloads/
atlases_pdfs/
.git/
*.md
tests/
node_modules/
```

---

## Quick-start Cheat Sheet

```bash
# First time setup
docker compose build                                         # build images
docker compose up -d                                         # start all services
docker exec mosdac_ollama ollama pull qwen2.5:7b             # pull LLM model
docker exec mosdac_chat_api python main.py ingest            # ingest documents

# Daily use
docker compose up -d              # start everything
docker compose down               # stop everything
docker compose ps                 # check status
docker compose logs -f chat_api   # watch logs

# After code changes
docker compose build chat_api     # rebuild changed service
docker compose up -d chat_api     # restart it

# After requirement.txt changes
docker compose build --no-cache chat_api
docker compose up -d chat_api

# Shell access
docker exec -it mosdac_chat_api bash

# Run tests
docker exec mosdac_chat_api pytest tests/ -v

# Clean up disk space
docker system prune               # remove unused stuff (safe)
docker system prune -a --volumes  # remove everything (destructive)
```

---

## Service Port Reference

| Service | Host port | Container port | URL in browser |
|---------|-----------|----------------|----------------|
| FastAPI (chat + MOSDAC) | 8000 | 8000 | http://localhost:8000 |
| Neo4j Browser | 7474 | 7474 | http://localhost:7474 |
| Neo4j Bolt driver | 7687 | 7687 | bolt://localhost:7687 |
| Ollama LLM API | 11434 | 11434 | http://localhost:11434 |
| Mock MOSDAC backend | 9000 | 9000 | http://localhost:9000 |
| MCP tool server | 8765 | 8765 | http://localhost:8765/mcp |
| Streamlit UI | 8501 | 8501 | http://localhost:8501 |

---

*This guide covers the Docker setup as of May 2026.
Update the service table and any `image:` tags when upstream versions change.*
