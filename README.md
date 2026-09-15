# Texas Power Outage Consensus Pipeline

A production-style data sourcing, consensus, and serving pipeline for **live
power-outage status**. It ingests outage data from **seven independent online
sources** (four electric utilities' own outage maps and three aggregator
sites), computes a cross-source **consensus** with dynamic confidence/quality
scores, stores raw + consensus data in **PostgreSQL (JSONB)** under a **TTL
lifecycle**, and serves a standardized REST payload for agent runtimes.

> Assignment task **#81** from the catalog — Energy & Utilities / Grid /
> Outage status — `GET /v1/energy/outages` with `{"region": "TX"}`.

## Sources

| Source id | Publisher | Kind | Gives |
|-----------|-----------|------|-------|
| `ONCOR` | Oncor Electric Delivery (Kubra Storm Center JSON) | utility, first-hand | customers out by county + restoration ETA |
| `CPS` | CPS Energy, San Antonio (Kubra) | utility, first-hand | same |
| `AUSTIN_ENERGY` | Austin Energy (Kubra) | utility, first-hand | same |
| `TNMP` | Texas-New Mexico Power (Kubra) | utility, first-hand | same |
| `OUTAGE_PRO` | outage-pro.com (HTML) | aggregator | all 254 counties + top utilities |
| `OUTAGE_ONLINE` | outage.online (HTML) | aggregator | all counties + 24 utilities |
| `USOUTAGE` | usoutage.com (HTML) | aggregator | all counties + 68 utilities |

All are public and keyless. The brief allows scraping; the three aggregator
pages are parsed with plain regexes and every parse failure is a typed error.

## Does it run?

Yes. A migration creates the DB/schema, an ingestion worker populates it from
the live sources (one pass or continuously), a TTL worker manages lifecycle,
and a FastAPI service serves consensus data read **only** from Postgres. A
38-test pytest suite (unit + parser + API + SLA + lifecycle) passes.

## Layout

| Path | What it is |
|------|------------|
| `app/config.py` | Source registry/weights, tolerances, TTLs, region list |
| `app/sources.py` | Seven adapters (Kubra JSON ×4, HTML ×3); typed errors, no silent fallback |
| `app/consensus.py` | **Pure** consensus: weighted clustering, outlier rejection, floors, ETAs |
| `app/ingestion.py` | Ingestion worker (fetch → store raw → consensus → snapshot), `--loop` |
| `app/ttl.py` | Lifecycle worker: hourly rollups, purge raw + old snapshots, mark stale |
| `app/main.py` | FastAPI serving layer (`GET /v1/energy/outages`) |
| `app/db.py` | Postgres connections + a small pool for the API |
| `migrations/` | `migrate.py` runner + `001_init.sql` schema (5 tables, GIN indexes) |
| `tests/` | `test_consensus.py`, `test_sources.py`, `test_api.py`, `test_sla.py` |
| `docs/` | `report.pdf` (deliverable), `res.md` (run results), assignment materials |
| `.github/workflows/` | CI (tests on a Postgres service) + hourly live-source health check |

## Prerequisites

- **Python 3.11+** (developed on 3.12; use the `py` launcher on Windows).
- **PostgreSQL 16** on `localhost:5432` (any standard install; the dev machine
  uses the portable binaries at `D:\PostgreSQL\16`, Windows service
  `postgresql-16-D`).

## Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

copy .env.example .env
#   then edit .env and set DB_PASSWORD
```

## Run (exact commands)

```powershell
# 1. Create the database and apply the schema (idempotent).
.venv\Scripts\python.exe -m migrations.migrate

# 2. One ingestion pass over all seven live sources (~15 s).
.venv\Scripts\python.exe -m app.ingestion

#    Or continuously, one pass every INGEST_INTERVAL_SECONDS (default 300 = 5 min):
.venv\Scripts\python.exe -m app.ingestion --loop

# 3. Start the API.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. Query it (in another shell).
curl "http://127.0.0.1:8000/v1/energy/outages?region=TX"
curl "http://127.0.0.1:8000/v1/energy/outages?region=TX&area=Harris"
curl "http://127.0.0.1:8000/v1/energy/outages?region=TX&min_customers=100&limit=10"

# 5. Run the TTL/lifecycle worker (rollups, purge, stale marking).
.venv\Scripts\python.exe -m app.ttl

# 6. Run the full test suite.
.venv\Scripts\python.exe -m pytest
```

### Query parameters

| Param | Default | Meaning |
|-------|---------|---------|
| `region` | required | Region code; `TX` is the tracked region, anything else → 404 |
| `area` | — | Optional county filter (case-insensitive), e.g. `Harris` |
| `min_customers` | `1` | Hide areas/utilities with fewer customers out (`0` lists all 254 counties) |
| `limit` | `50` | Max rows in `outages` and `utilities` (≤ 500) |

## Design notes

- **Two facts, one rule.** Consensus runs on *county* customers-out and on
  *utility* customers-out. For each figure, every value defines a candidate
  cluster (reports within `max(25, 20 %)`); the cluster with the most
  reliability weight wins (utility feeds 0.55, aggregators 0.15 each), its
  members are averaged by weight, the rest are outliers. Confidence starts at
  0.90–0.97 by in-cluster spread and drops in proportion to outlier weight;
  `verified` needs ≥ 2 agreeing sources, confidence ≥ 0.85 and a weight
  majority. One source, or nobody agreeing → conservative (highest) figure at
  confidence 0.5, never verified.
- **Utilities arbitrate and floor.** A utility's own county figure votes as one
  first-hand value; a *lower* first-hand figure is consistent (aggregators also
  count utilities we don't fetch) and is not an outlier; a *higher* one raises
  the served value — we never report fewer customers out than a utility admits.
  Restoration `eta` = the latest estimate among that county's utility outages.
- **No silent fallbacks.** Timeouts, 5xx, 403/429, 404 and unparseable pages
  are typed `SourceError`s written to `ingestion_errors` (and a failed
  `raw_ingests` row); the pass continues; failed sources are named in the
  snapshot's `warnings`. If every source fails, no snapshot is written.
- **Freshness.** Catalog says *minutes*: `TTL_SECONDS = 900`. `age_seconds`
  is measured from `computed_at`; `stale: true` plus a warning beyond the TTL.
- **Lifecycle.** Raw payloads purge after 6 h; snapshots older than 24 h are
  summarised into `area_rollups` (per region/county/hour: samples, max, avg,
  min confidence) before deletion; aged snapshots are flagged stale.
- **API reads only Postgres**, via a connection pool — p95 < 200 ms asserted.
- **Rate limit enforced.** The advertised 100 requests / 60 s is applied per
  client IP with a clean `429` error object and `Retry-After` header
  (`RATE_LIMIT_ENFORCE=0` disables it).
- **Storage.** JSON sources are stored verbatim; HTML sources are stored as the
  raw page (gzip + base64, ~60 kB) plus the extracted micro-data and a sha256,
  so the audit trail can reproduce exactly what the parser saw.

## Known limitations

- Aggregators poll utilities on their own schedules, so they routinely lag by
  10–40 minutes — this is the disagreement the consensus rule exists to handle.
- Only Texas is wired up; adding a region means adding its sources to
  `config.SOURCES` / `KUBRA` / `AGGREGATOR_URLS` and `REGIONS`.
