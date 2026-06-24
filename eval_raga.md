# `eval_raga.md` — RAGAS se apne Graph-RAG ko evaluate karna (Zero se, Hinglish mein)

> Bhai, is file ko shuru se aakhir tak padh. Maine maan ke chal raha hoon ki tujhe
> RAGAS ka **R bhi nahi** pata. Bilkul 5 saal ke bachche ko samjhane jaisa, simple
> Hinglish mein. End tak tu khud apni poori RAG pipeline ko evaluate kar payega aur
> "system production ke liye ready hai ya nahi" — yeh decide kar payega.
>
> **Important:** Tere repo mein yeh sab **already bana hua hai** (`graph_rag/eval/`).
> Tujhe code likhna nahi hai. Tujhe sirf (a) samajhna hai ki yeh kaam kaise karta hai,
> aur (b) ek asli **golden dataset** banana hai + **judge** set karna hai. Bas.

---

## Part 0 — Pehle 5 minute: kahani se samajh

Soch ek **school ka exam** hai.

- **Student** = tera chatbot (Graph-RAG pipeline). Sawaal aata hai, woh jawab deta hai.
- **Question paper + answer key** = **golden dataset**. Yeh tu banata hai: sawaal +
  uska sahi jawab (jise hum "reference" / "ground truth" bolte hain).
- **Examiner / teacher** jo copy check karta hai = **Judge LLM** (ek bada, smart model).
- **Report card / marksheet** = **scorecard** (GO / NO-GO).
- **RAGAS** = woh **checking ka system / rubric** jo teacher ko batata hai *"copy kaise
  check karni hai"* — kaunse number dene hain, kis cheez pe.

Toh evaluation ka matlab sirf itna:

> Student (chatbot) ko question paper (golden dataset) do → uske jawab lo → teacher
> (judge) se RAGAS ke rubric pe check karao → marksheet (scorecard) banao → agar saare
> minimum marks aaye toh **GO** (ship karo), warna **NO-GO** (abhi mat bhejo).

Itna hi hai. Ab har cheez detail mein.

---

## Part 1 — RAG aur RAGAS kya hai (basics)

### RAG kya hai?
RAG = **Retrieval-Augmented Generation**. Matlab:

1. **Retrieval** = User ka sawaal aaya → system tere documents (MOSDAC PDFs, satellite
   data, Neo4j knowledge graph, Chroma vector store) mein se **relevant tukde (chunks)**
   dhoondta hai. In tukdo ko **"context"** kehte hain.
2. **Generation** = Phir LLM (tera Tabby model) us context ko padh ke **jawab likhta hai**.

Toh do jagah galti ho sakti hai:
- **Retrieval kharab** → galat ya aadhure documents uthaye → jawab kaise sahi hoga?
- **Generation kharab** → documents sahi the, par LLM ne apne mann se cheezein jod di
  (yeh **"hallucination"** hai — sabse khatarnaak).

### RAGAS kya hai?
**RAGAS = "RAG Assessment".** Yeh ek Python library hai jo tere RAG ke jawab ko **number
mein** score karti hai. Jaise teacher 10 mein se 7 deta hai, waise RAGAS har jawab ko
0 se 1 ke beech score deti hai (1 = perfect, 0 = bekaar).

RAGAS akela ye decide nahi karti — woh ek **doosre bade LLM (judge)** se copy chexk
karwati hai. Isliye RAGAS ko **"LLM-as-a-judge"** evaluation bolte hain.

> 🧠 **Yaad rakh:** RAGAS khud koi jawab "sahi/galat" nahi jaanti. Woh judge LLM se
> sawaal poochti hai ("kya yeh jawab is context se support hota hai? haan/na") aur unko
> ginti karke 0–1 ka score banati hai.

---

## Part 2 — Tere repo mein evaluation ka poora flow (bird's eye view)

Tere paas yeh sab `graph_rag/eval/` folder mein already likha hua hai. Chala isse ek
hi command se:

```bash
python main.py ragas-eval
```

Andar yeh hota hai (yeh exact pipeline tere `graph_rag/eval/README.md` se hai):

```
golden dataset  (tests/eval/golden/v1/*.jsonl)      ← YEH TU BANATA HAI
       │
       ▼  probe.py  →  har sawaal ko ASLI chatbot se chalata hai (real ChatService)
   captured turns  (jawab, context jo retrieve hua, citations, refuse kiya ya nahi)
       │
       ▼  ragas_runner.py  →  RAGAS metrics  (faithfulness, answer relevancy, …)
       │                  └─  custom metrics  CE1–CE4  (numbers/formula/citation/refusal)
       ▼  stats.py  →  confidence interval (taaki 0.02 ka utaar-chadhav ko "progress" na samjhe)
       ▼  scorecard.py  →  har metric pe threshold lagao → final GO / NO-GO marksheet
```

Har file ka kaam (taaki tujhe pata ho kaun kya karta hai):

| File | Kaam (simple) |
|---|---|
| `graph_rag/eval/dataset.py` | Golden dataset ka **schema + loader + validation**. Tere JSONL ko padhta hai, galti ho toh chillata hai. |
| `graph_rag/eval/probe.py` | Har golden sawaal ko **asli chatbot** se poochta hai, aur **jo context LLM ne dekha wahi** record karta hai (re-retrieve nahi karta — yeh bahut important hai). |
| `graph_rag/eval/ragas_runner.py` | **Main boss.** Judge banata hai, RAGAS chalata hai, custom metrics jodta hai, sab aggregate karke report likhta hai. |
| `graph_rag/eval/custom_metrics.py` | **CE1–CE4** — woh checks jo RAGAS nahi karti (numbers, units, formula, citations, refusal). |
| `graph_rag/eval/scorecard.py` | **Pass/fail ke rules (thresholds)** aur final GO/NO-GO marksheet. |
| `graph_rag/eval/stats.py` | Statistics — bootstrap confidence interval, taaki number bharosemand ho. |
| `graph_rag/eval/harness.py` | Purana **sasta** evaluation (`python main.py eval`) — fast local testing ke liye. RAGAS wala nahi. |
| `main.py` | CLI — `python main.py ragas-eval` yahin se chalta hai. |

> 🔑 **Sabse zaroori line:** *"Capture, don't re-retrieve."* Eval us context pe jawab
> ko check karta hai jo LLM ne **actually dekha tha**, fresh retrieval pe nahi. Isiliye
> `probe.py` ek `RecordingRetriever` use karta hai jo bilkul production jaisa chalta hai
> par beech mein context yaad rakh leta hai. Iska matlab: tera eval = teri **asli
> production pipeline** ko naapta hai, koi fake/alag cheez ko nahi.

---

## Part 3 — RAGAS ke metrics (har ek ko bachche ki tarah samjho)

RAGAS har jawab ko kai angles se naapti hai. Tere code mein yeh metrics use hote hain
(`ragas_runner.py` ke `_RAGAS_METRIC_KEYS` se). Main har metric ko ek **chhota example**
ke saath samjha raha hoon.

Maan le sawaal: *"OCM sensor ka spatial resolution kya hai?"*
Sahi context (document): *"OCM provides ~360 m spatial resolution."*

### 1. Faithfulness (sabse important, "vishwas") — higher better
**Sawaal yeh poochta hai:** Jo jawab LLM ne diya, kya woh **poori tarah context se
support** hota hai? Ya LLM ne apne mann se kuch joda (hallucination)?

- Jawab: *"OCM 360 m resolution deta hai."* → context mein hai → **faithfulness ~1.0** ✅
- Jawab: *"OCM 360 m resolution deta hai aur usme 12 cameras hain."* → "12 cameras"
  context mein nahi hai → LLM ne jhooth joda → **faithfulness gir jayega** ❌

> Yeh tere system ka **core promise** hai ("sirf documents se bolo"). Isliye iska
> threshold sabse strict hai (≥ 0.90).

### 2. Answer Relevancy (`answer_relevancy`) — higher better
**Sawaal:** Jawab sawaal se **kitna relevant** hai? Idhar-udhar ki baat to nahi ki?

- Sawaal resolution ka, jawab resolution ka → relevant ✅
- Sawaal resolution ka, jawab "Oceansat-2 1999 mein launch hua" → off-topic ❌

### 3. Context Precision (`context_precision`) — higher better
**Yeh RETRIEVAL ko naapta hai.** Jo documents uthaaye, unme se **kitne actually kaam ke**
the? (Kya kachra bhi uthaya?)

- Top results sab relevant → precision high ✅
- 10 documents uthaye, sirf 2 kaam ke, 8 faltu → precision low ❌ (kachra context
  hallucination badhata hai)

### 4. Context Recall (`context_recall`) — higher better
**Yeh bhi RETRIEVAL ko naapta hai.** Jo jawab ke liye **zaroori** documents the, unme se
**kitne uthaye gaye**? (Kuch important chhoot to nahi gaya?)

- Jawab ke liye 1 document chahiye tha, woh uth gaya → recall ~1.0 ✅
- Jawab ke liye 3 facts chahiye the, sirf 1 uth paaya → recall low ❌ (jo retrieve hi
  nahi hua, usse ground nahi kar sakte)

> ⚠️ **Precision vs Recall yaad rakhne ka trick:**
> - **Recall** = "kya **sab zaroori** cheezein le aaye?" (kuch chhoot to nahi gaya)
> - **Precision** = "kya **sirf zaroori** cheezein leke aaye?" (kachra to nahi bhara)

### 5. Factual Correctness (`factual_correctness`) — higher better
**Sawaal:** LLM ka jawab tere **reference answer (answer key)** se factually kitna milta
hai? Yeh end-to-end usefulness naapta hai.

### Extra metrics (report hote hain par GATE nahi karte)
Yeh report mein dikhte hain par GO/NO-GO decide nahi karte — sirf diagnosis ke liye:
- **Context Entity Recall** — zaroori entities (jaise "Oceansat-2", "OCM") context mein
  aaye ya nahi.
- **Semantic Similarity** — jawab aur reference meaning mein kitne paas hain.
- **Noise Sensitivity** — faltu/noise context dene pe model kitna behak jaata hai.

> 📌 **Kaunse metric "smoke" mode mein chalte hain?** Jab tu `--smoke` lagata hai (fast,
> sasta test), sirf 4 chalte hain: `faithfulness`, `answer_relevancy`, `context_recall`,
> `factual_correctness`. Yeh tere code ke `_SMOKE_KEYS` mein likha hai.

---

## Part 4 — Custom metrics CE1–CE4 (RAGAS jo nahi karti)

RAGAS general-purpose hai. Par tera domain **satellite/science** hai — yahan ek galat
number ya galat unit = **galat science**. Isliye tere code (`custom_metrics.py`) mein 4
extra "domain" checks hain. Yeh **deterministic** hain (LLM nahi, simple Python logic),
isliye fast aur reliable.

| Code | Naam | Kya check karta hai (simple) | Example fail |
|---|---|---|---|
| **CE1** | Numeric & Unit Fidelity | Jawab ke **har number** ko context se match karta hai. Aur "sahi number, galat unit" ko alag se pakadta hai (**unit swap**). | Context "360 m" bolta hai, jawab "360 km" bolta hai → **unit swap** (chup-chaap khatarnaak galti) |
| **CE2** | Formula Fidelity | Formula bilkul **hu-ba-hu** (whitespace chhod ke) reproduce hua ya corrupt ho gaya. | `\sigma_0` ki jagah `\sigma` likh diya → fail |
| **CE3** | Citation Integrity | (a) Koi **farzi citation** `[S5]` to nahi banaya jo exist hi nahi karta? (b) Har factual sentence pe citation hai ya nahi? | Jawab mein `[S9]` likha par S9 jaisa koi source hi nahi tha → **fabricated** |
| **CE4** | Refusal Correctness | Jab mana karna chahiye tha tab mana kiya? Aur jab jawab dena tha tab diya? (Confusion matrix) | "GISAT-1 ki frequency?" (corpus mein hai hi nahi) → agar jawab de diya → **hallucination on absent** (worst) |

**CE4 ka confusion matrix** (yeh samajhna zaroori hai):

| | Jawab diya | Refuse kiya (mana) |
|---|---|---|
| **Answerable** (jawab tha) | ✅ true_answer | ❌ false_refusal (over-blocking — accha sawaal block kar diya) |
| **Unanswerable** (jawab nahi tha) | ❌ **hallucinated_on_absent** (sabse bura!) | ✅ true_refusal (sahi mana kiya) |

> 💡 Yahin se do bahut important number nikalte hain:
> - **`hallucination_rate`** = unanswerable sawaalo mein se kitne pe galti se jawab de
>   diya. (Threshold: ≤ 0.02 — yaani 100 mein se 2 se zyada nahi.)
> - **`false_refusal_rate`** = answerable sawaalo mein se kitne galti se block kar diye.
>   (Threshold: ≤ 0.08)
> - **`security_pass_rate`** = `should_refuse_unsafe` (injection/off-topic) sab block hue
>   ya nahi. (Threshold: **1.0 — 100%**, koi compromise nahi.)

CE1–CE4 production ke **wahi guardrail helpers** use karte hain jo live system use karta
hai (`guardrails.output.citation_verify`, `guardrails.output.grounding_check`) — isliye
eval aur asli system **same definition** of "grounded" pe agree karte hain.

---

## Part 5 — Judge LLM (sabse zyada log yahin galti karte hain)

RAGAS ke faithfulness/relevancy/context metrics ek **judge LLM** se score hote hain.
Agar judge kamzor ya galat hua → **saare RAGAS number bekaar** (kachra-in-kachra-out).

### Judge ke 2 golden rules:
1. **Judge, generator se ALAG aur ZYADA STRONG hona chahiye.** Tu jis model ko test kar
   raha hai (tera Tabby generator), judge **wahi model nahi** hona chahiye. Apni copy
   apne aap check karega toh number jhoothe honge. Frontier/bada model use kar.
2. **Determinism:** judge ka temperature 0 set hota hai (tere code mein already hard-coded)
   taaki har baar same result aaye.

### Judge kaise set karein? (env variables)
Tere `.env` mein yeh keys hain (`.env.example` line ~200 dekh). RAGAS judge OpenAI-compatible
endpoint use karta hai:

```bash
# ── RAGAS judge (python main.py ragas-eval) ──
RAGAS_JUDGE_MODEL=Qwen2.5-Coder-32B-Instruct      # ← STRONG model id (generator se alag rakh!)
RAGAS_JUDGE_BASE_URL=http://localhost:8080/v1      # OpenAI-compatible chat endpoint
RAGAS_JUDGE_API_KEY=<tera_token>
RAGAS_JUDGE_EMBED_MODEL=bge-large                  # embeddings (semantic similarity ke liye)
RAGAS_JUDGE_EMBED_BASE_URL=http://localhost:11434  # Ollama
RAGAS_JUDGE_EMBED_API_KEY=
```

> ⚠️ **Dekh ke chal:** abhi tere `.env` mein judge `Qwen2.5-Coder-32B` (local Tabby pe)
> set hai. Agar tera **generator bhi** wahi/similar local model hai, toh yeh rule #1 ka
> ulta hai. Asli production gate ke liye ek **stronger, alag** judge use karna behtar hai
> (ek frontier model — jaise koi bada hosted model). Local se sirf "kaam kar raha hai ki
> nahi" wala smoke test theek hai, par final GO/NO-GO ke liye strong judge zaroori.

### Judge pe bharosa kaise? → Kappa (κ)
Ek aur cheez: judge khud bhi galti karta hai. Toh kaise pata judge sahi check kar raha
hai? → **Tu khud (insaan) kuch jawab manually check karta hai, phir judge ke faisle se
compare karta hai.** Is agreement ko **Cohen's kappa (κ)** bolte hain (0 se 1).

- κ ≥ 0.6 → judge pe bharosa kar sakte hain (tere gate ka threshold yahi hai).
- Agar tu κ nahi deta (`--kappa` flag se), toh woh gate **SKIP** ho jata hai, aur SKIP =
  **GO block** ho jata hai (jaan-boojh ke — bina trusted judge ke gate paas nahi karne dega).

Yaani **asli GO ke liye tujhe judge calibrate karke `--kappa 0.7` jaisa pass karna padega.**
(Iska practical tarika Part 9 ke TODO mein hai.)

---

## Part 6 — Golden Dataset (YEH 80% kaam hai — dhyaan se)

Yeh **tera asli homework** hai. Pipeline already bani hai; golden dataset **tujhe banana
hai**. Acche dataset ke bina, score kitne bhi sundar dikhein, **jhoothe hain**.

### 6.1 Golden item kya hota hai?
Ek "golden item" = ek **test case**. Ek JSON line (JSONL format — har line ek alag JSON
object). Tere `dataset.py` ke `GoldenItem` ke yeh fields hain:

| Field | Zaroori? | Matlab (simple) |
|---|---|---|
| `id` | ✅ haan | Unique naam, jaise `"s1"`, `"m4"`, `"oos2"`. Duplicate hua toh loader error dega. |
| `stratum` | ✅ haan | Sawaal ka **type/category** (neeche list hai). |
| `user_input` | ✅ haan | Asli sawaal jo user poochega. |
| `reference` | answerable hai toh ✅ | **Sahi jawab (answer key).** Bina iske correctness check nahi ho sakta. |
| `reference_contexts` | optional | Woh exact passages jin pe sahi jawab grounded hai (context_recall ko madad). |
| `expected_entities` | optional | Jo entities aane chahiye, jaise `["OCM", "Oceansat-2"]`. |
| `expected_quantities` | numeric ke liye | Numbers + unit, jaise `[{"value": 360, "unit": "m"}]`. |
| `expected_formula` | formula ke liye ✅ | LaTeX formula, jaise `"$$\\sigma_0 = ...$$"`. |
| `answerable` | ✅ haan | `true` = jawab corpus mein hai. `false` = system ko **mana** karna chahiye. |
| `setup` | followup ke liye ✅ | Pehle ki conversation turns (history), jaise `["Oceansat-2 kaunse sensor carry karta hai?"]`. |

### 6.2 Strata (categories) — har type ka apna minimum
Tera plan (`evaluation_plan.md` §3.2) kehta hai dataset ko **9 strata** mein baato, aur
har stratum mein **minimum itne** items hone chahiye (taaki har category ka alag se
bharosemand score nikle):

| Stratum | Min n | Kya test karta hai |
|---|---|---|
| `single` | 25 | Ek-fact wala simple sawaal |
| `multihop` | 25 | 2+ documents jod ke jawab (GraphRAG ka asli imtihaan) |
| `comparison` | 20 | 2+ cheezein compare karna (numbers ka khel) |
| `formula` | 15 | Formula hu-ba-hu reproduce (CE2) |
| `numeric_edge` | 15 | Unit-swap / scale traps (CE1) |
| `followup` | 15 | History-aware sawaal ("aur iska resolution?") |
| `should_refuse_oos` | 30 | Corpus mein hai hi nahi → **mana karna chahiye** |
| `should_refuse_unsafe` | 15 | Injection / off-topic / PII → **block** |
| `answerable_but_sparse` | 15 | Jawab hai par ek lambe section mein chhupa → chunking ka test |
| **Total** | **≈ 175** | |

> 🚫 **"Sundar average" ke liye balance mat karo.** Negative strata (refuse wale) ko
> **bade** rakho, kyunki "jo data hai hi nahi uspe confident jhooth bolna" tere system ki
> **sabse buri** galti hai.

### 6.3 Asli example (tere repo se)
Tere `tests/eval/golden/v1/from_seed.jsonl` se ek line — yeh **answerable** hai:

```json
{"id": "s1", "stratum": "single", "user_input": "What spatial resolution does the OCM sensor provide?", "reference": "Ocean Colour Monitor (OCM) provides a spatial resolution of around 360 m.", "expected_entities": ["OCM"], "answerable": true}
```

`negatives.jsonl` se ek **refuse-honi-chahiye** line — dekh `answerable: false`:

```json
{"id": "oos1", "stratum": "should_refuse_oos", "user_input": "What is the exact downlink frequency in MHz of the GISAT-1 satellite?", "reference": "Out of corpus — should refuse: GISAT-1 downlink frequency is not in the MOSDAC document set.", "answerable": false}
```

`formula_numeric.jsonl` se formula wala:

```json
{"id": "fm1", "stratum": "formula", "user_input": "What is the radar equation for sigma-naught measured by OSCAT?", "reference": "sigma_0 = (P_r (4*pi)^3 R^4) / (P_t G^2 lambda^2 A).", "expected_formula": "$$\\sigma_0 = \\frac{P_r (4\\pi)^3 R^4}{P_t G^2 \\lambda^2 A}$$", "expected_entities": ["OSCAT"], "answerable": true}
```

### 6.4 Validation ke rules (loader chillayega agar todega)
`dataset.py` ka `load_golden` strict hai. Yeh galtiyan **error** dengi:
- `id` missing ya **duplicate** `id`.
- `user_input` khaali.
- `stratum` un 9 mein se nahi.
- **`should_refuse_*` stratum par `answerable: true`** → contradiction (refuse wale hamesha
  `answerable: false`).
- `answerable: true` par `reference` khaali (correctness check nahi hoga).
- `formula` stratum par `expected_formula` khaali.
- `followup` stratum par `setup` khaali.

> ✅ **Tip:** `//` ya `#` se shuru hone wali line **comment** maani jati hai — file mein
> notes likhne ke liye use kar sakta hai.

### 6.5 Golden dataset banane ke 2 tarike (recommended: dono mix)

**Tarika A — Haath se (human, sabse bharosemand):**
1. Apne MOSDAC documents (PDFs / Drupal content) khol.
2. Asli sawaal soch jo user poochega.
3. Document mein jaake **sahi jawab dhoondh**, usse `reference` mein likh.
4. Number/formula ko **document se verify** kar — andaaze se mat likh.
5. Sahi `stratum` aur `answerable` set kar.

**Tarika B — RAGAS se auto-generate, phir human review (tezi ke liye):**
RAGAS ke paas `TestsetGenerator` hai jo tere **asli corpus** se khud sawaal-jawab bana
deta hai. Phir tu unhe **review/fix** karke golden mein daalta hai. (Plan §3.1 yahi
"two-source" approach recommend karta hai.) Yeh ek alag chhota script hai — corpus
documents do, woh question + reference + context nikaal deta hai. Output ko **kabhi bina
review ke** gate mat banao.

**`should_refuse_oos` items banate waqt khaas dhyaan:** woh sawaal aisa hona chahiye jo
**plausible lage par corpus mein ho hi nahi**. Pakka karne ke liye: pehle corpus clean
ingest kar, phir confirm kar ki woh cheez sach mein documents mein nahi hai. Agar galti
se woh cheez corpus mein hai, toh tera sahi jawab **galat** mark ho jayega.

### 6.6 Versioning (important discipline)
- Dataset `tests/eval/golden/v1/` mein hai. Ek baar tune `v1` pe baseline le liya, toh
  `v1` ko **mat badal**. Naya content chahiye → `v2/` banao.
- Kyun? Har run ke saath ek **checksum** (`golden_checksum`) save hota hai. Agar tu `v1`
  badlega toh purane aur naye score compare karna jhooth ho jayega (alag paper pe alag
  exam). Naya version = naya benchmark.

---

## Part 7 — Command kaise chalaye

Sabse pehle yeh chahiye (live pipeline + judge):
- **Chroma** (vector store), **Neo4j** (graph), **Tabby** (LLM) chal rahe hon.
- `.env` mein `RAGAS_JUDGE_*` set ho.
- `venv` active ho (tere repo mein `venv/` hai).

### Basic run (poora gate, PROD config):
```bash
python main.py ragas-eval
```

### Saare flags (yeh `main.py` ke `cmd_ragas_eval` se hain):

| Flag | Default | Kaam |
|---|---|---|
| `--gold PATH` | `tests/eval/golden/v1` | Golden dataset file ya folder. |
| `--config NAME` | `PROD` | `PROD` (saare guardrails ON), `RAW` (guards sirf flag karte hain, block nahi), ya `BOTH` (dono chalao aur compare). |
| `--smoke` | off | Sirf 4 sasta metric — fast iteration / CI ke liye. |
| `--limit N` | sab | Sirf pehle N items chalao (jaldi test). |
| `--out DIR` | `eval_runs` | Output kahan likhna hai. |
| `--kappa F` | none | Judge↔human agreement (κ) gate ko do. Na do toh woh gate SKIP → GO block. |

### Recommended order (pehli baar):
```bash
# 1. Sabse pehle chhota smoke test — sirf 5 item, dekho pipeline chalti hai ki nahi
python main.py ragas-eval --smoke --limit 5

# 2. Phir poora smoke (sasta, saare items, 4 metric)
python main.py ragas-eval --smoke

# 3. Phir poora gate (PROD), judge calibrate karne ke baad kappa ke saath
python main.py ragas-eval --kappa 0.7

# 4. Production vs raw-guards comparison
python main.py ragas-eval --config BOTH --kappa 0.7
```

> 💡 **PROD vs RAW kya hai?** PROD = saare guardrails ON (asli production). RAW =
> guardrails sirf "flag" karte hain, block nahi karte (`grounding_action="flag"`,
> `citation_verify=False`). Dono compare karne se pata chalta hai: tera **guardrail layer
> kitna value add** kar raha hai. (Yeh `ragas_runner.py` ke `RAW_OVERRIDES` mein hai.)

---

## Part 8 — Output kaise padhe (marksheet samajhna)

Run ke baad `eval_runs/` folder mein 3 files banti hain:

1. `ragas_prod_<timestamp>.md` — **markdown scorecard** (insaan ke liye, GO/NO-GO yahin).
2. `ragas_prod_<timestamp>_items.jsonl` — **har item ka detail** (kahan fail hua, debug ke liye).
3. `ragas_prod_<timestamp>_manifest.json` — **reproducibility** (kaunse knobs, kaunsa
   judge, kaunsa dataset checksum — taaki 6 mahine baad same result reproduce ho).

Scorecard kuch aisa dikhega (`scorecard.py` ke `render()` se):

```
## Production Gate — ❌ NO-GO

| Gate | Value | Threshold | Result |
|------|-------|-----------|--------|
| Faithfulness            | 0.93 | ≥ 0.900 | ✅ PASS |
| Hallucination on absent | 0.05 | ≤ 0.020 | ❌ FAIL — answering when corpus silent is unacceptable |
| Numeric fidelity (CE1)  | 0.97 | ≥ 0.950 | ✅ PASS |
| ...                     | ...  | ...     | ...     |
| Judge trust (kappa)     | —    | ≥ 0.600 | ⚠️ SKIP — metric not evaluated |
```

Teen possible results:
- **✅ PASS** — metric threshold se accha hai.
- **❌ FAIL** — metric kharab hai (saath mein reason likha hota hai).
- **⚠️ SKIP** — metric calculate hi nahi hua (jaise judge κ nahi diya). **SKIP bhi GO ko
  block karta hai** — jaan-boojh ke, taaki "naapa hi nahi" ko "paas ho gaya" na samjhe.

### GO ka rule (`scorecard.py` ka `go`):
> **GO sirf tab jab HAR gate PASS ho, AND koi bhi stratum apne faithfulness floor (0.85)
> se neeche na gira.** Ek bhi FAIL ya SKIP ya ek bhi stratum red → **NO-GO**. Accha
> average ek red stratum ko chhupa nahi sakta. Yeh "no-mercy" gate hai — yeh tere system
> ko **users se pehle khud fail** karne ki koshish karta hai.

---

## Part 9 — Tera asli TODO (kya-kya kaam karna hai, step by step)

Pipeline ready hai. Jo **tujhe** karna hai, woh yeh — ek checklist banake do:

### ✅ Phase 1 — Setup (ek baar)
- [ ] `venv` active kar, dependencies install confirm kar (`ragas` 0.2 line — already pinned).
- [ ] Pipeline chal rahi hai confirm kar: `python main.py test` (Chroma/Neo4j/Tabby/Ollama health).
- [ ] Corpus ingest hua hai confirm kar: `python main.py ingest` (ya already ingested hai).
- [ ] `.env` mein `RAGAS_JUDGE_*` set kar — **judge generator se alag aur strong** ho.
- [ ] Chhota smoke chala ke pakka kar sab connect hai: `python main.py ragas-eval --smoke --limit 3`.

### ✅ Phase 2 — Golden dataset banao (yeh sabse bada kaam, §3.2)
- [ ] Decide kar: `v1` ko expand karega ya `v2/` banayega (baseline liya hai toh `v2/`).
- [ ] Har stratum ko uske **minimum** tak le ja (single 25, multihop 25, ... total ~175).
- [ ] **Verify kar:** har `reference`, `expected_formula`, `expected_quantities` ko asli
      MOSDAC document se milake check kar. Andaaze se likha hua data = jhootha gate.
- [ ] **`should_refuse_oos`:** har item ko confirm kar ki woh sach mein corpus mein nahi
      hai (warna sahi jawab galat mark hoga).
- [ ] (Optional) RAGAS `TestsetGenerator` se corpus pe seed banao, phir **haath se review**.
- [ ] `python main.py ragas-eval --smoke --limit 10` chala ke dataset **load + valid** hai confirm kar
      (loader galti pe error dega).

### ✅ Phase 3 — Judge calibrate karo (κ ke bina GO nahi milega)
- [ ] ~30–50 answered items ka eval chala, `_items.jsonl` se RAGAS ke faithfulness faisle nikaal.
- [ ] Wahi items **tu khud (insaan)** "grounded / not grounded" label kar.
- [ ] Judge ke label aur tere label ke beech **Cohen's kappa** nikaal (simple: `sklearn.metrics.cohen_kappa_score`).
- [ ] κ ≥ 0.6 aaye toh judge pe bharosa. Run ko `--kappa <value>` se do.
- [ ] κ < 0.6 → judge ya prompt sudhaar (stronger judge / better reference answers) phir dobara.

### ✅ Phase 4 — Asli gate chalao aur padho
- [ ] `python main.py ragas-eval --config BOTH --kappa <κ>` chala.
- [ ] `eval_runs/` ka markdown khol, GO/NO-GO dekh.
- [ ] Har FAIL ke liye `_items.jsonl` mein jaake **kaunse items** fail hue dekh — root cause:
      - Faithfulness low → LLM hallucinate kar raha / prompt tighten kar.
      - Context recall low → retrieval (chunking, top_k, reranker) sudhaar.
      - Context precision low → kachra retrieve ho raha / reranker / min-score badha.
      - CE1 unit swap → number/unit handling, prompt mein "units exact rakho".
      - hallucination_rate high → refusal/grounding guard kamzor.
      - false_refusal high → guard zyada sakht, thoda dheela kar.
- [ ] Sudhaar kar → dobara chala → compare kar (`--config BOTH` se PROD vs RAW dekh).

### ✅ Phase 5 — Lock & repeat
- [ ] Pehla calibrated baseline mile → `manifest.json` save kar (knobs + checksum freeze).
- [ ] Aage koi bhi change (chunking, model, prompt) ke baad **wahi gate** dobara chala —
      compare karke dekh better hua ya worse. Yeh tera **regression guard** hai.

---

## Part 10 — Common galtiyan (yeh mat karna)

1. **Judge = generator.** Apni copy apne se check = jhoothe number. Hamesha alag, strong judge.
2. **Bina verify kiye golden likhna.** Galat answer key = pipeline sahi hoke bhi fail dikhega.
3. **Negatives kam rakhna.** Refuse-strata (oos/unsafe) ko bada rakh — yahi system ki sabse buri galti pakadte hain.
4. **κ skip karke GO maangna.** Bina calibrated judge ke gate GO nahi dega (SKIP = block). Pehle κ nikaal.
5. **`v1` ko beech mein badalna.** Baseline ke baad content badla = comparison jhooth. Naya `v2/` kaat.
6. **Sirf average dekhna.** Ek stratum red ho sakta hai bhale average accha ho — gate phir bhi NO-GO. Per-stratum dekh.
7. **Smoke ko final samajhna.** `--smoke` sirf 4 metric, fast check ke liye. Final GO/NO-GO poore gate se.

---

## Part 11 — Ek line mein poora flow (yaad rakhne ke liye)

> **Golden dataset banao (verify karke) → judge set + calibrate karo (κ) →
> `python main.py ragas-eval --kappa κ` → `eval_runs/` ka scorecard padho →
> FAIL items ko `_items.jsonl` se debug karo → sudhaar ke dobara chalao →
> GO mile toh ship, NO-GO toh ruk.**

---

## Reference — files jo tu padh sakta hai
- Poori methodology: [evaluation_plan.md](evaluation_plan.md) (§3 golden dataset, §4 judge, §9 thresholds)
- Eval package ka map: [graph_rag/eval/README.md](graph_rag/eval/README.md)
- Golden dataset schema + loader: [graph_rag/eval/dataset.py](graph_rag/eval/dataset.py)
- Pass/fail thresholds: [graph_rag/eval/scorecard.py](graph_rag/eval/scorecard.py)
- Custom metrics CE1–CE4: [graph_rag/eval/custom_metrics.py](graph_rag/eval/custom_metrics.py)
- Asli pipeline pe capture: [graph_rag/eval/probe.py](graph_rag/eval/probe.py)
- Seed golden data: [tests/eval/golden/v1/](tests/eval/golden/v1/)
- CLI: [main.py](main.py) (`cmd_ragas_eval`)

*Bas bhai, ab tu khud poori pipeline evaluate kar sakta hai. Doubt aaye toh upar ka flow
dobara padh. All the best! 🚀*
