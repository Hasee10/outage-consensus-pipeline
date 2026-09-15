# Assignment #1 — Report

**Student:** Haseeb Arshad (23i-2578)
**Task:** #81 Energy & Utilities / Grid — Outage status (`GET /v1/energy/outages`, `{"region":"TX"}`)

---

## Section 1 — System Architecture (one page, plain terms)

The system answers one question for an AI agent: *"Who is without power in
Texas right now, where, how many, when is it coming back — and can I trust the
number?"* It does this in four moving parts.

**1. Ingestion (the collector).** A worker (`app/ingestion.py`) runs once or
continuously (`--loop`, every 5 minutes). Each pass asks **seven independent
online sources**: four electric utilities' own outage maps — **Oncor**, **CPS
Energy**, **Austin Energy**, **TNMP** (Kubra Storm Center JSON: customers out
by county plus restoration estimates) — and three aggregator sites that read
every Texas utility's map — **outage-pro.com**, **outage.online**,
**usoutage.com** (HTML, scraped). Every reply is saved to `raw_ingests`
(JSONB) as an audit trail: JSON verbatim, HTML as the extracted numbers plus a
page fingerprint. If a source times out, is blocked, returns a server error or
sends a page the parser does not recognise, that failure is written explicitly
to `ingestion_errors` and named in the snapshot's warnings. We never reuse old
data quietly or present a failed fetch as a success.

**2. Consensus (the judge).** A **pure function** (`app/consensus.py`) turns
the sources' numbers into one answer per county and per utility. For each
figure, every reported value defines a candidate cluster (values within
max(25, 20 %) of it); the cluster carrying the most reliability weight wins
(utility feeds 0.55, aggregators 0.15), its members are averaged by weight,
and the rest are rejected as outliers. Confidence starts at 0.90–0.97 by the
spread inside the winning cluster and falls in proportion to the weight of
what was rejected; a figure is **verified** only when at least two sources
agree, confidence is ≥ 0.85 and the agreeing sources hold a weight majority. A
lone source, or nobody agreeing, yields the most conservative (highest) claim
at confidence 0.5 and is never verified. A utility's own county figure votes as
one first-hand value, arbitrates 2-vs-2 splits, supplies the restoration
**ETA**, and acts as a **floor**: we never serve fewer customers out than the
utility itself admits. A **quality score** records how complete each source's
record was (timestamp, totals, county breakdown, customers tracked, ETAs).

**3. Storage & lifecycle (the memory).** Everything lives in **PostgreSQL**
with indexed **JSONB**: `raw_ingests` (GIN on payload), `consensus_snapshots`
(one row per pass; areas and utilities as JSONB with GIN indexes; provenance
stored on the row so it survives purging), `area_rollups`, `ingestion_errors`,
`request_log`. A **TTL worker** (`app/ttl.py`) summarises snapshots older than
24 h into hourly per-county rollups (samples, max, average, minimum
confidence) before deleting them, purges raw payloads older than 6 h, and
flags snapshots older than the 15-minute freshness TTL as stale.

**4. Serving (the counter).** A **FastAPI** service (`app/main.py`) exposes
`GET /v1/energy/outages?region=TX` (optional `area`, `min_customers`, `limit`).
It reads **only the newest snapshot from Postgres** — never a live provider —
so p95 latency stays far under 200 ms. It returns the standardized envelope:
`data` (region, outages by county with customers affected, tracked, ETA,
sources, confidence; utility-level figures; total; as_of) and `meta`
(request id, product id, freshness with age/TTL/stale, provenance, trust,
license, api latency and rate limit, warnings). Unknown regions and regions
without a snapshot get a clean 404 error object. Every request is logged.

```
 Oncor ─┐  CPS ─┐  Austin ─┐  TNMP ─┐          outage-pro ─┐ outage.online ─┐ usoutage ─┐
        └───────┴──────────┴────────┴──── ingestion ───────┴────────────────┴───────────┘
                    │ raw_ingests (JSONB, audit)        │ ingestion_errors (explicit)
                    ▼
            consensus() [pure]  →  consensus_snapshots  →  API  →  agent
                                        │        ▲
                        TTL worker: rollups + purge + stale
```

**Data flow:** 7 sources → `raw_ingests` → `compute_consensus()` →
`consensus_snapshots` → API → agent, with `ingestion_errors`, `request_log`
and hourly rollups guarding integrity and lifecycle along the way.

---

## Section 2 — Use of AI (models & prompts)

**AI tools used:**
- Tool: **Claude Code** (Anthropic's agentic CLI), September 2026
- Model: **Claude Opus 5** (`claude-opus-5`)

**How it was used:**
I used Claude Code as a pair-programming assistant against my written
specification and the catalog row for task #81. It probed candidate online
sources with me (PowerOutage.us blocks automated access and FEMA's EAGLE-I
layer needs a token, so both were dropped; the four Kubra utility feeds and the
three aggregator pages were confirmed live and keyless), then generated the
module layout, the seven source adapters, the pure consensus function, the
schema, the FastAPI layer, the TTL worker, the pytest suite and the GitHub
Actions workflows. It ran everything in my environment — migration, live
ingestion, the API, the TTL worker, the tests — and iterated on what the real
data showed: the first consensus rule (median-based) collapsed on 2-vs-2
splits and had no tiebreaker when two aggregators disagreed, so we replaced it
with weighted clustering and added the utility first-hand vote and floor; a
third county-level aggregator was added after the first run showed only two.
I directed the design decisions (which sources, weights, tolerances, the
floor/ETA rules, TTLs, schema) and approved each step.

**Representative prompts:**
1. "Above is the API I chose (#81, GET /v1/energy/outages, region TX) — redo
   everything in here please and make sure nothing is looked over in any
   manner at all."
2. "Add more sources if you can please but using the exact API or task I
   want in here."
3. "Make a GitHub workflow inside GitHub Actions for this repo if necessary
   since we are working with the API in a minute-wise manner."
4. "Run the TTL worker once more and add the above results in a structured
   manner in res.md."

**What I verified / changed myself:**
- Confirmed all 38 tests pass and the served payload matches the required
  envelope exactly (data + meta: provenance, freshness, trust, license, api,
  warnings), including the clean 404 shapes.
- Verified live consensus on real data: e.g. Williamson County reported as 610
  by Oncor's own map and outage-pro but 121 by two lagging aggregators — the
  first-hand vote resolved it to 610, verified, with the two laggards listed as
  rejected outliers; Harris County resolved by majority (two aggregators vs.
  one). Overall snapshot confidence 0.90, verified, quality 0.97.
- Verified the TTL worker rolls old snapshots into `area_rollups`, purges raw
  rows and marks stale snapshots (with before/after row counts).
- Chose the consensus parameters (weights 0.55/0.15, tolerance max(25, 20 %),
  verified threshold 0.85, freshness TTL 15 min, raw TTL 6 h, snapshot TTL
  24 h) and the seven-source lineup.
