# Run Results — 15 September 2026

End-to-end verification of the Texas outage consensus pipeline on the
submission machine (Windows 10, Python 3.12, PostgreSQL 16.10 native on `D:`).
Commands are the ones listed in `README.md`, run in order.

## 1. Migration

```
py -m migrations.migrate
```

| Step | Result |
|------|--------|
| Create database `outage_intel` | created |
| Apply `001_init.sql` (5 tables, 8 indexes incl. 3 GIN) | applied |

## 2. Live ingestion (`py -m app.ingestion`)

All seven sources fetched live; one pass takes ~15 s.

| Source | Kind | Result | Customers out (own view) | Areas | Source timestamp |
|--------|------|--------|-------------------------:|------:|------------------|
| ONCOR | utility | ok | 548 | 7 counties | 07:10:00Z |
| CPS | utility | ok | 1 | 1 | 07:13:35Z |
| AUSTIN_ENERGY | utility | ok | 0 | 0 (no outages → no county layer) | 07:16:34Z |
| TNMP | utility | ok | 28 | 2 | 07:12:31Z |
| OUTAGE_PRO | aggregator | ok | 4,129 | 254 counties | 07:11:00Z |
| OUTAGE_ONLINE | aggregator | ok | 11,606 | 262 | (page carries no stamp) |
| USOUTAGE | aggregator | ok | 12,478 | 261 | 06:27:05Z |

Snapshot: **total 11,539 customers affected · confidence 0.88 · quality 0.97 ·
verified true**. Warnings: 33 areas had an outlier rejected or unresolved
disagreement; 2 areas raised to the utility-reported floor.
Summary: `regions 1 · snapshots_written 1 · sources_ok 7 · errors 0`.

### Consensus in action (real rows from this pass)

| County | Reports (source → customers out) | Result | Conf. | Verified | Why |
|--------|----------------------------------|-------:|------:|:--------:|-----|
| Harris | outage-pro 217 · outage.online 2,482 · usoutage 2,669 | 2,576 | 0.92 | yes | majority cluster; outage-pro rejected as outlier |
| Williamson | **Oncor 400** · outage-pro 400 · outage.online 121 · usoutage 121 | 400 | 0.93 | yes | 2-vs-2 split resolved by the first-hand utility vote |
| Denton | Oncor 28 · usoutage 155 · outage.online 154 | 154 | 0.97 | yes | lower first-hand figure is a partial count, not an outlier |
| Montgomery | three aggregators, none within tolerance | 630 | 0.50 | no | total disagreement → conservative (highest) claim |
| Bexar (earlier pass) | CPS 1 · outage-pro 0 · outage.online 394 · usoutage 405 | 400 | 0.97 | yes | agreeing pair wins; utility ETA 06:15Z attached |

Utility level: Oncor reported 798 by its own map and by outage-pro, 1,593 by
outage.online and 2,017 by usoutage (both lagging) → consensus **798,
confidence 0.93, verified**, laggards listed as rejected.

### Earlier runs the same day

| Run | Change | Outcome |
|-----|--------|---------|
| 1 | 2 aggregators with county data (usoutage counties not yet parsed) | conf 0.51, unverified — no tiebreaker when the two disagreed |
| 2 | utility first-hand vote + floor added | conf 0.54 — 2-vs-2 splits still unresolved by the median rule |
| 3 | usoutage county list parsed (3rd vote) | conf 0.85, verified |
| 4 | median rule → weighted clustering | conf 0.90, verified; Williamson resolved correctly |
| 5 | outage-pro parser fixed for `<0.01%` rows (254/254 counties) | conf 0.88, verified, 0 errors |
| 6 (later that day) | Austin Energy developed outages and, having no county layer, was reported as `bad_schema` — exactly as designed (named in warnings, snapshot still verified from the other six). Fix: single-county utilities (Austin → Travis, CPS → Bexar) now attribute their total to the home county with the ETA from their ZIP/district layer. | 7/7 ok, 40 tests |

No source failed during any pass today, so `ingestion_errors` is empty; the
failover paths are exercised by the test suite instead.

## 3. API (`py -m uvicorn app.main:app`)

| Request | Status | Notes |
|---------|--------|-------|
| `GET /v1/energy/outages?region=TX&limit=8` | 200 | total 11,539 · age 5 s · stale false · verified true · latency 66 ms · 7 provenance entries |
| `GET /v1/energy/outages?region=TX&area=Williamson` | 200 | one row: 400 customers, sources ONCOR + 3 aggregators, conf 0.93 |
| `GET /v1/energy/outages?region=CA` | 404 | `{"error":{"code":"region_not_tracked",…},"meta":{…}}` |

Envelope matched the assignment's Section 3 schema exactly (`data` + `meta`:
request_id, product_id `energy.outages.v1`, version, served_at,
source_last_updated_at, freshness, provenance, trust, license, api, warnings).

## 4. TTL / lifecycle worker (`py -m app.ttl`)

| Run | Raw TTL | Snapshot TTL | Rollup rows | Snapshots purged | Raw purged | Marked stale |
|-----|--------:|-------------:|------------:|-----------------:|-----------:|-------------:|
| Normal (6 h / 24 h / 15 min) | 21600 s | 86400 s | 0 | 0 | 0 | 0 |
| Demonstration | 60 s | 60 s | 133 | 1 | 0 | 0 |

The demonstration run summarised one 2-minute-old snapshot into 133 hourly
per-county rollup rows and deleted it; `area_rollups` now holds 276 rows over
143 counties (peak 2,576 in Harris, 4 samples in the 09:00 hour). The normal
run purged nothing because everything was younger than its TTL.

## 5. Test suite (`py -m pytest -v`)

**40 passed, 0 failed, 0 skipped — 3.9 s**

| File | Tests | Covers |
|------|------:|--------|
| `tests/test_consensus.py` | 12 | clustering rule, outlier rejection, first-hand tiebreak, single source, total disagreement, floor, partial first-hand counts, failed-source warnings, no-snapshot, quality |
| `tests/test_sources.py` | 13 | all four parser families on captured fixtures; home-county fallback; bad-schema errors; county-name unification; storage shape |
| `tests/test_api.py` | 8 | exact envelope, filters, case-insensitive area, both 404 shapes, stale/fresh, unverified served honestly |
| `tests/test_sla.py` | 7 | p95 < 200 ms (60 req), 200-request availability, failover ×3, TTL rollup+purge, stale marking |

## 6. Database state after the run

| Table | Contents |
|-------|----------|
| `raw_ingests` | 7 rows (one per source, latest pass): Oncor JSON 58 kB, CPS 9 kB, TNMP 3 kB, Austin 1.6 kB; aggregators stored as extracted data + page fingerprint (4–6 kB each) |
| `consensus_snapshots` | 1 (newest), `stale = false` |
| `area_rollups` | 276 rows, 143 counties |
| `ingestion_errors` | 0 |
| `request_log` | 2,149 × HTTP 200 (avg 2 ms), 19 × HTTP 404 (avg 23 ms) |

## 7. Live source health (`python scripts/source_health.py`)

All 7 sources healthy; fetch times 0.6–3.8 s each. The same script runs
hourly in GitHub Actions (`.github/workflows/source-health.yml`).

## Environment

- PostgreSQL 16.10, portable binaries at `D:\PostgreSQL\16`, Windows service `postgresql-16-D`
- Python 3.12 virtualenv, packages pinned in `requirements.txt`
- No containers used locally (CI uses GitHub's own Postgres service container)
