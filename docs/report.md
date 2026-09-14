# Assignment #1 — Report

**Student:** 23i-2578
**Task:** #23 Cybersecurity / Vulnerability — CVE intelligence (`GET /v1/security/cve`)

---

## Section 1 — System Architecture (one page, plain terms)

The system answers one question for an AI agent: *"What is the current, trusted
information about this CVE?"* It does this in four moving parts.

**1. Ingestion (the collector).** A worker (`app/ingestion.py`) walks a
hardcoded watchlist of ~11 well-known CVEs. For each one it calls **two
independent providers** — the U.S. government's **NVD** API and Europe's
**CIRCL** CVE service. Each raw response is saved verbatim into the
`raw_ingests` table (JSONB) as an audit trail. If a provider times out, is rate
limited, returns a server error, or sends data we can't parse, that failure is
written explicitly to the `ingestion_errors` table and the run keeps going. We
never quietly reuse old data or pretend a failed fetch succeeded.

**2. Consensus (the judge).** A **pure function** (`app/consensus.py`) takes the
two providers' numbers and produces one answer. If both providers roughly agree
on the CVSS severity score (within a tolerance of 1.0), it takes a
reliability-weighted average — NVD is weighted more heavily as the authoritative
source — and marks the result **verified** with high confidence. If they
disagree by more than the tolerance, it flags an **outlier disagreement**, keeps
the more cautious (higher) score, and lowers confidence. If only one provider
answered, it still records an answer but clearly marks it as *not* two-source
verified. It also computes a **quality score** from how complete each provider's
data was. The single winning answer is written to the `consensus` table.

**3. Storage & lifecycle (the memory).** Everything lives in **PostgreSQL** with
indexed **JSONB** columns. Because raw micro-data piles up, a **TTL worker**
(`app/ttl.py`) deletes raw rows older than one hour (3600s) but first writes a
small **rollup summary row** recording how many rows it purged — so the history
isn't lost. It also marks aged consensus rows as stale.

**4. Serving (the counter).** A **FastAPI** service (`app/main.py`) exposes
`GET /v1/security/cve?cve=<ID>`. It reads **only from Postgres** — never calling
the live providers during a request, which keeps responses fast (p95 < 200ms).
It returns a standardized envelope with the data plus rich metadata:
provenance (which sources, when retrieved), freshness (age vs. TTL, stale flag),
trust (confidence, quality, verified), and any warnings. Unknown CVEs get a
clean 404. Every request is logged to `request_log`.

**Data flow:** `NVD + CIRCL → raw_ingests → consensus() → consensus table → API → agent`,
with `ingestion_errors`, `request_log`, and TTL rollups guarding integrity and
lifecycle along the way.

```
                +-------- NVD --------+
 watchlist --> ingestion              +--> raw_ingests (JSONB, audit)
                +------- CIRCL -------+          |
                       |                         v
              ingestion_errors            consensus() [pure]
              (explicit failures)                |
                                                 v
   API  <----- reads only Postgres <-------  consensus table
    |                                            ^
    v                                            |
 request_log                            TTL worker (purge + rollup)
```

---

## Section 2 — Use of AI (models & prompts)

**AI tools used:**
- Tool: **Claude Code** (Anthropic's agentic CLI)
- Models: **Claude Opus 4.8** (`claude-opus-4-8`) for the initial build;
  **Claude Opus 5** (`claude-opus-5`) for the final verification pass
- Date used: **September 2026**

**How it was used:**
I used Claude Code as a pair-programming assistant to scaffold and implement the
entire pipeline against my written specification. It generated the module layout
(`app/`, `migrations/`, `tests/`), wrote the initial source adapters, the pure
consensus function, the FastAPI serving layer, the migration runner, and the
pytest suite. It also ran the code in my environment — executing the migration,
the live ingestion pass, the TTL worker, and the tests — and iterated on failures
it observed (most notably fixing the CIRCL parser once the live API returned an
unexpected format). I directed the design decisions (consensus weighting,
tolerance, schema fields, TTL strategy) and approved each step.

**Representative prompts:**
1. "Build a production-style data sourcing, consensus, and serving pipeline for
   CVE intelligence — Python 3.11+, FastAPI, psycopg2, pytest, Postgres. Ingest
   from NVD and CIRCL, compute cross-source consensus with confidence/quality
   scores, store raw + consensus in Postgres JSONB with a 3600s TTL, and serve
   the exact standardized JSON envelope. No silent fallbacks; API reads only from
   Postgres." (full specification, including the required response schema)
2. "The CIRCL source is failing with `bad_schema` on every CVE — inspect what the
   CIRCL API actually returns now and fix the parser so real two-source consensus
   works."
3. "Run everything yourself: migration, ingestion, start the API and query it,
   run the TTL worker, and run the full test suite — and show me the logs."
4. "See what this assignment is about and make sure nothing is left aside —
   tell me what remains and run the things for me." (final pass: this surfaced
   that ingestion was one-shot although the brief says *continuously* fetch, so
   a `--loop` mode was added; it also set up Postgres natively and re-ran the
   full pipeline and test suite end to end.)

**What I verified / changed myself:**
- Confirmed all 16 tests pass and the served payload matches the required schema
  exactly (data + meta: provenance, freshness, trust, license, api, warnings).
- Verified real two-source consensus on live data (8/11 CVEs on the final run),
  and that single-source cases (CIRCL HTTP 429) are recorded in
  `ingestion_errors` and correctly marked `verified: false`. During one run NVD
  itself returned HTTP 503 for every CVE — every failure was propagated
  explicitly and the API kept serving clearly-labelled single-source data —
  i.e. no silent fallback.
- Verified the TTL worker actually purges expired raw rows, writes rollup
  summaries, and marks consensus stale (with before/after DB inspection).
- Chose the consensus parameters (NVD/CIRCL weights 0.6/0.4, CVSS tolerance 1.0,
  verified-confidence threshold) and the watchlist of real CVEs.
