r"""Render docs/report.pdf (two pages) from the report content.

Run:  .venv\Scripts\python.exe docs/build_report_pdf.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer)

OUT = Path(__file__).with_name("report.pdf")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=15, spaceAfter=4, spaceBefore=0)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=6, spaceAfter=3)
BODY = ParagraphStyle("Body", parent=ss["Normal"], fontSize=9.6, leading=12.4,
                      spaceAfter=4)
META = ParagraphStyle("Meta", parent=BODY, textColor="#444444", spaceAfter=6)
BUL = ParagraphStyle("Bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2)
CODE = ParagraphStyle("Code", parent=ss["Code"], fontSize=7.6, leading=9.2,
                      leftIndent=6, spaceBefore=2, spaceAfter=4)

DIAGRAM = """\
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
 request_log                            TTL worker (purge + rollup)"""


def P(t, s=BODY):
    return Paragraph(t, s)


def B(t):
    return Paragraph(t, BUL, bulletText="•")


story = [
    P("AI4012 — Assignment #1 Report", H1),
    P("<b>Student:</b> 23i-2578 &nbsp;&nbsp;|&nbsp;&nbsp; <b>Task:</b> #23 Cybersecurity / "
      "Vulnerability — CVE intelligence (<font face='Courier'>GET /v1/security/cve</font>)", META),

    P("Section 1 — System Architecture", H2),
    P("The system answers one question for an AI agent: <i>“What is the current, trusted "
      "information about this CVE?”</i> It is a Python 3 / FastAPI / PostgreSQL pipeline "
      "with four moving parts."),

    P("<b>1. Ingestion (the collector).</b> A worker (<font face='Courier'>app/ingestion.py</font>) "
      "walks a watchlist of 11 well-known CVEs, once or continuously "
      "(<font face='Courier'>--loop</font>, every 15 min). For each one it calls <b>two independent "
      "providers</b>: NIST’s <b>NVD</b> API and Europe’s <b>CIRCL</b> CVE service. Every raw "
      "response is saved verbatim to the <font face='Courier'>raw_ingests</font> table (JSONB) as an "
      "audit trail. If a provider times out, is rate-limited (HTTP 429), returns a server error "
      "(HTTP 503) or unparseable data, that failure is written explicitly to "
      "<font face='Courier'>ingestion_errors</font> and the run continues. Old data is never quietly "
      "reused and a failed fetch is never presented as a success."),

    P("<b>2. Consensus (the judge).</b> A <b>pure function</b> (<font face='Courier'>app/consensus.py</font>) "
      "turns the providers’ answers into one. If both agree on the CVSS score within a tolerance of "
      "1.0, it takes a reliability-weighted average (NVD 0.6 / CIRCL 0.4) and marks the result "
      "<b>verified</b> with confidence 0.97. If they disagree by more, it flags an outlier "
      "disagreement, keeps the more cautious (higher) score and drops confidence to 0.5. If only one "
      "provider answered, an answer is still recorded but clearly marked <i>not</i> two-source "
      "verified. A <b>quality score</b> reflects how complete each provider’s record was. The "
      "single result is upserted into the <font face='Courier'>consensus</font> table."),

    P("<b>3. Storage &amp; lifecycle (the memory).</b> Everything lives in <b>PostgreSQL</b> with "
      "indexed <b>JSONB</b> columns (GIN indexes on payloads and affected products, B-tree on "
      "CVE id / source / fetch time). A <b>TTL worker</b> (<font face='Courier'>app/ttl.py</font>) "
      "deletes raw rows older than 3600 s, but first writes one <b>rollup summary row</b> per "
      "CVE/source recording what was purged, so history is summarised rather than lost. It also "
      "marks aged consensus rows as stale."),

    P("<b>4. Serving (the counter).</b> A <b>FastAPI</b> service (<font face='Courier'>app/main.py</font>) "
      "exposes <font face='Courier'>GET /v1/security/cve?cve=&lt;ID&gt;</font>. It reads <b>only from "
      "Postgres</b>, never calling providers during a request, which keeps p95 latency well under "
      "200 ms. It returns the standardised envelope: <font face='Courier'>data</font> plus "
      "<font face='Courier'>meta</font> with request id, product id/version, provenance (source, "
      "publisher, retrieved_at), freshness (age vs. TTL, stale flag), trust (confidence, quality, "
      "verified), license, api latency/rate-limit and warnings. Unknown CVEs get a clean 404 error "
      "object; every request is logged to <font face='Courier'>request_log</font>."),

    P("<b>Data flow:</b> NVD + CIRCL → raw_ingests → consensus() → consensus table "
      "→ API → agent, with ingestion_errors, request_log and TTL rollups guarding integrity "
      "and lifecycle."),
    Preformatted(DIAGRAM, CODE),

    P("<b>Testing (16 automated tests, all passing).</b> Unit tests for the pure consensus "
      "function (agreement, disagreement, single source, total failure, severity bands, quality). "
      "API tests for exact schema shape, verified/unverified, fresh/stale and 404. SLA tests: "
      "p95 latency &lt; 200 ms over 100 requests, sustained-polling availability (100 % 200s), and "
      "source failover (consensus recomputed and served as degraded when one provider fails)."),

    PageBreak(),

    P("Section 2 — Use of AI (models and prompts)", H2),
    P("<b>Tool:</b> Claude Code (Anthropic’s agentic CLI), September 2026.<br/>"
      "<b>Models:</b> Claude Opus 4.8 (<font face='Courier'>claude-opus-4-8</font>) for the initial "
      "build; Claude Opus 5 (<font face='Courier'>claude-opus-5</font>) for the final verification pass."),

    P("<b>How it was used.</b> I used Claude Code as a pair-programming assistant working against "
      "my written specification. It generated the module layout "
      "(<font face='Courier'>app/</font>, <font face='Courier'>migrations/</font>, "
      "<font face='Courier'>tests/</font>), the two source adapters, the pure consensus function, "
      "the FastAPI serving layer, the migration runner and the pytest suite. It also executed the "
      "code in my environment (migration, live ingestion, API, TTL worker, tests) and iterated on "
      "failures it observed — most notably rewriting the CIRCL parser after the live API turned "
      "out to use the cvelistv5 format with CVSS in an ADP container. I made the design decisions "
      "(consensus weighting, tolerance, schema, TTL strategy, watchlist) and approved each step."),

    P("<b>Representative prompts.</b>"),
    B("“Build a production-style data sourcing, consensus, and serving pipeline for CVE "
      "intelligence — Python 3.11+, FastAPI, psycopg2, pytest, Postgres. Ingest from NVD and "
      "CIRCL, compute cross-source consensus with confidence/quality scores, store raw + consensus "
      "in Postgres JSONB with a 3600 s TTL, and serve the exact standardized JSON envelope. No "
      "silent fallbacks; the API reads only from Postgres.” (followed by the full response "
      "schema from the brief)"),
    B("“The CIRCL source is failing with bad_schema on every CVE — inspect what the CIRCL "
      "API actually returns now and fix the parser so real two-source consensus works.”"),
    B("“Run everything yourself: migration, ingestion, start the API and query it, run the "
      "TTL worker, and run the full test suite — and show me the logs.”"),
    B("“See what this assignment is about and make sure nothing is left aside — tell me "
      "what remains and run the things for me.” This final pass found that ingestion was "
      "one-shot although the brief says <i>continuously</i> fetch, so a "
      "<font face='Courier'>--loop</font> mode was added; it also set up PostgreSQL 16 natively "
      "and re-ran the whole pipeline and test suite end to end."),

    P("<b>What I verified and changed myself.</b>"),
    B("Confirmed all 16 tests pass and the served payload matches the required schema exactly "
      "(data + meta: provenance, freshness, trust, license, api, warnings)."),
    B("Verified real two-source consensus on live data (8/11 CVEs on the final run) and that "
      "single-source cases (CIRCL HTTP 429) are recorded in ingestion_errors and marked "
      "verified: false. During one run NVD itself returned HTTP 503 for every CVE; every failure "
      "was propagated explicitly and the API kept serving clearly-labelled single-source data "
      "— no silent fallback."),
    B("Verified the TTL worker purges expired raw rows, writes rollup summaries and marks "
      "consensus stale (22 rows purged → 22 rollups, 11 consensus rows stale, checked in the DB)."),
    B("Chose the consensus parameters (weights 0.6/0.4, CVSS tolerance 1.0, verified-confidence "
      "threshold 0.9) and the watchlist of real CVEs."),

    P("<b>Limits of the AI’s contribution.</b> The AI did not choose the task, the providers "
      "or the trust model; it did not have access to the internet beyond the two public APIs the "
      "code calls. All generated code was read and run by me before submission."),
]

doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                        topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                        title="AI4012 Assignment 1 Report - 23i-2578", author="23i-2578")
doc.build(story)
print(f"wrote {OUT}")
