# CVE Intelligence Pipeline

A production-style data sourcing, consensus, and serving pipeline for CVE
(vulnerability) intelligence. It ingests live CVE data from **two independent
sources** (NVD and CIRCL), computes a cross-source **consensus** with dynamic
confidence/quality scores, stores raw + consensus data in **PostgreSQL (JSONB)**
under a **TTL lifecycle**, and serves a standardized REST payload for agent
runtimes.

> Assignment task #23 from the catalog — `GET /v1/security/cve`.

## Does it run?

Yes. It is a runnable pipeline: a migration creates the DB/schema, an ingestion
worker populates it from live sources, a TTL worker manages lifecycle, and a
FastAPI service serves consensus data read **only** from Postgres. A full pytest
suite (unit + API + SLA) passes.

## Layout

| Path | Live? | What it is |
|------|-------|------------|
| `app/config.py` | ✅ | Watchlist, source registry/weights, shared consensus constants |
| `app/db.py` | ✅ | Postgres connections + a small pool for the API |
| `app/sources.py` | ✅ | NVD + CIRCL fetch/parse; typed errors, no silent fallback |
| `app/consensus.py` | ✅ | **Pure** consensus function (unit-tested) |
| `app/ingestion.py` | ✅ | Ingestion worker (fetch → store → consensus) |
| `app/ttl.py` | ✅ | TTL worker: purge raw rows past TTL + write rollup summary |
| `app/main.py` | ✅ | FastAPI serving layer (`GET /v1/security/cve`) |
| `migrations/` | ✅ | `migrate.py` runner + `001_init.sql` schema |
| `tests/` | ✅ | `test_consensus.py`, `test_api.py`, `test_sla.py` |
| `docs/` | 📄 | `report.md` + the original assignment materials |

## Prerequisites

- **Python 3.11+** (developed on 3.13; use the `py` launcher on Windows).
- **PostgreSQL 16** running locally on `localhost:5432` (any standard install;
  the dev machine uses the portable binaries at `D:\PostgreSQL`, registered
  as Windows service `postgresql-16-D`).

## Setup

```powershell
# 1. From the repo root, create a virtual environment and install deps.
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Configure DB credentials.
copy .env.example .env
#   then edit .env and set DB_PASSWORD (and DB_NAME if not 'cve_intel').
```

> All commands below use `.venv\Scripts\python.exe`. On macOS/Linux use
> `.venv/bin/python` and `python3 -m venv .venv`.

## Run (exact commands)

```powershell
# 1. Create the database and apply the schema (idempotent).
.venv\Scripts\python.exe -m migrations.migrate

# 2. Run one ingestion pass over the watchlist (hits NVD + CIRCL live).
#    Takes ~1 min: it paces requests to respect NVD's public rate limit.
.venv\Scripts\python.exe -m app.ingestion

#    Or run it continuously (one pass every INGEST_INTERVAL_SECONDS, default 900):
.venv\Scripts\python.exe -m app.ingestion --loop

# 3. Start the API.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. Query it (in another shell).
curl "http://127.0.0.1:8000/v1/security/cve?cve=CVE-2021-44228"

# 5. Run the TTL/lifecycle worker (purge expired raw rows + write rollups).
.venv\Scripts\python.exe -m app.ttl

# 6. Run the full test suite.
.venv\Scripts\python.exe -m pytest
```

## Design notes

- **No silent fallbacks.** Any source timeout / 5xx / rate-limit / bad-schema is
  written to `ingestion_errors` (and a failed `raw_ingests` row); the run
  continues and never substitutes stale data as fresh.
- **Consensus.** If both sources agree within a CVSS tolerance of `1.0`, the
  score is a reliability-weighted average (NVD 0.6 / CIRCL 0.4) and the result is
  `verified`. Beyond tolerance it is flagged as an outlier disagreement, the
  conservative (higher) score is used, and confidence drops. A single source
  still produces a row but is never presented as two-source verified.
- **Freshness.** `age_seconds` is measured from `consensus_computed_at`; the API
  sets `stale: true` and adds a warning when `age_seconds > ttl_seconds` (3600).
- **API reads only Postgres.** It never calls NVD/CIRCL per request — that keeps
  latency low (p95 < 200ms, asserted in `test_sla.py`).
- **CIRCL note.** CIRCL migrated its API backend to the **cvelistv5** format,
  where the CVSS score often lives in an **ADP** container (CISA/NVD-supplemented
  data) rather than the CNA; `app/sources.py` scans both, and also still handles
  the classic flat format. A minority of CVEs have no CVSS in CIRCL at all, and
  CIRCL occasionally returns **HTTP 429** (rate limit) — in both cases the fetch
  is recorded as a real ingestion error and the CVE degrades to a single-source
  (unverified) consensus. That is the correct, spec-compliant behavior (no silent
  fallback), and is exactly the failover path exercised by the tests.

## Known limitations

- The reported `rate_limit` in the payload is advertised metadata; the API does
  not itself throttle callers (out of assignment scope).
- The TTL worker is a one-shot process (`py -m app.ttl`); run it from a
  scheduler/cron (or alongside `app.ingestion --loop`) for continuous purging.
