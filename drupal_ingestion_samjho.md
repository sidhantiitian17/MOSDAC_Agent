# `drupal_ingestion_samjho.md` — Drupal JSON:API se Ingestion ko zero se samajh (Hinglish)

> Bhai, yeh file `drupal_ingest.py` ke baare mein hai — woh script jo tere **Drupal website
> ke articles** ko chatbot ke andar (Chroma + Neo4j) laata hai, woh bhi **incremental** (sirf
> jo badla). Maan ke chal raha hoon tujhe "JSON:API" ya "Drupal ingestion" ka **D bhi nahi**
> pata. End tak samajh jayega: kya, kyun, kaise, config, run, verify.
>
> **Important:** yeh already bana hua hai. Tujhe samajhna hai + `.env` config set karna hai.

---

## Part 0 — Kahani se samajh (5 minute)

Tere paas do tarah ka data hai jo chatbot ko jaanna chahiye:
1. **PDF/HTML files** (downloads/, atlases_pdfs/) — yeh file-based ingestion handle karti hai.
2. **Drupal website ka content** (articles/news/pages) — yeh ek **live website** pe baithta
   hai, file nahi. Ise laane ke liye `drupal_ingest.py`.

**Drupal** ek CMS hai (website banane ka system, jaise WordPress). Usme content "nodes"
(articles) ke roop mein hota hai. Drupal ek **JSON:API** deta hai — ek URL jisse tu uske
articles ko **JSON format** mein padh sakta hai (HTML page nahi, machine-readable data).

Soch yeh aise: **Drupal website ek library hai, JSON:API uska "issue counter".** Tu counter
pe jaake "saari nayi/badli hui kitaabein do" maangta hai, woh ek-ek karke deta hai. Tu unhe
padhke chatbot ke dimaag (Chroma/Neo4j) mein daal deta hai.

Aur sabse important: **sirf woh articles process karo jo naye ya badle hain** — jo same hain
unhe chhod do (paisa/time bacha). Yeh "delta sync" SHA-256 hash se hota hai (bilkul manifest
jaisa — dekh [manifest_hashing_samjho.md](manifest_hashing_samjho.md)).

---

## Part 1 — Poora flow (bird's eye view)

```
Drupal website (JSON:API URL)
        │  DrupalClient.iter_nodes()  — Basic auth + pagination (links.next)
        ▼  ek-ek node (article) JSON
   parse_node()  — title nikaalo, HTML se clean text banao (BeautifulSoup),
        │          content_hash = SHA-256(title + body_html)
        ▼
   StateManager.verdict(uuid, hash)  →  "new" / "updated" / "skip"
        │
        │   skip      → kuch mat karo (counter++)
        │   updated   → purane Chroma chunks delete karo, phir re-ingest
        │   new       → ingest karo
        ▼
   ingest_node()  →  IngestionPipeline.run_on_documents([document])
        │            (wahi KG/vector code path jo file-ingestion use karta hai!)
        ▼
   state.record(uuid, hash)  →  end mein  state.save()  (atomic write)
```

Output: ek stats dict — `{scanned, new, updated, skipped, errors}`.

---

## Part 2 — Step-by-step, har hissa detail mein

### Step 1 — Config (`DrupalConfig.from_env`)
Drupal-specific settings `.env` se aati hain. Baaki sab (Chroma path, Neo4j, chunk size,
embedder) `graph_rag.config.settings` se inherit hota hai — **kuch duplicate nahi**.

```bash
# .env (zaroori)
DRUPAL_JSONAPI_URL=http://my-drupal-site.ddev.site/jsonapi/node/article
DRUPAL_USERNAME=api_user
DRUPAL_PASSWORD=change-me

# optional (defaults dikhaye hain)
DRUPAL_STATE_PATH=drupal_ingestion_state.json   # delta-sync register kahan
DRUPAL_PAGE_SIZE=50                              # ek API page mein kitne nodes
DRUPAL_REQUEST_TIMEOUT=30                        # seconds
DRUPAL_KG_MIN_CONFIDENCE=0.6                     # KG extraction ka confidence threshold
```
> ⚠️ Teeno (`URL`, `USERNAME`, `PASSWORD`) **required** hain — na ho to script `SystemExit`
> karke saaf bata degi konsa missing hai.

### Step 2 — Articles fetch karo (`DrupalClient`)
- **Basic auth** se Drupal mein login (`HTTPBasicAuth(username, password)`).
- Header `Accept: application/vnd.api+json` (JSON:API ka standard).
- **Pagination:** Drupal ek baar mein sab nahi deta, "pages" mein deta hai. `iter_nodes()` ek
  **generator** hai — ek node de, RAM saaf, agla page `links.next` se follow karo, jab tak
  pages khatam. **Flat memory** — 10,000 articles ho ya 10, RAM same.

```python
def iter_nodes(self):
    url = self._config.jsonapi_url
    params = {"page[limit]": self._config.page_size}
    while url:
        resp = self._session.get(url, params=params, timeout=...)
        for node in resp.json().get("data", []):
            yield node                      # ek-ek karke do
        url = payload["links"]["next"]["href"]   # agla page
        params = None
```

### Step 3 — Parse + hash (`parse_node`)
Har node se:
- `uuid` (Drupal ka unique id, jaise `7697caff-585c-...`),
- `title`,
- `body_html` (raw HTML),
- **`clean_text`** = HTML tags hata ke saaf text (`BeautifulSoup(...).get_text()`),
- **`content_hash`** = `SHA-256(title + body_html)` — yeh delta-sync ka dil.

```python
def compute_content_hash(title, body_html):
    canonical = json.dumps({"title": title, "body": body_html}, sort_keys=True, ...)
    return hashlib.sha256(canonical.encode()).hexdigest()
```
> 💡 **`sort_keys=True` kyun?** Taaki dict ka order chahe kuch bhi ho, same content ka hash
> hamesha same aaye. (Canonical JSON.)

### Step 4 — Verdict: new / updated / skip (`StateManager`)
`drupal_ingestion_state.json` ek register hai: `{uuid: content_hash}`. Asli file aisi
dikhti hai:
```json
{
  "7697caff-585c-42d5-8db1-766c63790496": "ed73747ed22fd4db...795f7f081"
}
```
Logic (`verdict`):
- uuid register mein **nahi** → **"new"** (pehli baar dekha).
- uuid hai par hash **alag** → **"updated"** (article edit hua).
- uuid hai aur hash **same** → **"skip"** (kuch nahi badla, chhod do). ✅ paisa bacha.

### Step 5 — Ingest (`ingest_node` → `IngestionPipeline.run_on_documents`)
Yeh sabse important design baat:

> Drupal article ko ek LangChain `Document` mein wrap karke **wahi `IngestionPipeline`** se
> chalaya jata hai jo file-ingestion use karti hai (`run_on_documents`). Iska matlab:
> quantity_parser, measurements, resolver, upsert_triples, upsert_chunks — **sab same code**.
> Drupal ke liye alag KG logic **duplicate nahi** kiya gaya.

Do khaas baatein:
1. **`extract_at_document_level=True`** — poore article ke liye **ek hi LLM call** (har chunk
   ke liye alag nahi). Yeh "N-LLM-calls-per-article" bug ko fix karta hai (paisa bachao).
2. **Updated node ke liye pehle purane chunks delete** (`_delete_stale_vector_chunks`) — warna
   Chroma mein purana + naya dono reh jayega (duplicate/stale). Delete `drupal_uuid` metadata
   filter se hota hai. Yeh Drupal-specific concern hai (pipeline ko Drupal UUID ka pata nahi).

```python
def _to_document(parsed):
    return Document(
        page_content=parsed.text,
        metadata={
            "source": parsed.uuid,
            "file_name": parsed.title,
            "file_hash": parsed.content_hash,
            "drupal_uuid": parsed.uuid,   # Chroma deletion filter key
        },
    )
```

### Step 6 — Record + save (atomic)
Article safal ingest hua → `state.record(uuid, hash)`. Sab nodes ke baad → `state.save()`.

> 🔒 **Atomic write:** save temp file pe likhta hai phir `os.replace()` se asli file pe move
> karta hai. Agar beech mein crash ho → state file **corrupt nahi** hoti (purani intact
> rehti hai). "Torn write" se bachao.

---

## Part 3 — Kaise chalayein (run karna)

Do tarike:

### Tarika A — Seedha Drupal script
```bash
python drupal_ingest.py
```

### Tarika B — Main ingestion ke saath (auto)
`main.py ingest` **automatically Drupal bhi chala deta hai agar `DRUPAL_JSONAPI_URL` set hai**:
```bash
python main.py ingest                 # files + Drupal (agar URL set ho)
python main.py ingest --skip-drupal   # sirf files, Drupal chhodo
python main.py ingest --skip-files    # sirf Drupal, files chhodo
```

Run ke ant mein stats dikhega:
```
Ingestion complete — scanned 120 | new 5 | updated 3 | skipped 112 | errors 0
```
- **scanned** = total articles dekhe.
- **new** = pehli baar ingest hue.
- **updated** = badle hue, re-ingest hue.
- **skipped** = same the, chhod diye (delta-sync ka faayda).
- **errors** = jo fail hue (ek fail se poora run nahi rukta — woh skip karke aage badhta hai).

---

## Part 4 — File-ingestion vs Drupal-ingestion (antar samajh)

| Cheez | File ingestion (`main.py ingest`) | Drupal ingestion (`drupal_ingest.py`) |
|---|---|---|
| Source | Local PDF/HTML files | Live Drupal website (JSON:API) |
| Delta register | `ingest_manifest.json` | `drupal_ingestion_state.json` |
| Hash kis cheez ka | **file ke bytes** | **title + body_html** |
| Dedup key | content hash | **Drupal uuid** → hash |
| Update pe purana delete? | Naya hash = nayi file | Haan — `_delete_stale_vector_chunks` (uuid se) |
| KG/vector code | `IngestionPipeline.run()` | `IngestionPipeline.run_on_documents()` — **same core** |
| LLM calls | per chunk (default) | per article (`extract_at_document_level=True`) |

> 🧠 Dono mein **idea same**: SHA-256 se "badla ya nahi" check karo, nahi badla to skip.
> Bas register aur source alag. (Detail: [manifest_hashing_samjho.md](manifest_hashing_samjho.md))

---

## Part 5 — Kaise verify/test karein (tera TODO)

### ✅ Setup
- [ ] Drupal site chal rahi hai aur JSON:API on hai confirm kar. URL browser mein khol ke
      dekh JSON aata hai: `http://<site>/jsonapi/node/article`.
- [ ] `.env` mein `DRUPAL_JSONAPI_URL`, `DRUPAL_USERNAME`, `DRUPAL_PASSWORD` set kar.
- [ ] Auth test: `curl -u api_user:change-me -H "Accept: application/vnd.api+json" <URL>` —
      JSON `data` array aana chahiye.

### ✅ Delta-sync test (yeh zaroor kar)
- [ ] Pehli baar chala: `python drupal_ingest.py` → sab **"new"** dikhenge.
- [ ] **Turant dobara** chala → ab sab **"skipped"** (kuch nahi badla). ✅ delta-sync kaam kar raha.
- [ ] Drupal mein **ek article edit** kar → dobara chala → woh ek **"updated"** dikhe, baaki skip.
- [ ] `cat drupal_ingestion_state.json` — `{uuid: hash}` entries dikhengi.

### ✅ End-to-end test
- [ ] Ingest ke baad chatbot se us article ka content poochho (`python main.py chat`) — jawab
      milna chahiye + citation.
- [ ] Updated article ka **purana** content poochho — purana jawab **nahi** aana chahiye
      (stale chunks delete ho gaye).

---

## Part 6 — Common galtiyan (mat karna)

1. **State file delete karke confuse hona.** `drupal_ingestion_state.json` uda diya → agla
   run **saare articles dobara** ingest karega (mehenga). Reset ka tarika hai, galti se mat kar.
2. **Updated node pe stale chunks.** Agar `_delete_stale_vector_chunks` skip ho (jaise
   `--skip-vector` ke saath), to Chroma mein purana + naya dono reh sakta hai. Dhyaan rakh.
3. **Auth/credentials galat.** Basic auth fail → `requests.HTTPError`. `DRUPAL_USERNAME/PASSWORD`
   check kar, aur Drupal user ko JSON:API read permission ho.
4. **JSON:API band hona.** Drupal mein JSON:API module enable hona chahiye, aur `node/article`
   endpoint exposed ho.
5. **Bade body ka hash sirf clean text pe socha.** Hash **raw `body_html`** pe hai (clean text
   pe nahi) — taaki HTML formatting change bhi "updated" trigger kare. Yeh intentional hai.

---

## Part 7 — Ek line mein

> **Drupal JSON:API se articles ek-ek (paginated, low-memory) fetch karo → title+body ka
> SHA-256 nikaalo → state register se new/updated/skip decide karo → updated ke purane Chroma
> chunks delete karke, naye ko wahi `IngestionPipeline.run_on_documents` se ingest karo (ek
> LLM call/article) → state atomically save. `main.py ingest` ise auto chala deta hai jab
> `DRUPAL_JSONAPI_URL` set ho.**

---

## Reference — files jo padh sakta hai
- Drupal ingestion script: [drupal_ingest.py](drupal_ingest.py)
- Shared core pipeline: [graph_rag/ingestion/pipeline.py](graph_rag/ingestion/pipeline.py) (`run_on_documents`)
- Ingestion package map: [graph_rag/ingestion/README.md](graph_rag/ingestion/README.md)
- CLI auto-wiring: [main.py](main.py) (`cmd_ingest` ka Drupal step)
- State file (live): [drupal_ingestion_state.json](drupal_ingestion_state.json)
- Jude tutorials: [manifest_hashing_samjho.md](manifest_hashing_samjho.md), [guardrails_samjho.md](guardrails_samjho.md), [eval_raga.md](eval_raga.md)
