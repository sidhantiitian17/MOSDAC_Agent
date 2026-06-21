# Backup & Disaster Recovery Runbook

State that must survive a host loss (production.md §3). All paths assume the
`docker-compose.yml` layout (Neo4j bind-mount `./neo4j_data`, Chroma `./chroma_db`).

## What holds state

| Store | Location | Rebuildable from source? | Backup priority |
|---|---|---|---|
| **Neo4j** (knowledge graph) | `./neo4j_data` (bind mount) | Yes, by re-ingesting — but slow/expensive | High |
| **ChromaDB** (vectors) | `./chroma_db` | Yes, by re-ingesting (re-embeds the whole corpus) | High |
| **Ingestion manifest** | `./ingest_manifest.json`, `./drupal_ingestion_state.json` | Regenerated, but drives incremental ingest | Medium |
| **Scope centroid** | `./guardrails_data/scope_centroid.npy` | Recomputed on first request | Low |
| **Redis sessions** | Redis volume (if used) | No — ephemeral chat history (TTL'd) | Low |
| **Audit log** | `GUARD_AUDIT_LOG_PATH` | No — compliance record | High (if enabled) |
| **Source corpus** | `./downloads`, `./atlases_pdfs` | The ground truth — back up or keep re-fetchable | High |

> Vectors and the graph are **derived** data. The authoritative source is the
> document corpus; with it you can always rebuild via `python main.py ingest`.

## Backup (cold/offline — safest)

```bash
docker compose stop neo4j chat_api          # quiesce writers
tar czf mosdac-backup-$(date +%F).tar.gz \
    neo4j_data chroma_db ingest_manifest.json \
    drupal_ingestion_state.json guardrails_data
docker compose start neo4j chat_api
```

## Backup (hot Neo4j — no downtime)

```bash
# Online dump to a file inside the container, then copy out.
docker exec mosdac_neo4j neo4j-admin database dump neo4j \
    --to-path=/data/backups
docker cp mosdac_neo4j:/data/backups ./neo4j-dumps
```

Chroma is a file store — snapshot `./chroma_db` with the filesystem/volume
snapshot tool while the API is briefly paused, or rely on the nightly cold backup.

## Restore

```bash
docker compose down
tar xzf mosdac-backup-YYYY-MM-DD.tar.gz
# (or) restore a Neo4j dump:
#   docker run --rm -v ./neo4j_data:/data -v ./neo4j-dumps:/backups neo4j:5.18.0 \
#     neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
docker compose up -d
curl -fsS http://localhost:8000/ready    # confirm deps healthy before serving
```

## Full rebuild from source (last resort)

```bash
docker compose up -d neo4j
python main.py ingest --force            # re-parse, re-embed, re-extract everything
curl -fsS http://localhost:8000/ready
```

## After ANY restore or re-ingest on a running server

Pick up the new corpus without a restart (P1-4):

```bash
curl -X POST http://localhost:8000/reload -H "X-Admin-Token: $CHAT_API_ADMIN_TOKEN"
```

## Verification checklist

- [ ] `GET /ready` returns `{"ready": true}` (embedder + Chroma + Neo4j ok)
- [ ] `GET /health` 200
- [ ] A known grounded question returns an answer with citations
- [ ] `schema_report()` entity/relationship counts match pre-incident baseline
      (`python main.py test` prints these)

## Recommended cadence

- **Nightly**: cold tar of derived stores + manifests (retain 7).
- **Weekly**: off-host copy (object storage) of the latest backup.
- **Always**: keep `./downloads` + `./atlases_pdfs` (or their fetch scripts) so a
  full rebuild is always possible even if every backup is lost.
