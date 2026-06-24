# `docker_samjho.md` — Is project mein Docker ko zero se samajh (Hinglish)

> Bhai, yeh file tere project ke Docker setup ke baare mein hai —
> [Dockerfile.api](Dockerfile.api), [docker-compose.yml](docker-compose.yml),
> [docker-entrypoint.sh](docker-entrypoint.sh), aur [.dockerignore](.dockerignore). Maan ke
> chal raha hoon tujhe Docker ka **D bhi nahi** pata. End tak samajh jayega: Docker kya hai,
> har file kya karti hai, **volume mapping se bina rebuild kiye real-time edit** kaise karein,
> aur is project mein Docker ka **faayda** kya hai.

---

## Part 0 — Docker kya hai? (kahani se, 5 minute)

Soch tune ek **kaam ka software** banaya (tera chat_api). Woh teri machine pe to chalta hai,
par jab doosri machine (ISRO ka server) pe le jaata hai, to **"mere yahan to chal raha tha!"**
wala problem aata hai — Python version alag, koi library missing, Tesseract install nahi,
OS alag. Yeh "works on my machine" ki sabse badi bimari hai.

**Docker iska ilaaj hai.** Docker tere app ko ek **"dabba" (container)** mein band kar deta
hai — usme app + Python + saari libraries + system tools (Tesseract, Poppler) + settings,
**sab kuch ek saath** packed. Yeh dabba **kisi bhi machine** pe bilkul same chalega, kyunki
woh apna poora environment khud leke chalta hai.

Restaurant ki analogy:
- **Dockerfile** = **recipe** (kaise dabba banana hai — step by step).
- **Image** = banaya hua **frozen ready-meal** (recipe se ek baar banta hai, dobara use hota hai).
- **Container** = us meal ko **garam karke plate mein** (image se chalta hua live app).
- **Volume** = **tiffin/dabba jo bahar se laaya** — container ke andar mounted, par data
  bahar (host pe) safe rehta hai.
- **docker-compose.yml** = poore **thali ka menu** — ek saath kai dishes (neo4j + redis +
  chat_api) ko order karna aur unhe aapas mein jodna.

---

## Part 1 — 3 core concepts (yeh 3 pakke kar le)

### 1. Image vs Container
- **Image** = ek **template / blueprint**. Read-only. Ek baar `build` hoti hai.
- **Container** = us image ka **running instance**. Ek image se **kai** containers chala
  sakte ho. Container delete karo to uska andar ka data jaata hai (jab tak volume na ho).

> 🧠 Image = "class", Container = "object" (agar programming aati hai). Ya: Image = "PDF",
> Container = "us PDF ka khula hua, edit-hote hua copy".

### 2. Build-time vs Run-time (SABSE zaroori for real-time editing)
- **Build-time** = jab `docker build` chalta hai. Yahan `COPY . .` se tera code **image ke
  andar permanently chhap** jaata hai. Isse badalne ke liye **rebuild** chahiye.
- **Run-time** = jab container chalta hai. Yahan **volume mount** se host ka folder container
  ke andar **live dikhta hai** — koi rebuild nahi, edit turant dikhega.

> Yahi tere sawaal ka jawab hai: **"baar-baar build na karna pade"** = jo cheez tu baar-baar
> badalta hai, usse **volume mount** kar do, `COPY` pe mat chhodo. (Part 7 mein detail.)

### 3. Volume (data ka ghar)
Container delete ho jaye to bhi data bachana ho (chat history, vector DB, graph) → **volume**.
Do tarah:
- **Bind mount** (`./chroma_db:/app/chroma_db`) — host ka **asli folder** container mein. Tu
  host pe file dekh/edit kar sakta hai.
- **Named volume** (`conv_data:/app/data`) — Docker apni jagah manage karta hai (host pe
  seedha folder nahi dikhta, par data persist karta hai).

---

## Part 2 — Is project ka architecture (kaun kahan chalta hai)

Yeh **important** hai — sab kuch container mein **nahi** hai:

```
┌─────────────────── Docker Compose network ───────────────────┐
│                                                              │
│   [neo4j]  ←──── bolt://neo4j:7687 ────┐                      │
│   container                            │                      │
│                                        │                      │
│   [redis]  ←── redis://...@redis:6379 ─┤                      │
│   container                            │                      │
│                                  [chat_api]  ← port 8000      │
│                                  container (FastAPI)          │
│                                        │                      │
└────────────────────────────────────────┼─────────────────────┘
                                          │  host.docker.internal
                  ┌───────────────────────┴───────────────────┐
                  ▼                                            ▼
          [Tabby LLM]  host :8080                    [Ollama bge-large] host :11434
          (alag, host pe)                            (alag, host pe)
```

| Cheez | Kahan chalta hai | Kyun |
|---|---|---|
| **chat_api** (FastAPI) | Container (compose) | Tera app — Dockerfile.api se banta hai |
| **Neo4j** (graph) | Container (compose) | Standard image, easy to run |
| **Redis** (sessions) | Container (compose) | Standard image |
| **Tabby** (LLM) | **Host pe alag** (container nahi) | GPU/bada model — compose se nahi, separately |
| **Ollama** (embeddings) | **Host pe alag** | Host port 11434 pe |

> ⚠️ Isliye container ke andar `localhost` se kaam nahi chalega — `localhost` matlab
> "container khud", host nahi. Tabby/Ollama tak pahunchne ke liye `host.docker.internal`
> use hota hai (Part 5 mein).

---

## Part 3 — `Dockerfile.api` line-by-line (recipe)

Yeh file batati hai chat_api ka dabba kaise banega. Tukdo mein samajh:

```dockerfile
FROM python:3.11-slim          # base: chhota Python 3.11 wala Linux
WORKDIR /app                   # andar /app folder mein kaam karenge
```

**System tools install** (Python libraries nahi, OS-level binaries):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng libtesseract-dev libleptonica-dev \  # OCR (scanned PDF)
    poppler-utils \      # PDF → image
    libgl1 libglib2.0-0 libgomp1 \   # Docling/ML libs ke liye
    gosu \               # root se appuser pe switch karne ke liye (entrypoint)
    && rm -rf /var/lib/apt/lists/*   # cache saaf → image chhoti
```

**ENV settings:**
```dockerfile
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata   # Tesseract ko data kahan milega
ENV OMP_NUM_THREADS=4    # CPU threads bound karo (GPU nahi hai is deploy mein)
```

**Python dependencies (yahan ek SMART trick hai — layer caching):**
```dockerfile
COPY requirement.txt .              # PEHLE sirf requirements file copy
RUN pip install --no-cache-dir -r requirement.txt fastapi "uvicorn[standard]" \
 && python -m spacy download en_core_web_sm   # NLP model
```
> 💡 **`requirement.txt` ko alag, pehle copy kyun?** Docker **layers cache** karta hai. Agar
> tu sirf apna Python code badalta hai (requirements nahi), to yeh **bhaari pip install
> layer dobara nahi chalti** — cache se use hoti hai. Agar `COPY . .` pehle hota, to har
> chhote code change pe saari libraries dobara install hoti (bahut slow). **Yeh build ko
> tez rakhne ka core trick hai.**

**Docling ML models build-time pe download (air-gapped readiness):**
```dockerfile
ENV HF_HOME=/app/.cache/huggingface
ENV DOCLING_ARTIFACTS_PATH=/app/.cache/docling-models
RUN docling-tools models download --output-dir /app/.cache/docling-models \
 && test -n "$(ls -A /app/.cache/docling-models 2>/dev/null)" \   # check models aaye
 && python -c "from docling.document_converter import DocumentConverter; print('OK')"
```
> 🛰️ **Yeh khaas hai (ISRO offline deploy):** image ban-ne ke **baad internet nahi** hoga.
> Docling normally apne models HuggingFace se first-parse pe download karta hai. Toh hum
> **build ke time hi sab models download** karke local disk pe rakh dete hain, aur Docling
> ko `DOCLING_ARTIFACTS_PATH` se local path bata dete hain — runtime pe **zero network call**.

**Internet band karo (offline lock):**
```dockerfile
ENV HF_HUB_OFFLINE=1            # ab koi HuggingFace lookup local cache se hi
ENV TRANSFORMERS_OFFLINE=1
ENV HF_HUB_DISABLE_TELEMETRY=1
```
> Note: yeh **download ke BAAD** set hota hai — taaki download khud network use kar sake,
> phir lock lag jaye.

**Ab poora code copy:**
```dockerfile
COPY . .     # saara project image mein  (.dockerignore ke hisaab se — Part 4)
```

**Build-time offline verification (fail fast):**
```dockerfile
RUN python -c "...build the Docling converters offline..."
```
> Agar koi model missing hua, to **build yahin fail** hoga — runtime pe field mein parse-error
> aane se behtar.

**Non-root user (security hardening H3):**
```dockerfile
RUN useradd -m -u 10001 appuser \
 && mkdir -p /app/data \
 && chown -R appuser:appuser /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENV CHAT_API_SQLITE_PATH=/app/data/conversations.db
```
> 🔒 App **root se nahi**, `appuser` (UID 10001) se chalta hai. Agar kabhi koi hacker app
> ke andar ghus bhi gaya (RCE), to woh **bina privilege** ke hoga — root nahi. Par container
> **shuru root se** hota hai (sirf mounts chown karne ke liye), phir `appuser` pe drop kar
> deta hai (yeh entrypoint karta hai — Part 6).

**Container kaise start hoga:**
```dockerfile
ENTRYPOINT ["docker-entrypoint.sh"]    # pehle yeh chalega (chown + drop privilege)
CMD ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "65"]
```
> ENTRYPOINT = "hamesha yeh wrapper chalao". CMD = "default command jo wrapper ko diya
> jata hai". Yaani: entrypoint mounts theek karke `uvicorn ...` ko `appuser` ke roop mein
> chalu kar deta hai.

---

## Part 4 — `.dockerignore` (kya, kyun, bina iske kya tootega)

### Yeh kya hai?
`.dockerignore` Docker ko batata hai **`COPY . .` ke waqt kaunse files/folders chhod do**.

> ⚠️ **Bahut important galatfahmi:** `COPY . .` `.gitignore` ko **bilkul nahi dekhta** — woh
> **sirf `.dockerignore`** maanta hai. Yaani agar `.env` `.gitignore` mein hai par
> `.dockerignore` mein nahi, to woh **image mein chhap jayega** (secret leak!).

### Kya-kya ignore kar rahe hain aur KYUN:

| Ignore | Kyun |
|---|---|
| `.git`, `.gitignore` | Version history image mein bekaar — size badhata hai |
| `.env`, `.env.*` (par `!.env.example` rakho) | **Secrets!** Passwords, tokens. Image layer mein chhap gaye to `docker history` se nikaale ja sakte hain |
| `venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/` | Local Python junk — image apni libraries khud install karta hai |
| `conversations.db*`, `ingest_manifest.json`, `drupal_ingestion_state.json` | **Runtime state + user PII** — yeh chalte waqt banta hai, image mein nahi hona chahiye |
| `chroma_db/`, `neo4j_data/`, `downloads/`, `atlases_pdfs/`, `eval_runs/`, `models_cache/` | **Sau-sau MB ka data** — yeh **runtime pe volume se mount** hota hai, image mein nahi |
| `tests/`, `docs/`, `*.md` (par `!README.md`) | Runtime image ko test/docs ki zaroorat nahi |
| `.vscode/`, `.idea/`, `.claude/` | IDE settings — bekaar |

### "Bina iske sab kaise kaam karega?" — yeh confusion clear kar:
Tujhe lagega "data ignore kar diya to app ko data kaise milega?" Jawab:

> Yeh files **image mein nahi**, par **runtime pe volume se mount** ho jaati hain. Jaise
> `chroma_db/` image se ignore hai, par compose mein `- ./chroma_db:/app/chroma_db` se woh
> live mount ho jaata hai. **Image patli rehti hai (sirf code + libraries), data bahar se
> aata hai.** Best of both.

Iske 3 bade faayde:
1. **Security** — secrets/PII image mein nahi.
2. **Size** — image MB mein, GB mein nahi (data baahar).
3. **Speed** — kam files copy = tez build.

---

## Part 5 — `docker-compose.yml` (poore stack ka conductor)

Yeh file **3 services** ko ek saath chalati + jodti hai. `docker compose up` ek hi command
se sab khada ho jata hai.

### Service 1: `neo4j` (knowledge graph)
```yaml
neo4j:
  image: neo4j:5.18.0
  ports:
    - "127.0.0.1:7474:7474"     # sirf loopback — network se reachable NAHI (security B2)
    - "127.0.0.1:7687:7687"
  environment:
    NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD:-please-change-me}"   # .env se password
  volumes:
    - ./neo4j_data:/data        # BIND mount — graph container recreate hone pe bhi bacha rehta
    - neo4j_logs:/logs
  healthcheck: ...              # ready hai ya nahi, baar-baar check
```
> `127.0.0.1:7474:7474` ka matlab: port sirf **host ki apni machine** se khulta hai, baahar
> LAN se nahi. chat_api ise compose-network ke andar `bolt://neo4j:7687` se reach karta hai
> (service-name se).

### Service 2: `redis` (sessions, persistent)
```yaml
redis:
  image: redis:7-alpine
  command: [..., "--requirepass", "${REDIS_PASSWORD:-mosdac-redis}"]   # password (L6)
  volumes:
    - redis_data:/data          # named volume
  # NOTE: koi `ports:` nahi — Redis sirf andar-andar (internal-only), host pe expose nahi
```

### Service 3: `chat_api` (tera FastAPI app)
```yaml
chat_api:
  build:
    context: .
    dockerfile: Dockerfile.api  # is image ko Dockerfile.api se banao
  ports:
    - "8000:8000"               # host:8000 → container:8000 (yahan tera API milta hai)
  env_file:
    - .env                      # saari settings + creds .env se
  environment:
    NEO4J_URI: bolt://neo4j:7687                                  # service-name se neo4j
    DOWNLOADS_DIR: /app/downloads                                 # mount target pin
    ATLASES_DIR: /app/atlases
    TABBY_BASE_URL: ${TABBY_BASE_URL_DOCKER:-http://host.docker.internal:8080/v1}   # HOST Tabby
    OLLAMA_BASE_URL: ${OLLAMA_BASE_URL_DOCKER:-http://host.docker.internal:11434}   # HOST Ollama
    CHAT_API_SESSION_BACKEND: redis
    CHAT_API_REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
    CHAT_API_SQLITE_PATH: /app/data/conversations.db
  extra_hosts:
    - "host.docker.internal:host-gateway"   # container ko host tak pahunchne deta hai
  volumes:
    - ./chroma_db:/app/chroma_db            # vector DB (bind, read-write)
    - ./prompts:/app/prompts                # prompts (bind) — edit karke restart, no rebuild
    - ./static:/app/static:ro               # widget/front-end (bind, read-only) — LIVE edit!
    - conv_data:/app/data                   # chat history (named volume, persists)
    - ${DOWNLOADS_DIR:-./downloads}:/app/downloads:ro    # corpus (read-only)
    - ${ATLASES_DIR:-./atlases_pdfs}:/app/atlases:ro
  healthcheck:
    test: [... GET http://localhost:8000/ready ...]   # embedder+Chroma+Neo4j ready tab hi traffic
  depends_on:
    neo4j: { condition: service_healthy }   # neo4j ready hone ke baad hi chat_api start
    redis: { condition: service_healthy }
```

### Networking ka raaz (3 line mein):
- **Service-to-service** (compose ke andar): naam se → `neo4j:7687`, `redis:6379`.
- **Container → host service** (Tabby/Ollama): `host.docker.internal:8080` / `:11434`.
- **Host → container** (tu browser se): `localhost:8000` (ports mapping se).
- ❌ Container ke andar `localhost:8080` = container khud, host nahi → isliye Tabby ke liye
  `host.docker.internal`.

### Volumes block (neeche):
```yaml
volumes:
  neo4j_logs:
  redis_data:
  conv_data:        # named volumes — Docker manage karta hai, data persist
  # neo4j_data host bind hai (./neo4j_data), named volume nahi
```

---

## Part 6 — `docker-entrypoint.sh` (root → appuser ka pul)

Chhoti par clever file. Container start hote hi yeh chalti hai (`ENTRYPOINT`):

```bash
set -euo pipefail
for d in /app/data /app/chroma_db; do
    if [ -d "$d" ]; then
        chown -R appuser:appuser "$d" 2>/dev/null || echo "could not chown $d — continuing"
    fi
done
exec gosu appuser "$@"     # ab asli command (uvicorn) APPUSER ke roop mein chalao
```

**Yeh kyun chahiye?**
- `/app/data` aur `/app/chroma_db` **writable mounts** hain jinki ownership **host decide
  karta hai** (image nahi). Agar yeh host-owned rahe, to non-root `appuser` unme likh nahi
  payega.
- Khaas problem: **ChromaDB apni SQLite file ko read ke liye bhi read-WRITE** kholta hai (WAL
  journal). Agar folder appuser ka nahi, to **"attempt to write a readonly database"** error
  aata hai (BM25 warm-up fail).
- Solution: container **root se shuru** ho, yeh do folder `appuser` ko de (chown), **phir
  `gosu` se appuser ban ke** uvicorn chalaye. **App kabhi root se nahi chalti** — sirf yeh
  chown root se hota hai.

> 🧠 `exec gosu appuser "$@"` = "apni jagah (PID 1) pe, appuser ban ke, jo command (CMD =
> uvicorn ...) diya tha woh chala do." `exec` se signals (Ctrl-C, stop) sahi se app tak
> pahunchte hain.

---

## Part 7 — ⭐ Volume mapping se real-time edit (tera main sawaal)

**Problem:** har chhoti edit pe `docker compose up --build` = 2-5 min wait. Bachna hai.

**Funda:** jo cheez tu baar-baar badalta hai, usse **bind-mount** kar do. Mount **image ke
andar ki baked copy ko "shadow" (dhak) deta hai** — container live host file dekhta hai, koi
rebuild nahi.

### Abhi already live-mounted hai (no rebuild needed):
| Mount | Edit ka asar |
|---|---|
| `./static:/app/static:ro` | **Front-end / widget / KaTeX** — host pe `static/` edit karo, `docker compose up -d` (ya browser refresh) se live. Cache bust ke liye `?v=` bump karo. |
| `./prompts:/app/prompts` | **Prompt templates** — edit karke container **restart** (`docker compose restart chat_api`) se naya prompt load. |
| `./chroma_db`, `./downloads`, `./atlases_pdfs` | Data/corpus — host pe badlo, container live dekhta hai (ingest dobara chalao). |

> 📌 Isiliye Dockerfile mein `COPY . .` `static/` ko bhi bake karta hai (fallback, agar repo
> disk pe na ho) — par compose ka bind mount use ke time **usko shadow karke live file**
> dikhata hai. Best of both: production mein self-contained image, dev mein live edit.

### Python application code (graph_rag/, chat_api/) live edit kaise?
**Yeh abhi bind-mounted NAHI hai** — `COPY . .` se image mein baked hai. Toh aise code change
ke liye normally rebuild chahiye. **Par dev ke liye tu live bana sakta hai** — ek
`docker-compose.override.yml` bana (compose ise auto-merge karta hai):

```yaml
# docker-compose.override.yml  (sirf DEV ke liye — prod mein mat use kar)
services:
  chat_api:
    volumes:
      - .:/app                      # poora project live mount (image ki copy ko shadow)
      - /app/.cache                 # par baked Docling models ko mat dhako (anonymous volume)
    command: ["uvicorn", "chat_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```
- `- .:/app` → tera saara code live; Python file save karte hi…
- `--reload` → uvicorn **khud restart** ho jata hai (hot reload). **Zero rebuild.**
- `- /app/.cache` line zaroori — warna tera host (jisme Docling models nahi) `/app/.cache`
  ko khaali se dhak dega aur models gayab ho jayenge.

> ⚠️ **Dev-only.** Production image **self-contained** honi chahiye (`.:/app` mount production
> mein mat karo — woh image ke baked, verified code ko bypass kar deta hai). Override file
> sirf local dev ke liye.

### Kab kya karna hai (cheat-sheet):
| Tune badla | Kya karo |
|---|---|
| `static/` (front-end) | `docker compose up -d` (mounted — turant) |
| `prompts/` | `docker compose restart chat_api` |
| Python code (dev, override + `--reload`) | bas save karo — auto-reload |
| Python code (no override) | `docker compose up -d --build chat_api` (rebuild — pip layer cache se tez) |
| `requirement.txt` | `docker compose build chat_api` (libraries dobara install — slow) |
| `.env` settings | `docker compose up -d` (env dobara load) |

---

## Part 8 — Commands cheat-sheet (rozmarra)

```bash
# Pehli baar / Dockerfile badla — build + start
docker compose up --build

# Background mein chalao (detached) — volume edits yahan pick hote hain
docker compose up -d

# Sirf chat_api dobara build karo (neo4j/redis chhede bina)
docker compose build chat_api
docker compose up -d chat_api

# Logs dekho (live tail)
docker compose logs -f chat_api

# Container ke andar shell lo (debug)
docker compose exec chat_api bash

# Restart (code/prompt reload ke liye)
docker compose restart chat_api

# Rok do + containers hatao (DATA volumes bache rahenge)
docker compose down

# Sab kuch hatao — volumes bhi (⚠️ chat history + data UD jayega)
docker compose down -v

# Container ke andar ingestion chalao
docker compose exec chat_api python main.py ingest
```

> 💡 `down` se container hatte hain par **named volumes (`conv_data`, `redis_data`) aur bind
> mounts (`./neo4j_data`, `./chroma_db`) bache rehte hain** — data safe. `down -v` lagao tabhi
> volumes udte hain (soch-samajh ke).

---

## Part 9 — Is project mein Docker ka faayda (kyun yeh sab)

1. **"Works everywhere"** — Tesseract, Poppler, Python 3.11, saari libs ek dabbe mein. ISRO
   ke server pe wahi chalega jo tere laptop pe.
2. **Air-gapped ready** — Docling models build-time pe baked, runtime pe **zero internet**.
   Offline government deployment ke liye perfect.
3. **One-command stack** — `docker compose up` se neo4j + redis + chat_api ek saath, sahi
   network + healthcheck + dependency order ke saath.
4. **Security baked-in** — non-root appuser, loopback-only DB ports, secrets `.dockerignore`
   se bahar, password-protected redis.
5. **Reproducible** — image ek baar ban gayi to har jagah byte-for-byte same. "Mere yahan
   chalta tha" khatam.
6. **Data safe** — code (image) aur data (volumes) alag. Container phaado-banao, chat history
   aur vector DB bachey rehte hain.
7. **Fast iterate (volume mounts)** — front-end/prompt/code (dev override) bina rebuild live.

---

## Part 10 — Common galtiyan (mat karna)

1. **`.env` ko `.dockerignore` se bahar nikalna.** Secret image mein chhap jayega — `docker
   history` se recover ho sakta hai. Hamesha ignore rakho (sirf `.env.example` bake karo).
2. **Container ke andar `localhost` se Tabby/Ollama dhoondhna.** Woh host pe hain —
   `host.docker.internal` use karo (compose mein already set hai).
3. **Production mein `.:/app` mount karna.** Yeh image ke verified, baked code ko bypass kar
   deta hai. Sirf dev override mein.
4. **`docker compose down -v` casually chalana.** `-v` named volumes uda deta hai — **chat
   history + redis data gayab**. Bina `-v` ke `down` safe hai.
5. **`requirement.txt` ke baad code COPY ka order todna.** Layer caching toot jayegi — har
   chhoti edit pe saari libraries dobara install hongi (slow build).
6. **chroma_db ka permission ignore karna.** Host-owned `chroma_db` + non-root app = "readonly
   database" error. Entrypoint chown karta hai — agar tu entrypoint hata de to yeh tootega.
7. **Neo4j password confusion.** `NEO4J_AUTH` sirf **fresh** `./neo4j_data` volume pe lagta
   hai. Purana volume apni purani setting rakhta hai jab tak reset/recreate na karo.

---

## Part 11 — Ek line mein

> **Dockerfile.api chat_api ka self-contained, offline-ready, non-root image banata hai
> (layer-cached pip + baked Docling models); .dockerignore secrets/data/junk ko image se
> bahar rakhta hai (data runtime pe volume se aata hai); docker-compose.yml neo4j+redis+chat_api
> ko network + healthcheck + host.docker.internal ke saath ek command mein khada karta hai;
> docker-entrypoint.sh root se mounts chown karke appuser pe drop karta hai; aur jo cheez tu
> baar-baar badalta hai (static/prompts/dev-code) usse volume-mount karke bina rebuild live
> edit kar sakta hai.**

---

## Reference — files jo padh sakta hai
- Image recipe: [Dockerfile.api](Dockerfile.api)
- Stack orchestration: [docker-compose.yml](docker-compose.yml)
- Privilege-drop wrapper: [docker-entrypoint.sh](docker-entrypoint.sh)
- Build-context exclusions: [.dockerignore](.dockerignore)
- Settings template: [.env.example](.env.example)
- Jude tutorials: [manifest_hashing_samjho.md](manifest_hashing_samjho.md), [drupal_ingestion_samjho.md](drupal_ingestion_samjho.md), [guardrails_samjho.md](guardrails_samjho.md), [eval_raga.md](eval_raga.md)
