# How to Integrate the Chatbot with the MOSDAC Drupal Portal

**Who this guide is for:** Someone who has never integrated two web systems before. Every technical term is explained the first time it appears. You do not need to be a programmer — but you do need access to the server where MOSDAC runs.

---

## Table of Contents

1. [The Big Picture — What Are We Actually Doing?](#1-the-big-picture)
2. [Glossary — All the Scary Words Explained](#2-glossary)
3. [How the Two Systems Talk to Each Other](#3-how-they-talk)
4. [Before You Start — What You Need](#4-prerequisites)
5. [Step 1 — Deploy the Chatbot Server](#5-step-1-deploy-chatbot-server)
6. [Step 2 — Configure CORS (Allow the Browser to Connect)](#6-step-2-cors)
7. [Step 3 — Set Up Nginx as a Reverse Proxy](#7-step-3-nginx)
8. [Step 4 — Add the Widget to Drupal](#8-step-4-drupal-widget)
9. [Step 5 — Enable the MOSDAC Agent](#9-step-5-mosdac-agent)
10. [Step 6 — Connect Drupal User Identity](#10-step-6-user-identity)
11. [Step 7 — Ingest MOSDAC Documents into the Knowledge Base](#11-step-7-ingest-documents)
12. [Step 8 — Customise Widget Appearance](#12-step-8-customise)
13. [Step 9 — Test Everything (7-Step Checklist)](#13-step-9-test)
14. [How All the Files Work Together](#14-file-map)
15. [Updating and Maintaining After Launch](#15-maintenance)
16. [Troubleshooting — 6 Common Problems](#16-troubleshooting)
17. [Quick Command Reference](#17-quick-reference)

---

## 1. The Big Picture

Imagine you have two separate buildings:

- **Building A** — the MOSDAC website. It is built with Drupal (a content management system). Users visit `https://www.mosdac.gov.in` and browse weather data, satellite images, and reports.
- **Building B** — the chatbot server. It is a Python program that can answer questions about MOSDAC data by searching a knowledge base and calling an AI language model.

Right now, these two buildings are completely separate. A user visiting the MOSDAC website has no way to talk to the chatbot.

**What integration means:** We are building a **doorway** between the two buildings. We do this in two parts:

1. **On Building B (chatbot server):** We start the chatbot and make it reachable over the internet (or internal network).
2. **On Building A (Drupal website):** We add a small piece of JavaScript code — called a **widget** — that opens a chat panel on every page. When a user types a question, the widget sends it to Building B and shows the answer.

Here is a simple diagram:

```
User's Browser
     │
     ├─── visits ──────────────────────────────► mosdac.gov.in (Drupal)
     │                                            (returns the webpage)
     │
     │    User sees chat button and clicks it
     │
     └─── sends chat message ──► Nginx (the doorman)
                                      │
                                      ▼
                               chatbot server (FastAPI)
                                      │
                                      ├─── searches knowledge base (Neo4j + ChromaDB)
                                      └─── asks LLM for answer
                                      │
                                      ▼
                               answer sent back to browser
```

**What changes on each system:**

| System | What changes |
|--------|-------------|
| Chatbot server | Started inside Docker; exposed on a port |
| Nginx (web server) | Gets a new routing rule: `/chatapi/` → chatbot |
| Drupal theme | Gets two lines of JavaScript added to every page |
| `.env` file | Filled with real passwords and addresses |

---

## 2. Glossary

Read this section once before you start. Come back to it whenever you see an unfamiliar word.

### Drupal
A free, open-source **Content Management System (CMS)**. Think of it like a super-powered website builder that stores pages, articles, and media in a database and lets editors update content without writing code. MOSDAC uses Drupal to manage its public website.

### CMS (Content Management System)
Software that lets non-programmers create and manage website content through a web interface (like a dashboard), instead of editing raw HTML files.

### Widget
A small, self-contained piece of user interface — like a chat bubble or a calendar — that you can drop into any webpage by adding a snippet of HTML/JavaScript. Our chatbot widget is a floating button that opens a chat panel.

### JavaScript Snippet
A short piece of JavaScript code (usually 1–5 lines) that you paste into a webpage. When a browser loads the page, it runs this code. Our snippet loads the chat widget.

### API (Application Programming Interface)
A way for two computer programs to talk to each other. When the widget needs an answer, it sends a message to the chatbot's API. The API receives the message, processes it, and sends back the answer. Think of it like a restaurant menu — you (the widget) choose what you want from the menu (the API), and the kitchen (the chatbot) prepares it.

### REST API
The most common type of API on the web. Messages are sent using standard web protocols (HTTP/HTTPS). Each piece of information has its own address (called an **endpoint**).

### HTTP / HTTPS
The language that browsers and servers use to communicate. HTTP is the basic version; HTTPS is the encrypted (secure) version. MOSDAC uses HTTPS because it handles sensitive data.

### Endpoint
A specific address (URL) where an API listens for requests. For example, our chatbot has an endpoint `/mosdac/chat` that receives questions and returns answers.

### Docker
A tool that packages a program and everything it needs (Python, libraries, configuration) into a self-contained box called a **container**. This means the chatbot runs the same way on any machine, without requiring you to install Python or other dependencies manually.

### Docker Compose
A tool that starts multiple Docker containers at once from a single file (`docker-compose.yml`). Instead of starting Neo4j, ChromaDB, and the chatbot one by one, you run one command and they all start together.

### Container
A lightweight, isolated box that runs a program. Containers share the host computer's operating system but are otherwise isolated from each other.

### CORS (Cross-Origin Resource Sharing)
A browser security rule that prevents a webpage from making requests to a different domain without permission. For example, a script running on `mosdac.gov.in` is not allowed (by default) to talk to `chatbot.mosdac.gov.in`. CORS is the mechanism by which the chatbot says "it is OK for mosdac.gov.in to talk to me". You configure this in the `.env` file.

### Reverse Proxy
A server that sits in front of your application and forwards incoming requests to the right place. **Nginx** (pronounced "engine-X") is the most popular reverse proxy. We configure Nginx so that any request to `mosdac.gov.in/chatapi/` is forwarded to the chatbot, while all other requests go to Drupal as normal. This solves CORS completely because from the browser's point of view, it is talking to `mosdac.gov.in`, not a different domain.

### Nginx
A fast, free web server and reverse proxy. MOSDAC almost certainly already uses Nginx (or Apache) to serve the Drupal website. We just need to add a few lines to its configuration file.

### SSO (Single Sign-On)
A system that lets a user log in once and be recognised by multiple applications. MOSDAC uses **Keycloak** as its SSO provider. When a user logs in to `mosdac.gov.in`, Keycloak gives Drupal proof of who they are.

### HTTP Header
Extra information attached to an HTTP request, like a label on a parcel. Our integration uses a custom header called `X-MOSDAC-User` to tell the chatbot who is asking the question, so the chatbot can personalise responses.

### Keycloak
An open-source identity management system. It handles login, logout, password resets, and SSO for MOSDAC users.

### Session
A way to remember who a user is across multiple requests. When you send a message, get a reply, and send another message, the chatbot needs to remember the conversation history. It does this by giving you a `session_id` — a unique ID for your conversation.

### Environment Variable
A setting stored outside the code, in the operating system or a file called `.env`. We use these for passwords, server addresses, and toggles. This way the same code works in development (with fake data) and in production (with real servers), just by changing the environment variables.

### Knowledge Base / RAG (Retrieval-Augmented Generation)
Instead of the AI model guessing answers from memory, RAG first searches a database of MOSDAC documents, finds the relevant paragraphs, and passes them to the AI as context. This makes answers much more accurate and specific to MOSDAC.

### ChromaDB
A vector database — a specialised database that stores documents as mathematical representations (vectors) and can find the most relevant ones for a given question. Used for the "fuzzy search" part of the knowledge base.

### Neo4j
A graph database — stores information as a network of connected nodes (like a mind map). Used for the "structured relationships" part of the knowledge base. For example: "Satellite X → carries → Sensor Y → measures → Parameter Z".

### Volume (Docker)
A folder on the host machine that is shared with a container. Data written to a volume survives even if the container is deleted or restarted.

---

## 3. How the Two Systems Talk to Each Other

Here is the full journey of a single chat message:

```
1. User visits mosdac.gov.in
   └── Drupal serves the page HTML

2. Browser loads the page
   └── Finds our <script> tag in the HTML
   └── Downloads widget.js from /chatapi/widget/widget.js (through Nginx)

3. User clicks the chat bubble
   └── widget.js creates a chat panel in the browser

4. User types: "What is the rainfall forecast for Mumbai this week?"
   └── widget.js collects:
       - message: "What is the rainfall forecast for Mumbai this week?"
       - session_id: "abc-123" (a unique ID for this conversation)

5. widget.js sends an HTTP POST request to:
   POST https://www.mosdac.gov.in/chatapi/mosdac/chat
   Headers:
     Content-Type: application/json
     X-MOSDAC-User: user@example.com   ← injected by Nginx from Drupal's logged-in user

6. Nginx receives the request
   └── URL starts with /chatapi/ → forward to http://localhost:8000
   └── Adds X-MOSDAC-User header

7. FastAPI (chat_api/main.py) receives the request
   └── Routes it to mosdac_agent/routes.py

8. MOSDAC agent processes the request
   ├── Extracts user identity from X-MOSDAC-User header
   ├── Searches ChromaDB for relevant document chunks
   ├── Searches Neo4j for related entities
   └── Calls LLM (Ollama or vLLM) with context + question

9. LLM generates the answer

10. FastAPI sends back:
    {
      "answer": "Based on IMD forecast data...",
      "session_id": "abc-123"
    }

11. widget.js displays the answer in the chat panel
```

---

## 4. Before You Start — What You Need

Check all of these before you begin. Each item is explained below.

### 4.1 On the Chatbot Server Machine

```bash
# Check Docker version (need 20.10 or higher)
docker --version

# Check Docker Compose version (need 2.1 or higher)
docker compose version

# Check available disk space (need at least 20 GB free)
df -h /

# Check available RAM (need at least 8 GB)
free -h

# Check if GPU is available (optional, for faster inference)
nvidia-smi
```

### 4.2 On the MOSDAC Web Server

- SSH access (a way to log in to the server via the terminal)
- Permission to edit Nginx configuration files (usually requires `sudo`)
- Permission to edit Drupal theme files or add custom blocks
- The Nginx config directory (usually `/etc/nginx/sites-available/` or `/etc/nginx/conf.d/`)

### 4.3 Information to Collect

Write these down before you start:

| Item | What it is | Where to find it |
|------|-----------|-----------------|
| Chatbot server IP | The internal IP address of the machine running Docker | Run `hostname -I` on that machine |
| Chatbot port | The port Docker exposes (default: 8000) | See `docker-compose.yml` |
| Neo4j password | Password for the graph database | Ask your infrastructure team |
| LLM choice | Ollama (easy) or vLLM (production) | Decide based on GPU availability |
| Drupal theme name | Name of the active Drupal theme | Drupal admin → Appearance |
| Drupal files path | Where Drupal theme files are stored | Usually `/var/www/html/web/themes/` |

---

## 5. Step 1 — Deploy the Chatbot Server

### 5.1 Copy the Project to the Server

```bash
# On the chatbot server machine, clone the project
git clone <your-repository-url> /opt/ai_agents
cd /opt/ai_agents
```

If you received the project as a zip file:
```bash
unzip ai_agents.zip -d /opt/ai_agents
cd /opt/ai_agents
```

### 5.2 Create Your Environment File

The `.env` file is where you store all passwords and settings. **This file must never be committed to git.**

```bash
# Copy the MOSDAC template
cp deployments/mosdac.env .env
```

Now open `.env` in a text editor:

```bash
nano .env
```

You will see something like this. Fill in every line that says `CHANGE_ME`:

```dotenv
# ─── LLM Backend ────────────────────────────────────────────────────────────
# Choose ONE of the three options below.

# Option A: Use Ollama (easier, no GPU required for small models)
LLM_API_BASE=http://ollama:11434/v1
LLM_API_KEY=ollama

# Option B: Use vLLM inside Docker (requires NVIDIA GPU)
# LLM_API_BASE=http://vllm:8000/v1
# LLM_API_KEY=not-needed

# Option C: Use an external OpenAI-compatible API
# LLM_API_BASE=https://api.openai.com/v1
# LLM_API_KEY=sk-your-key-here

# ─── Model Name ──────────────────────────────────────────────────────────────
QWEN_MODEL=qwen2.5:7b

# ─── Neo4j Database ──────────────────────────────────────────────────────────
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=CHANGE_ME_strong_password_here

# ─── ChromaDB ────────────────────────────────────────────────────────────────
CHROMA_HOST=chromadb
CHROMA_PORT=8001

# ─── CORS (which websites are allowed to use this chatbot) ───────────────────
CHAT_API_ALLOWED_ORIGINS=https://www.mosdac.gov.in,https://mosdac.gov.in

# ─── MOSDAC Agent Settings ───────────────────────────────────────────────────
ENABLE_MOSDAC_ENDPOINT=true
MOSDAC_REQUIRE_SSO_HEADER=true
# For testing without SSO (leave empty in production):
# MOSDAC_SSO_DEV_USER=

# ─── Bot Branding ─────────────────────────────────────────────────────────────
CHAT_API_BOT_NAME=MOSDAC Assistant
```

**Important rules for the `.env` file:**
- Lines starting with `#` are comments — they are ignored
- To activate an option, remove the `#` at the start of the line
- To deactivate an option, add `#` at the start
- Never share this file or commit it to git

### 5.3 Choose Your LLM Backend and Start Docker

**Option A: Ollama (recommended for first-time setup)**

Ollama is easier — it downloads and manages model files for you. No GPU required for 7B models with 4-bit quantization, though it will be slow on CPU.

```bash
# Start all services with Ollama
docker compose --profile ollama up -d

# Download the model (do this once — takes 5-10 minutes)
docker compose exec ollama ollama pull qwen2.5:7b

# Watch the logs
docker compose logs -f chat_api
```

**Option B: vLLM (recommended for production with GPU)**

vLLM runs the model directly inside Docker and is much faster. Requires an NVIDIA GPU with at least 16 GB VRAM for a 7B model.

```bash
# Set the model to download (edit .env first)
# VLLM_MODEL=Qwen/Qwen2.5-7B-Instruct

# Start all services with vLLM
docker compose --profile vllm up -d

# Watch startup (first run downloads the model — ~15 GB, takes time)
docker compose logs -f vllm
```

**Option C: External API (no GPU needed, costs money per query)**

Just set `LLM_API_BASE` and `LLM_API_KEY` in `.env` to your provider's values, then start without a profile:

```bash
docker compose up -d
```

### 5.4 Verify All Services Are Running

```bash
docker compose ps
```

You should see something like:

```
NAME          STATUS          PORTS
chat_api      healthy         0.0.0.0:8000->8000/tcp
neo4j         healthy         7474/tcp, 7687/tcp
chromadb      running         8001/tcp
ollama        healthy         11434/tcp
```

All services should show `healthy` or `running`. If any shows `Exit` or `unhealthy`, check its logs:

```bash
docker compose logs <service-name>
```

### 5.5 Test the Chatbot Directly

Before connecting Drupal, verify the chatbot works on its own:

```bash
# Health check
curl http://localhost:8000/health

# Expected response:
# {"status": "ok", "version": "1.0.0"}

# Test a chat message
curl -X POST http://localhost:8000/mosdac/chat \
  -H "Content-Type: application/json" \
  -H "X-MOSDAC-User: testuser@mosdac.gov.in" \
  -d '{"message": "Hello", "session_id": "test-001"}'

# Expected response (exact answer will vary):
# {"answer": "Hello! I am the MOSDAC Assistant...", "session_id": "test-001"}
```

If you get a response, the chatbot is working. Proceed to the next step.

---

## 6. Step 2 — Configure CORS

CORS (Cross-Origin Resource Sharing) is a browser security feature. Without it, a script on `mosdac.gov.in` cannot make requests to your chatbot server, even if the user wants it to.

**You already configured this in Step 5.2.** The relevant line in `.env` is:

```dotenv
CHAT_API_ALLOWED_ORIGINS=https://www.mosdac.gov.in,https://mosdac.gov.in
```

This tells the chatbot: "it is OK for browsers that loaded a page from `mosdac.gov.in` to send me messages."

**If you are setting up on a different portal**, change these values to match your domain.

**Note:** If you set up Nginx as a reverse proxy (Step 3), CORS becomes less critical because both Drupal and the chatbot share the same domain. However, it is still good practice to configure it correctly.

After changing `.env`, restart the chatbot to apply:

```bash
docker compose restart chat_api
```

---

## 7. Step 3 — Set Up Nginx as a Reverse Proxy

This is the most important integration step. We are telling Nginx: "when someone requests a URL starting with `/chatapi/`, forward the request to the chatbot server instead of Drupal."

### Why do we need this?

Without Nginx:
- Drupal is at: `https://www.mosdac.gov.in` (port 443)
- Chatbot is at: `http://chatbot-server:8000` (different machine, different port)
- The browser sees two different origins → CORS errors, blocked requests

With Nginx:
- Both Drupal and chatbot are at: `https://www.mosdac.gov.in`
- Nginx routes requests: `/` → Drupal, `/chatapi/` → chatbot
- The browser sees one origin → no CORS issues

### 7.1 Find the Nginx Configuration File

On most Linux servers, Nginx configuration files are in one of these locations:

```bash
# Check if this file exists:
ls /etc/nginx/sites-available/mosdac.gov.in

# Or:
ls /etc/nginx/conf.d/
```

Open the active configuration file:

```bash
sudo nano /etc/nginx/sites-available/mosdac.gov.in
```

### 7.2 Add the Chatbot Proxy Block

Inside your `server { ... }` block (the one handling HTTPS on port 443), add the following. Add it **before** the main `location /` block:

```nginx
# ─── MOSDAC Chatbot Proxy ─────────────────────────────────────────────────────
# Requests to /chatapi/ are forwarded to the chatbot Docker container.
# Everything else still goes to Drupal.

location /chatapi/ {
    # Forward to chatbot server
    # Replace 127.0.0.1:8000 with the actual IP:port if chatbot is on a
    # different machine (e.g., proxy_pass http://192.168.1.50:8000/;)
    proxy_pass http://127.0.0.1:8000/;

    # These headers tell the chatbot about the original request
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Pass the logged-in Drupal user's email to the chatbot.
    # $drupal_user is set by the sub-block below.
    # If Drupal is not passing user info, you can hardcode a value for testing:
    # proxy_set_header X-MOSDAC-User "anonymous";
    proxy_set_header X-MOSDAC-User     $drupal_user;

    # Allow large requests (for screenshot uploads)
    client_max_body_size 10m;

    # Keep the connection alive for streaming responses
    proxy_buffering    off;
    proxy_read_timeout 120s;
}

# ─── Extract Drupal User from Cookie ─────────────────────────────────────────
# This reads the Drupal session cookie and maps it to a user email.
# For a simpler setup, comment this out and set X-MOSDAC-User manually.
map $cookie_drupal_session $drupal_user {
    default "anonymous";
    # When Keycloak/SSO is integrated, replace this with the actual header name
    # that Keycloak sets. See Step 6 for the full SSO setup.
}
```

**Line-by-line explanation:**

| Line | What it does |
|------|-------------|
| `location /chatapi/ { }` | "For any URL starting with `/chatapi/`, use these rules" |
| `proxy_pass http://127.0.0.1:8000/;` | Forward the request to the chatbot on port 8000. The trailing `/` strips the `/chatapi` prefix before forwarding. |
| `proxy_set_header Host $host;` | Tell the chatbot what domain was requested |
| `proxy_set_header X-Real-IP $remote_addr;` | Tell the chatbot the user's real IP address |
| `proxy_set_header X-MOSDAC-User $drupal_user;` | Pass the logged-in user's identity |
| `client_max_body_size 10m;` | Allow uploads up to 10 MB (for screenshot feature) |
| `proxy_buffering off;` | Send responses immediately, don't buffer them |
| `proxy_read_timeout 120s;` | Wait up to 2 minutes for the chatbot to respond |

### 7.3 Test and Reload Nginx

```bash
# Test the configuration for syntax errors
sudo nginx -t

# If it says "test is successful", reload Nginx
sudo systemctl reload nginx
```

### 7.4 Verify the Proxy Works

From any machine, test that the proxy route works:

```bash
curl https://www.mosdac.gov.in/chatapi/health

# Expected:
# {"status": "ok", "version": "1.0.0"}
```

If this returns the health response, Nginx is correctly forwarding to the chatbot.

---

## 8. Step 4 — Add the Widget to Drupal

The widget is a floating chat button that appears on every page of the MOSDAC website. It is loaded by adding two `<script>` tags to the Drupal theme.

### What the two script tags do

```html
<!-- Script 1: html2canvas — lets users share screenshots with the chatbot -->
<script src="/chatapi/static/html2canvas.min.js"></script>

<!-- Script 2: The chat widget itself -->
<script
  src="/chatapi/widget/widget.js"
  data-api-base="/chatapi"
  data-bot-name="MOSDAC Assistant"
></script>
```

**`data-api-base`** — tells the widget where the chatbot is. Since we set up Nginx, this is just `/chatapi` (relative to the current domain).

**`data-bot-name`** — the name displayed in the chat panel header.

### Method 1: Edit the Drupal Theme Template (Recommended)

This method adds the widget to every page automatically.

**Step 1:** Find your Drupal theme directory:

```bash
# Typical location:
ls /var/www/html/web/themes/custom/

# Or check the Drupal admin panel:
# Go to: Appearance → (your active theme) → Settings
```

**Step 2:** Find the base HTML template:

```bash
find /var/www/html/web/themes -name "html.html.twig"
```

**Step 3:** Open the file and find the closing `</body>` tag:

```bash
sudo nano /var/www/html/web/themes/custom/mosdac_theme/templates/html.html.twig
```

Look for a line that says `</body>`. Add the script tags just before it:

```twig
{# ... existing theme code ... #}

{# ── MOSDAC Chatbot Widget ─────────────────────────────────────────── #}
{% if user.isAuthenticated() %}
  {# Only show the widget to logged-in users #}
  <script src="/chatapi/static/html2canvas.min.js"></script>
  <script
    src="/chatapi/widget/widget.js"
    data-api-base="/chatapi"
    data-bot-name="MOSDAC Assistant"
    data-user-email="{{ user.mail }}"
  ></script>
{% endif %}
{# ── End Chatbot Widget ─────────────────────────────────────────────── #}

</body>
</html>
```

**What is `user.mail`?** In Drupal's Twig template language, `user` is an object representing the currently logged-in user. `user.mail` is their email address. This is passed to the widget as `data-user-email`, which the widget can use to identify the user.

**Step 4:** Clear Drupal's cache so the change takes effect:

```bash
cd /var/www/html
vendor/bin/drush cache:rebuild
```

Or from the Drupal admin panel: **Configuration → Performance → Clear all caches**.

### Method 2: Use a Drupal Custom Block (No Code Required)

This method uses Drupal's admin interface — no file editing needed. However, it may not appear on all page types.

1. Log in to Drupal admin (`/admin`)
2. Go to **Structure → Block layout**
3. Click **+ Place block** in the "Page bottom" region
4. Click **+ Add custom block**
5. Set:
   - **Block title**: MOSDAC Chatbot (check "Display title: No")
   - **Body**: Switch the editor to "Source" mode and paste:
     ```html
     <script src="/chatapi/static/html2canvas.min.js"></script>
     <script src="/chatapi/widget/widget.js" data-api-base="/chatapi" data-bot-name="MOSDAC Assistant"></script>
     ```
6. Save and configure the block to appear on all pages

### Method 3: Nginx `sub_filter` (No Drupal Access Needed)

If you cannot edit Drupal files at all, you can use Nginx to inject the script tags into every HTML response. Add this inside the `/chatapi/` location or in a separate server block:

```nginx
location / {
    # ... existing Drupal proxy settings ...

    # Inject chatbot widget before </body>
    sub_filter '</body>' '<script src="/chatapi/static/html2canvas.min.js"></script><script src="/chatapi/widget/widget.js" data-api-base="/chatapi" data-bot-name="MOSDAC Assistant"></script></body>';
    sub_filter_once on;
    sub_filter_types text/html;
}
```

Then reload Nginx: `sudo systemctl reload nginx`

---

## 9. Step 5 — Enable the MOSDAC Agent

The MOSDAC agent is a specialised version of the chatbot that knows about MOSDAC data, satellites, and products. It is enabled via environment variables.

In your `.env` file, ensure these lines are set:

```dotenv
# Enable the MOSDAC-specific endpoint (/mosdac/chat)
ENABLE_MOSDAC_ENDPOINT=true

# In production, require users to be identified via SSO
MOSDAC_REQUIRE_SSO_HEADER=true

# For development/testing, allow anonymous access (comment out in production)
# MOSDAC_SSO_DEV_USER=dev_tester@mosdac.gov.in
```

After changing `.env`:

```bash
docker compose restart chat_api
```

Verify the MOSDAC endpoint is active:

```bash
curl https://www.mosdac.gov.in/chatapi/mosdac/health

# Expected:
# {"status": "ok", "agent": "mosdac"}
```

---

## 10. Step 6 — Connect Drupal User Identity

When a user is logged in to the MOSDAC portal, the chatbot should know who they are. This allows the chatbot to personalise responses ("Based on your previous queries, you seem interested in Cyclone data...") and provides an audit trail.

### How identity flows

```
Drupal user logs in → Keycloak validates identity
→ Drupal stores user email in session
→ Nginx reads the session → sets X-MOSDAC-User header
→ FastAPI reads the header → passes to MOSDAC agent
```

### 10.1 Method A: Via Drupal Twig Template (Simplest)

We already did part of this in Step 8. The `data-user-email="{{ user.mail }}"` attribute on the script tag passes the user's email to the widget JavaScript.

The widget then sends it as a query parameter or in the request body. Update your Twig template:

```twig
<script
  src="/chatapi/widget/widget.js"
  data-api-base="/chatapi"
  data-bot-name="MOSDAC Assistant"
  data-user-email="{{ user.isAuthenticated() ? user.mail : 'anonymous' }}"
></script>
```

In `widget.js`, this value is read and sent with every chat request.

### 10.2 Method B: Via Nginx (More Secure)

If MOSDAC uses Keycloak and Nginx with the `ngx_http_auth_request_module`, Nginx can validate the user's token and set the header before forwarding:

```nginx
location /chatapi/ {
    # ... other proxy settings ...

    # Extract user email from Keycloak token (requires auth_request setup)
    # See your Keycloak+Nginx documentation for the exact variable name
    proxy_set_header X-MOSDAC-User $keycloak_user_email;
}
```

The exact configuration depends on your Keycloak setup. Ask your infrastructure team for the Nginx variable that contains the authenticated user's email.

### 10.3 Testing User Identity

```bash
# Test with a user email in the header
curl -X POST https://www.mosdac.gov.in/chatapi/mosdac/chat \
  -H "Content-Type: application/json" \
  -H "X-MOSDAC-User: testuser@mosdac.gov.in" \
  -d '{"message": "What is INSAT-3D?", "session_id": "test-001"}'
```

The chatbot logs will show: `User: testuser@mosdac.gov.in asked: What is INSAT-3D?`

---

## 11. Step 7 — Ingest MOSDAC Documents into the Knowledge Base

The chatbot's knowledge comes from documents you provide. Without ingestion, the chatbot can only give generic answers based on the LLM's training data.

### What is ingestion?

Ingestion means:
1. Reading a PDF, Word document, or web page
2. Splitting it into small chunks (paragraphs or sections)
3. Converting each chunk into a vector (mathematical representation)
4. Storing the chunks and vectors in ChromaDB and Neo4j

### 11.1 Prepare Your Documents

Create a folder for MOSDAC documents:

```bash
mkdir -p /opt/ai_agents/data/mosdac_docs
```

Copy your documents into this folder:

```bash
cp /path/to/mosdac_products_guide.pdf /opt/ai_agents/data/mosdac_docs/
cp /path/to/satellite_specifications.pdf /opt/ai_agents/data/mosdac_docs/
cp /path/to/data_access_guide.pdf /opt/ai_agents/data/mosdac_docs/
```

Supported formats: PDF, DOCX, TXT, Markdown

### 11.2 Run the Ingestion Script

```bash
cd /opt/ai_agents

# Ingest all documents in the folder
docker compose exec chat_api python -m mosdac_agent.ingest \
  --input-dir /app/data/mosdac_docs \
  --collection mosdac

# Watch the progress
# You will see: "Processing file 1/10: satellite_specifications.pdf"
# "  Chunk 1/45 indexed..."
```

### 11.3 Verify Ingestion

```bash
# Ask the chatbot a question that should be in the documents
curl -X POST http://localhost:8000/mosdac/chat \
  -H "Content-Type: application/json" \
  -H "X-MOSDAC-User: test@mosdac.gov.in" \
  -d '{"message": "What satellites does MOSDAC operate?", "session_id": "test-ingest"}'
```

If the answer contains specific details from your documents (satellite names, product codes, etc.), ingestion worked.

### 11.4 Re-ingesting After Document Updates

When you add new documents or update existing ones:

```bash
# Re-run the ingestion script — it will update the knowledge base
docker compose exec chat_api python -m mosdac_agent.ingest \
  --input-dir /app/data/mosdac_docs \
  --collection mosdac \
  --update
```

---

## 12. Step 8 — Customise Widget Appearance

The chat widget uses a dark blue theme by default (`#002b5c`), matching MOSDAC's branding. You can customise it further.

### 12.1 Available Configuration Options

Pass these as `data-` attributes on the script tag:

```html
<script
  src="/chatapi/widget/widget.js"
  data-api-base="/chatapi"
  data-bot-name="MOSDAC Assistant"
  data-primary-color="#002b5c"
  data-secondary-color="#ffffff"
  data-position="bottom-right"
  data-placeholder="Ask me about MOSDAC data..."
  data-welcome-message="Hello! I can help you find weather data, satellite products, and more."
></script>
```

### 12.2 CSS Customisation

If you need deeper customisation, add CSS overrides in your Drupal theme:

```css
/* Override chat widget colors */
.mosdac-chat-widget {
  --widget-primary: #002b5c;     /* Dark blue — MOSDAC brand */
  --widget-accent:  #f39c12;     /* Orange for buttons */
  --widget-font:    'Roboto', sans-serif;
}
```

### 12.3 Changing the Bot Name Only (via Environment Variable)

The bot name can also be set server-side in `.env`:

```dotenv
CHAT_API_BOT_NAME=MOSDAC Assistant
```

This value is returned by the `/config` endpoint and picked up by the widget automatically.

---

## 13. Step 9 — Test Everything (7-Step Checklist)

Work through this checklist in order. Each step builds on the previous one.

### ✅ Test 1: Chatbot Health

```bash
curl https://www.mosdac.gov.in/chatapi/health
# Expected: {"status": "ok", "version": "1.0.0"}
```

**If this fails:** Nginx proxy is not working. Recheck Step 3.

### ✅ Test 2: Widget Loads

1. Open `https://www.mosdac.gov.in` in a browser
2. Open the browser developer tools (press F12)
3. Go to the **Network** tab
4. Reload the page
5. Search for `widget.js` in the network requests

**If widget.js does not appear:** The script tag was not added to Drupal. Recheck Step 4.

### ✅ Test 3: Chat Panel Opens

1. Look for the chat bubble in the bottom-right corner of the MOSDAC website
2. Click it

**If the bubble does not appear:** Open the browser console (F12 → Console tab) and look for JavaScript errors. Common errors:
- `Failed to fetch` → chatbot server is down
- `CORS error` → CORS is misconfigured (Step 2)
- `widget.js not found` → Nginx not forwarding `/chatapi/` (Step 3)

### ✅ Test 4: Basic Chat Works

1. Type "Hello" in the chat panel
2. Press Enter

**If no response appears:** Check `docker compose logs chat_api` for errors.

### ✅ Test 5: Knowledge Base Works

1. Type a question that should be in your documents, e.g., "What is INSAT-3D?"
2. The answer should contain specific information from your ingested documents

**If the answer is generic:** Documents were not ingested. Redo Step 7.

### ✅ Test 6: User Identity Works

1. Log in to the MOSDAC portal
2. Open the chat panel
3. Ask: "Who am I?"

Check the chatbot logs:

```bash
docker compose logs chat_api | grep "User:"
```

You should see the logged-in user's email.

**If you see "anonymous":** User identity is not being passed. Recheck Step 6.

### ✅ Test 7: Screenshot Feature Works

1. Open the chat panel
2. Look for a camera/screenshot button
3. Click it — it should capture the current page and attach it to your message

**If the button is missing:** `html2canvas.min.js` did not load. Check the network tab for the script.

---

## 14. How All the Files Work Together

This section shows every file involved in the integration and what it does.

```
d:\AI_agents\
│
├── docker-compose.yml         ← Starts all Docker services
│                                (Neo4j, ChromaDB, chat_api, ollama/vllm)
│
├── .env                       ← Your secrets and settings (never commit this)
│
├── deployments/
│   ├── mosdac.env             ← Template for MOSDAC settings
│   └── README.md              ← Deployment documentation
│
├── chat_api/                  ← The FastAPI web server
│   ├── main.py                ← App factory; mounts routers
│   ├── config.py              ← Reads CHAT_API_* environment variables
│   ├── routes.py              ← /health, /config, /chat endpoints
│   ├── service.py             ← Processes chat messages, calls LLM
│   ├── session.py             ← Stores conversation history
│   └── models.py              ← Request/response data shapes
│
├── mosdac_agent/              ← The MOSDAC-specific agent
│   ├── config.py              ← MOSDAC settings (SSO, route prefix)
│   ├── routes.py              ← /mosdac/chat endpoint; reads X-MOSDAC-User
│   ├── agent.py               ← Orchestrates knowledge retrieval + LLM call
│   ├── tools.py               ← ChromaDB + Neo4j search functions
│   ├── catalog.py             ← MOSDAC product catalogue
│   └── widget/
│       ├── widget.html        ← Chat panel HTML structure
│       ├── widget.css         ← Chat panel styling
│       └── widget.js          ← Chat widget logic (loaded by browser)
│
└── Dockerfile.api             ← How to build the chat_api Docker image
```

**Request flow through the files:**

```
Browser widget.js
  └── POST /chatapi/mosdac/chat
        └── Nginx forwards to port 8000
              └── chat_api/main.py (receives request)
                    └── mosdac_agent/routes.py (handles /mosdac/chat)
                          └── reads X-MOSDAC-User header
                          └── mosdac_agent/agent.py (processes query)
                                └── mosdac_agent/tools.py (searches DB)
                                └── calls LLM
                          └── returns {"answer": "...", "session_id": "..."}
              └── chat_api/main.py sends response back
        └── Nginx forwards response to browser
  └── widget.js displays the answer
```

---

## 15. Updating and Maintaining After Launch

### Updating the Chatbot Code

```bash
cd /opt/ai_agents

# Pull the latest code
git pull

# Rebuild and restart the containers
docker compose build chat_api
docker compose up -d chat_api

# Check the new version is running
curl http://localhost:8000/health
```

### Adding New Documents to the Knowledge Base

```bash
# Copy new documents to the data folder
cp /path/to/new_document.pdf /opt/ai_agents/data/mosdac_docs/

# Re-ingest
docker compose exec chat_api python -m mosdac_agent.ingest \
  --input-dir /app/data/mosdac_docs \
  --collection mosdac \
  --update
```

### Monitoring

```bash
# Watch live logs
docker compose logs -f

# Check disk usage (knowledge bases can grow large)
docker system df

# Check container resource usage
docker stats
```

### Backups

Back up these items regularly:

```bash
# Neo4j data
docker compose exec neo4j neo4j-admin database dump neo4j \
  --to-path=/data/backups

# ChromaDB data (it's just files)
cp -r /var/lib/docker/volumes/ai_agents_chroma_data/_data /backup/chroma_$(date +%Y%m%d)

# Your .env file (store in a secrets manager, not git)
cp .env /backup/.env.$(date +%Y%m%d)
```

---

## 16. Troubleshooting — 6 Common Problems

### Problem 1: "CORS error" in the browser console

**Symptom:** The chat panel opens but messages fail with a CORS error.

**Cause:** The chatbot is not allowing requests from `mosdac.gov.in`.

**Fix:**
1. Open `.env`
2. Find `CHAT_API_ALLOWED_ORIGINS`
3. Ensure it includes your exact domain (with and without `www`):
   ```dotenv
   CHAT_API_ALLOWED_ORIGINS=https://www.mosdac.gov.in,https://mosdac.gov.in
   ```
4. Restart: `docker compose restart chat_api`
5. If using Nginx proxy (Step 3), CORS should not be an issue — verify Nginx is running correctly.

---

### Problem 2: Widget does not appear on the page

**Symptom:** The chat bubble is missing from the MOSDAC website.

**Possible causes and fixes:**

| Cause | Fix |
|-------|-----|
| Script tag not added | Recheck Step 4; clear Drupal cache after adding |
| JavaScript error | Open browser console (F12), look for errors |
| Widget.js not found (404) | Verify Nginx is forwarding `/chatapi/` (Step 3) |
| User not logged in | Check if widget is set to only show for authenticated users |

---

### Problem 3: Chatbot returns generic answers, not MOSDAC-specific

**Symptom:** Chatbot answers questions but the answers are vague and don't mention MOSDAC products.

**Cause:** Documents have not been ingested into the knowledge base.

**Fix:** Follow Step 7 (Ingest MOSDAC Documents).

---

### Problem 4: "502 Bad Gateway" when accessing `/chatapi/`

**Symptom:** Opening `https://www.mosdac.gov.in/chatapi/health` returns a 502 error.

**Cause:** Nginx cannot reach the chatbot server on port 8000.

**Fix:**
1. Check the chatbot is running: `docker compose ps`
2. Verify the correct IP/port in Nginx config:
   ```nginx
   proxy_pass http://127.0.0.1:8000/;
   ```
   If the chatbot is on a different machine, replace `127.0.0.1` with its IP.
3. Check firewall rules: `sudo ufw status` or `sudo firewall-cmd --list-all`

---

### Problem 5: "X-MOSDAC-User header is required" error

**Symptom:** Chat requests fail with an error saying the user header is required.

**Cause:** `MOSDAC_REQUIRE_SSO_HEADER=true` but the header is not being sent.

**Fix (for development):** Add a development user fallback in `.env`:
```dotenv
MOSDAC_SSO_DEV_USER=dev@mosdac.gov.in
MOSDAC_REQUIRE_SSO_HEADER=false
```

**Fix (for production):** Ensure Nginx is setting the `X-MOSDAC-User` header (Step 6).

---

### Problem 6: Chatbot is very slow to respond

**Symptom:** Responses take 30–60+ seconds.

**Possible causes and fixes:**

| Cause | Fix |
|-------|-----|
| Running on CPU, no GPU | Add a GPU; or use a smaller model (e.g., `qwen2.5:3b`) |
| LLM model not loaded | Check `docker compose logs ollama` — model may still be downloading |
| Neo4j slow queries | Check Neo4j memory settings; add more RAM |
| Chatbot server under load | Add more CPU/RAM to the Docker host |

Check which part is slow:

```bash
# Time the health check (should be < 50ms)
time curl http://localhost:8000/health

# Time a chat request (measures full pipeline)
time curl -X POST http://localhost:8000/mosdac/chat \
  -H "Content-Type: application/json" \
  -H "X-MOSDAC-User: test@example.com" \
  -d '{"message": "Hello", "session_id": "test"}'
```

---

## 17. Quick Command Reference

```bash
# ─── Starting and Stopping ────────────────────────────────────────────────────

# Start all services (use Ollama LLM)
docker compose --profile ollama up -d

# Start all services (use vLLM — requires GPU)
docker compose --profile vllm up -d

# Start all services (use external API — no LLM in Docker)
docker compose up -d

# Stop all services
docker compose down

# Stop and delete all data (dangerous — use only to start fresh)
docker compose down -v

# ─── Checking Status ──────────────────────────────────────────────────────────

# Check all container statuses
docker compose ps

# Watch live logs from all containers
docker compose logs -f

# Watch logs from one container
docker compose logs -f chat_api

# ─── Testing ──────────────────────────────────────────────────────────────────

# Test chatbot health
curl http://localhost:8000/health

# Test chat (local)
curl -X POST http://localhost:8000/mosdac/chat \
  -H "Content-Type: application/json" \
  -H "X-MOSDAC-User: test@mosdac.gov.in" \
  -d '{"message": "What is MOSDAC?", "session_id": "test-001"}'

# Test through Nginx (production path)
curl https://www.mosdac.gov.in/chatapi/health

# ─── Knowledge Base ───────────────────────────────────────────────────────────

# Ingest documents
docker compose exec chat_api python -m mosdac_agent.ingest \
  --input-dir /app/data/mosdac_docs --collection mosdac

# ─── Ollama Model Management ──────────────────────────────────────────────────

# Download a model
docker compose exec ollama ollama pull qwen2.5:7b

# List downloaded models
docker compose exec ollama ollama list

# ─── Nginx ────────────────────────────────────────────────────────────────────

# Test Nginx configuration
sudo nginx -t

# Reload Nginx (apply config changes without downtime)
sudo systemctl reload nginx

# ─── Drupal ───────────────────────────────────────────────────────────────────

# Clear Drupal cache
vendor/bin/drush cache:rebuild

# ─── Maintenance ──────────────────────────────────────────────────────────────

# Check disk usage
docker system df

# Remove unused Docker images and containers
docker system prune

# Rebuild and restart the chatbot after a code update
docker compose build chat_api && docker compose up -d chat_api
```

---

*End of integration guide.*

*If you encounter a problem not covered here, check `docker compose logs chat_api` first — it usually contains a clear error message describing what went wrong.*
