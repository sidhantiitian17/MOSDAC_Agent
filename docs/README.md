# `docs/` — Operational Runbooks

Long-form operator documentation. These are **markdown runbooks** (no code) for running
the system in production and air-gapped environments. For the architecture and per-folder
code docs, start at [readme_main.md](../readme_main.md).

---

## Contents

### [start_offline.md](start_offline.md) — air-gapped / ISRO on-prem setup
The full walkthrough for deploying with **no internet at runtime**: loading Docker images
from tarballs, pre-caching the Docling and embedding models, and verifying the offline
Neo4j/Tabby/Ollama wiring. Read this for the ISRO on-prem install.

### [BACKUP_RESTORE.md](BACKUP_RESTORE.md) — backup & disaster recovery
How to back up and restore the two stateful stores — **ChromaDB** (`./chroma_db/`) and
**Neo4j** (`./neo4j_data/`) — plus the per-user conversation database, and how to recover
from a corrupted index.

### [setup_raga.md](setup_raga.md) — RAGAS evaluation setup
How to configure and run the **RAGAS production gate**: pointing at a judge model (a
*stronger* model than the generator), preparing the golden dataset, and reading the
GO/NO-GO scorecard. Complements [evaluation_plan.md](../evaluation_plan.md) and
[graph_rag/eval/README.md](../graph_rag/eval/README.md).

---

## Related documentation (repo root)

| File | Purpose |
|------|---------|
| [readme_main.md](../readme_main.md) | The complete end-to-end guide (start here). |
| [install.md](../install.md) | Step-by-step install & startup. |
| [README.md](../README.md) | The original concise "start here" overview. |
| [production.md](../production.md) | Production-readiness review & hardening checklist. |
| [evaluation_plan.md](../evaluation_plan.md) | RAGAS evaluation methodology & gate. |
| [set_sso.md](../set_sso.md) | Keycloak/OIDC SSO setup notes. |
| [.env.example](../.env.example) | The authoritative, fully-commented config reference. |
| [deployments/README.md](../deployments/README.md) | Per-domain deployment customization. |
