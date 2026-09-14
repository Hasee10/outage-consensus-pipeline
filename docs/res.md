# Run Results — 14 September 2026

End-to-end verification of the CVE intelligence pipeline on the submission
machine (Windows 10, Python 3.13, PostgreSQL 16.10 native on `D:`).
Commands are the ones listed in `README.md`, run in order.

## 1. Migration

```
py -m migrations.migrate
```

| Step | Result |
|------|--------|
| Create database `cve_intel` | created |
| Apply `001_init.sql` (4 tables, 7 indexes incl. 2 GIN) | applied |

## 2. Live ingestion (`py -m app.ingestion`)

11 watchlist CVEs, both providers queried live.

| CVE | Name | Severity | CVSS | Confidence | Quality | Sources | Verified |
|-----|------|----------|------|-----------:|--------:|--------:|:--------:|
| CVE-2021-44228 | Log4Shell | critical | 10.0 | 0.97 | 1.0 | 2 | yes |
| CVE-2021-45046 | Log4j follow-up | critical | 9.0 | 0.97 | 1.0 | 2 | yes |
| CVE-2014-6271 | Shellshock | critical | 9.8 | 0.97 | 0.9 | 2 | yes |
| CVE-2019-0708 | BlueKeep | critical | 9.8 | 0.97 | 1.0 | 2 | yes |
| CVE-2022-22965 | Spring4Shell | critical | 9.8 | 0.97 | 1.0 | 2 | yes |
| CVE-2017-0144 | EternalBlue | high | 8.8 | 0.97 | 1.0 | 2 | yes |
| CVE-2021-34527 | PrintNightmare | high | 8.8 | 0.97 | 1.0 | 2 | yes |
| CVE-2023-4863 | libwebp | high | 8.8 | 0.97 | 1.0 | 2 | yes |
| CVE-2014-0160 | Heartbleed | high | 7.5 | 0.97 | 0.9 | 2 | yes |
| CVE-2023-44487 | HTTP/2 Rapid Reset | high | 7.5 | 0.97 | 1.0 | 2 | yes |
| CVE-2020-1472 | Zerologon | medium | 5.5 | 0.97 | 1.0 | 2 | yes |

Summary: `processed 11 · consensus_written 11 · no_consensus 0 · errors 0`.
Quality 0.9 on two rows: CIRCL carries no product list for those older CVEs.

### Earlier runs the same day (kept for the record)

| Run | NVD | CIRCL | Outcome |
|-----|-----|-------|---------|
| 1 | HTTP 503 on all 11 | 6 ok, 5 × HTTP 429 | 6 single-source rows (conf 0.5, unverified), 5 no consensus, 16 errors logged |
| 2 | 11 ok | 8 ok, 3 × HTTP 429 | 8 verified, 3 single-source, 3 errors logged |
| 3 (final) | 11 ok | 11 ok | 11 verified, 0 errors |

Every provider failure was written to `ingestion_errors` and surfaced as a
lower-confidence row with an explicit warning — no silent fallback.

## 3. API (`py -m uvicorn app.main:app`)

| Request | Status | Notes |
|---------|--------|-------|
| `GET /v1/security/cve?cve=CVE-2021-44228` | 200 | cvss 10.0 · age 114 s · stale false · verified true · latency 73 ms · warnings [] |
| `GET /v1/security/cve?cve=CVE-1999-0001` | 404 | `{"error":{"code":"cve_not_found",…},"meta":{…}}` |

Response envelope matched the assignment's Section 3 schema exactly
(`data` + `meta.request_id/product_id/version/served_at/source_last_updated_at/freshness/provenance/trust/license/api/warnings`).

## 4. TTL / lifecycle worker (`py -m app.ttl`)

| Run | TTL | Rollups written | Rows purged | Consensus marked stale |
|-----|----:|----------------:|------------:|-----------------------:|
| Demonstration | 1 s | 22 | 22 | 11 |
| Final (normal) | 3600 s | 0 | 0 | 0 |

The 1-second run proves the mechanism: each purged (CVE, source) group left one
`*:SUMMARY` rollup row. The final run purged nothing because all raw rows were
younger than the TTL, and the fresh ingestion had reset `stale` to false.

## 5. Test suite (`py -m pytest -v`)

**16 passed, 0 failed, 0 skipped — 2.80 s**

| File | Tests | Covers |
|------|------:|--------|
| `tests/test_consensus.py` | 6 | agreement, disagreement, single source, total failure, severity bands, quality |
| `tests/test_api.py` | 6 | exact schema shape, verified, 404, stale, fresh, single-source |
| `tests/test_sla.py` | 4 | p95 < 200 ms (60 req), failover ×2, 200-request availability |

## 6. Database state after the run

### `raw_ingests` (audit trail)

| source_id | success | rows | payload size |
|-----------|---------|-----:|-------------:|
| NVD | true | 11 | 178 kB |
| CIRCL | true | 11 | 90 kB |
| NVD:SUMMARY | true | 11 | 2.4 kB |
| CIRCL:SUMMARY | true | 11 | 2.4 kB |

### `ingestion_errors` (all runs today)

| source | error_type | count |
|--------|-----------|------:|
| CIRCL | rate_limited | 3 |

(Run-1 NVD 503 errors were on the earlier Docker database, since replaced.)

### `request_log`

| status | requests | avg latency | max latency |
|-------:|---------:|------------:|------------:|
| 200 | 534 | 2 ms | 101 ms |
| 404 | 3 | 35 ms | 55 ms |

## Environment

- PostgreSQL 16.10, portable binaries at `D:\PostgreSQL\16`, Windows service `postgresql-16-D`
- Python 3.13 virtualenv, packages pinned in `requirements.txt`
- No containers used
