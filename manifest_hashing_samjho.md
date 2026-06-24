# `manifest_hashing_samjho.md` — SHA-256 Manifest Hashing ko zero se samajh (Hinglish)

> Bhai, yeh file `graph_rag/ingestion/manifest.py` ke baare mein hai — woh **content-hash
> manifest** jo ingestion ko **incremental + crash-safe** banata hai. Maan ke chal raha hoon
> tujhe SHA-256 ya "manifest" ka **M bhi nahi** pata. End tak samajh jayega: hash kya hai,
> manifest kya hai, kyun chahiye, kaise kaam karta hai, aur kaha-kaha use hota hai.
>
> **Important:** yeh already bana hua hai. Tujhe samajhna hai + ek-do env knob jaanna hai.

---

## Part 0 — Kahani se samajh (5 minute)

Soch tere paas **500 PDF documents** hain jinhe chatbot ke andar daalna (ingest) hai.
Ingestion **mehenga** kaam hai:
- Har file ko parse karo (Docling, OCR…),
- Chunks mein todo,
- Har chunk ka **embedding** banao (Ollama),
- Har chunk pe **LLM se knowledge-graph extraction** chalao (sabse mehenga — paisa + time).

Ab maan le tune 500 files ingest kar li. Kal tune **5 nayi files** add ki aur dobara
ingestion chalaya. Kya hona chahiye?
- ❌ **Galat:** dobara saari 500 files process karo (1.5 ghante, paisa barbaad).
- ✅ **Sahi:** purani 495 ko **skip** karo, sirf 5 nayi process karo.

Par computer ko kaise pata "yeh file pehle ho chuki hai"? → **Manifest** se.

**Manifest = ek register/diary** jisme likha hai "yeh-yeh files main pehle hi process kar
chuka hoon". Agli baar file aaye, register mein dekho — agar likhi hai to **chhod do**.

Aur file ko pehchanne ke liye **naam (path)** use nahi karte — **content ka fingerprint
(SHA-256 hash)** use karte hain. Kyun? Woh agle part mein.

---

## Part 1 — SHA-256 hash kya hai? (bilkul basic)

**Hash = kisi bhi file/text ka chhota, fixed-size "fingerprint".**

- Tu koi bhi file (1 KB ya 1 GB) SHA-256 mein daal → hamesha **64-character ka ek string**
  nikalta hai, jaise: `ed73747ed22fd4db...795f7f081`.
- **Same content → hamesha same hash.** (1 GB file dobara hash karo, wahi 64 char.)
- **Thoda sa bhi content badla → bilkul alag hash.** (Ek comma badla → poora naya fingerprint.)
- Hash se wapas file nahi bana sakte (one-way). Sirf pehchanne ke kaam aata hai.

> 🧠 **Analogy:** Hash ek insaan ke **fingerprint** jaisa hai. Naam (file path) badal sakta
> hai — "Rahul" ko "Raj" bula do — par fingerprint wahi rahega. Aur agar fingerprint alag
> hai, to pakka alag insaan (alag content) hai.

**Isliye content-hash use karte hain, file-name nahi:**
- File ka **naam badla ya move kiya** par content same → **hash same** → "pehle ho chuki hai"
  pehchaan leta hai → skip. ✅
- File **edit ki** (content badla) → **naya hash** → "yeh nayi hai" → re-ingest. ✅

Tere code mein (`manifest.py`):
```python
def compute_file_hash(path: Path) -> str:
    """SHA-256 of a file's bytes, read in 64 KB chunks."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):   # 64 KB tukdo mein padho
            sha256.update(block)
    return sha256.hexdigest()                              # 64-char fingerprint
```
> 💡 **64 KB tukdo mein kyun?** Taaki 1 GB ki PDF ko poori RAM mein load na karna pade —
> thoda-thoda padhke hash banate jaate hain (streaming). Memory-safe.

---

## Part 2 — Manifest kya hai? (woh register)

Manifest ek **JSON file** hai (default `./ingest_manifest.json`, env
`INGEST_MANIFEST_PATH`). Format aisa hota hai:

```json
{
  "version": 1,
  "files": {
    "ed73747ed22fd4db...795f7f081": {
      "source": "downloads/oceansat2.pdf",
      "file_name": "oceansat2.pdf",
      "chunk_count": 42,
      "ingested_at": "2026-06-20T19:05:00+00:00"
    },
    "a1b2c3...": { "source": "...", "file_name": "...", "chunk_count": 17, "ingested_at": "..." }
  }
}
```

Matlab: **`file_hash → {kahan se aayi, naam, kitne chunks bane, kab ingest hui}`.**

`manifest.py` ka `IngestionManifest` class iske 4 simple kaam karta hai:
- **`load(path)`** — disk se register padho. File missing/corrupt ho → khaali register se shuru
  (crash nahi karta).
- **`is_ingested(file_hash)`** — "yeh hash register mein hai?" → True/False.
- **`record(file_hash, source, file_name, chunk_count)`** — nayi entry add karo (memory mein).
- **`save()`** — register ko disk pe likho (pretty JSON).

---

## Part 3 — Yeh ingestion mein kaise use hota hai (poora flow)

`graph_rag/ingestion/pipeline.py` ka `run()` yeh karta hai:

```
1. Manifest load karo  (agar --force NAHI diya)
        │
        ▼
2. Saari source files discover karo (downloads/, atlases_pdfs/)
        │  Har file ka SHA-256 nikaalo → manifest mein hai?
        │     • haan  → SKIP (parse/embed/LLM kuch nahi — paisa bacha)
        │     • nahi  → aage process karo
        ▼
3. Nayi files: parse → chunk → embed (Chroma) → KG extract (Neo4j)
        │
        ▼
4. SAB clean chala?  → manifest.record(...) har nayi file ke liye
        │
        ▼
5. manifest.save()  → register disk pe update
```

Key code (`pipeline.py`):
```python
# force=True ho to manifest bypass (sab dobara ingest)
manifest = None if self.force else IngestionManifest.load(settings.ingest_manifest_path)
documents = load_all_documents(self.folders, manifest=manifest, force=self.force)
...
# Sirf clean run ke baad record + save
manifest.record(file_hash, source=..., file_name=..., chunk_count=...)
manifest.save()
```

### ⭐ Sabse important design decision: "save only after clean run"
Manifest **tabhi disk pe likha jata hai jab poora run safal ho** (`run()` ke ant mein). Agar
beech mein **crash** ho gaya (file 250/500 pe), to manifest update **nahi** hota. Iska
matlab:
- Agle run pe woh aadhi-processed file **dobara try** hogi (kyunki register mein nahi aayi).
- **Crash-safe / idempotent** — aadha kaam silently "ho gaya" mark nahi hota.

> Yeh "incremental" (sirf naya kaam) + "crash-safe" (aadha kaam phir se) dono ek saath deta
> hai. Production ingestion ka backbone.

---

## Part 4 — `--force` flag (manifest bypass)

Kabhi-kabhi tujhe **sab kuch dobara** ingest karna ho (jaise tune chunking logic ya
embedding model badla). Tab:

```bash
python main.py ingest --force      # manifest ignore karo, har file dobara ingest
python main.py ingest              # normal — manifest se skip karo (sirf naya)
```

`--force` se `manifest = None` ho jata hai (upar code mein dekha), to har file process hoti
hai. Run ke ant mein manifest **fresh** likha jata hai naye hashes ke saath.

---

## Part 5 — Manifest guardrails se bhi juda hai (bonus, important)

Yeh chhupa hua connection samajh — manifest sirf ingestion ke liye nahi hai:

`guardrails/retrieval/source_allowlist.py` **wahi `ingest_manifest.json` padhta hai**, aur
ek rule lagata hai:

> **Sirf woh chunks cite ho sakte hain jinki file manifest mein ingested hai.**

Yaani agar koi chunk kisi aisi file se hai jo manifest mein nahi (jaise koi purana/orphan
data Chroma mein bach gaya), to woh **citation ke layak nahi** maana jata. Yeh ek extra
safety layer hai — answer sirf **verified, tracked** sources se ground ho.

- Agar manifest load na ho paaye → allowlist **fail-open** (warning log karke sab allow) —
  taaki availability na tute.
- Re-ingestion ke baad allowlist ko `reload` karna padta hai (taaki naye sources dikhein).

> Toh manifest = **ek hi SHA-256 register, do kaam**: (1) ingestion ko incremental banao,
> (2) guardrail ko batao kaunse sources legit hain. ([guardrails_samjho.md](guardrails_samjho.md) L2 dekh.)

---

## Part 6 — Ek hi idea, 4 jagah (taaki confuse na ho)

Tere repo mein SHA-256 hashing **4 alag jagah** use hota hai — concept same, kaam alag:

| Kahan | Kya hash hota hai | Kyun |
|---|---|---|
| `graph_rag/ingestion/manifest.py` | **File ke bytes** | Ingestion incremental — ingested file skip karo |
| `drupal_ingest.py` | **title + body_html** (article ka content) | Drupal delta-sync — unchanged article skip karo ([drupal_ingestion_samjho.md](drupal_ingestion_samjho.md)) |
| `graph_rag/eval/dataset.py` (`golden_checksum`) | **Poore golden dataset ka content** | Eval reproducibility — score ko exact dataset version se baandho ([eval_raga.md](eval_raga.md)) |
| `graph_rag/eval/ragas_runner.py` (`corpus_manifest_sha`) | **ingest_manifest.json ki file** | Eval manifest mein record karo kaunse corpus pe score nikla |

> 🧠 **Pattern yaad rakh:** jahan bhi "yeh cheez badli ya nahi / pehle dekh chuka ya nahi"
> decide karna ho — **content ka SHA-256 nikaalo aur compare karo.** Naam/time pe bharosa
> nahi, content pe.

---

## Part 7 — Kaise verify/test karein (tera TODO)

### ✅ Quick checks
- [ ] Manifest file dekh: `cat ingest_manifest.json` (ya `INGEST_MANIFEST_PATH` jo set ho).
      `files` ke andar hash → metadata dikhega.
- [ ] **Incremental test:** `python main.py ingest` chala (sab ingest ho jaye). Phir **turant
      dobara** `python main.py ingest` — second baar saari files **"skipped"** dikhni chahiye
      (`IngestionStats` summary mein), kyunki manifest mein aa chuki.
- [ ] **Rename test:** ek already-ingested file ko **rename** kar (content na badal), dobara
      `python main.py ingest` — **skip** honi chahiye (content hash same → pehchaan li).
- [ ] **Edit test:** ek file ka content thoda **badal**, dobara ingest — woh file **"updated/new"**
      dikhni chahiye (naya hash → re-ingest).
- [ ] **Force test:** `python main.py ingest --force` — saari files dobara process (kuch skip nahi).

### ✅ Unit tests
- [ ] `python -m pytest tests/ -k manifest -v` (manifest pure I/O hai, offline testable).

---

## Part 8 — Common galtiyan (mat karna)

1. **Manifest delete karke confuse hona.** Manifest uda diya → agla run **sab kuch dobara**
   ingest karega (mehenga). Yeh "reset" ka tarika hai, galti se mat karo.
2. **File-path se dedup expect karna.** Yeh **content** se dedup karta hai. Same content,
   alag naam = ek hi maana jayega (skip). Yeh feature hai, bug nahi.
3. **Crash ke baad "ho gaya" maan lena.** Crash hua to manifest save nahi hua — agla run
   us file ko phir try karega. Yeh sahi behaviour hai.
4. **Embedding/chunking badal ke `--force` na lagana.** Pipeline logic badli to purane chunks
   stale ho jate hain — `--force` se clean re-ingest karo.
5. **Re-ingest ke baad guardrail allowlist reload na karna.** Naye sources allowlist mein
   tabhi dikhenge jab manifest reload ho.

---

## Part 9 — Ek line mein

> **Har file ka SHA-256 fingerprint nikaalo → manifest (JSON register) mein dekho pehle
> ingest hui ya nahi → hui to skip (paisa bacha), nahi hui to process karo → poora clean
> run ke baad hi manifest save (crash-safe) → `--force` se bypass. Wahi manifest guardrail
> ko bhi batata hai kaunse sources legit hain.**

---

## Reference — files jo padh sakta hai
- Manifest module: [graph_rag/ingestion/manifest.py](graph_rag/ingestion/manifest.py)
- Kaha use hota hai (pipeline): [graph_rag/ingestion/pipeline.py](graph_rag/ingestion/pipeline.py)
- Ingestion package map: [graph_rag/ingestion/README.md](graph_rag/ingestion/README.md)
- Guardrail allowlist (wahi manifest): [guardrails/retrieval/source_allowlist.py](guardrails/retrieval/source_allowlist.py)
- Path config: [graph_rag/config.py](graph_rag/config.py) (`ingest_manifest_path`)
- Jude tutorials: [drupal_ingestion_samjho.md](drupal_ingestion_samjho.md), [guardrails_samjho.md](guardrails_samjho.md), [eval_raga.md](eval_raga.md)
