r"""Render docs/report.pdf (architecture, AI use, results appendix).

Run:  .venv\Scripts\python.exe docs/build_report_pdf.py
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = Path(__file__).with_name("report.pdf")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontSize=15, spaceAfter=4, spaceBefore=0)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=6, spaceAfter=3)
BODY = ParagraphStyle("Body", parent=ss["Normal"], fontSize=9.4, leading=12.0, spaceAfter=4)
META = ParagraphStyle("Meta", parent=BODY, textColor="#444444", spaceAfter=6)
BUL = ParagraphStyle("Bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2)
CODE = ParagraphStyle("Code", parent=ss["Code"], fontSize=7.2, leading=8.8, leftIndent=4,
                      spaceBefore=2, spaceAfter=4)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=8.2, leading=10.0, spaceAfter=0)
CELLH = ParagraphStyle("CellH", parent=CELL, fontName="Helvetica-Bold")

C = "<font face='Courier'>%s</font>"


def P(t, s=BODY):
    return Paragraph(t, s)


def B(t):
    return Paragraph(t, BUL, bulletText="•")


def T(rows, widths):
    data = [[Paragraph(c, CELLH) for c in rows[0]]] + \
           [[Paragraph(str(c), CELL) for c in r] for r in rows[1:]]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.black),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


DIAGRAM = """\
 Oncor -+  CPS -+  Austin -+  TNMP -+       outage-pro -+  outage.online -+  usoutage -+
        +-------+---------+--------+--- ingestion -----+-----------------+-----------+
                 | raw_ingests (JSONB, audit)      | ingestion_errors (explicit failures)
                 v
         compute_consensus() [pure]  -->  consensus_snapshots  -->  API  -->  agent
                                                |          ^
                                TTL worker: hourly rollups + purge + stale"""

SOURCES = [
    ["Source", "Kind", "What it gives"],
    ["Oncor, CPS Energy, Austin Energy, TNMP (Kubra Storm Center JSON)", "utility, first-hand",
     "customers out by county, customers tracked, restoration ETA, own timestamp"],
    ["outage-pro.com, outage.online, usoutage.com (HTML, scraped)", "aggregator",
     "customers out for all 254 counties and for 5–68 utilities"],
]
RUN = [
    ["Source", "Result", "Customers out", "Areas", "Stamp (UTC)"],
    ["ONCOR", "ok", "548", "7 counties", "07:10:00"], ["CPS", "ok", "1", "1", "07:13:35"],
    ["AUSTIN_ENERGY", "ok", "0", "0 (no outages)", "07:16:34"], ["TNMP", "ok", "28", "2", "07:12:31"],
    ["OUTAGE_PRO", "ok", "4,129", "254", "07:11:00"], ["OUTAGE_ONLINE", "ok", "11,606", "262", "—"],
    ["USOUTAGE", "ok", "12,478", "261", "06:27:05"],
]
CONS = [
    ["County", "Reports (source → customers out)", "Result", "Conf.", "Verified", "Why"],
    ["Harris", "outage-pro 217 · outage.online 2,482 · usoutage 2,669", "2,576", "0.92", "yes",
     "majority cluster; outage-pro rejected"],
    ["Williamson", "Oncor 400 · outage-pro 400 · outage.online 121 · usoutage 121", "400", "0.93", "yes",
     "2-vs-2 split resolved by first-hand vote"],
    ["Denton", "Oncor 28 · usoutage 155 · outage.online 154", "154", "0.97", "yes",
     "lower first-hand = partial count, not outlier"],
    ["Montgomery", "three aggregators, none within tolerance", "630", "0.50", "no",
     "total disagreement → conservative claim"],
    ["Oncor (utility)", "Oncor 798 · outage-pro 798 · outage.online 1,593 · usoutage 2,017", "798", "0.93", "yes",
     "laggards rejected as outliers"],
]
API = [
    ["Request", "Status", "Notes"],
    ["GET /v1/energy/outages?region=TX&amp;limit=8", "200",
     "total 11,539 · age 5 s · stale false · conf 0.88 · quality 0.97 · verified · latency 66 ms · 7 provenance entries"],
    ["GET /v1/energy/outages?region=TX&amp;area=Williamson", "200", "one row: 400 customers, 4 sources, conf 0.93"],
    ["GET /v1/energy/outages?region=CA", "404", "{\"error\":{\"code\":\"region_not_tracked\",…},\"meta\":{…}}"],
]
TTL = [
    ["Run", "Raw / snapshot TTL", "Rollup rows", "Snapshots purged", "Raw purged", "Marked stale"],
    ["Normal (6 h / 24 h)", "21600 s / 86400 s", "0", "0", "0", "0"],
    ["Demonstration", "60 s / 60 s", "133", "1", "0", "0"],
]
TESTS = [
    ["File", "Tests", "Covers"],
    ["tests/test_consensus.py", "12", "clustering, outlier rejection, first-hand tiebreak, floor, single source, total disagreement, failed-source warnings, quality"],
    ["tests/test_sources.py", "13", "every parser on captured fixtures; home-county fallback; bad-schema errors; county-name unification"],
    ["tests/test_api.py", "9", "exact envelope, filters, both 404 shapes, stale/fresh, unverified served honestly, rate limit 429"],
    ["tests/test_sla.py", "7", "p95 &lt; 200 ms (60 req), 200-request availability, failover ×3, TTL rollup+purge, stale marking"],
]
DBSTATE = [
    ["Table", "Contents after the final run"],
    ["raw_ingests", "7 rows (latest pass): utility JSON verbatim (1.6–58 kB); aggregator pages raw (gzip) + extracted data (~60 kB)"],
    ["consensus_snapshots / area_rollups", "1 fresh snapshot; 276 rollup rows over 143 counties (peak 2,576 in Harris)"],
    ["ingestion_errors / request_log", "0 errors; 2,149 × HTTP 200 (avg 2 ms), 19 × HTTP 404 (avg 23 ms)"],
]

story = [
    P("AI4012 — Assignment #1 Report", H1),
    P("<b>Student:</b> Haseeb Arshad (23i-2578) &nbsp;&nbsp;|&nbsp;&nbsp; <b>Task:</b> #81 Energy &amp; Utilities / "
      "Grid — Outage status (" + C % "GET /v1/energy/outages" + ", " + C % '{"region":"TX"}' + ")", META),

    P("Section 1 — System Architecture", H2),
    P("The system answers one question for an AI agent: <i>“Who is without power in Texas right now, "
      "where, how many, when is it coming back — and can I trust the number?”</i> It is a Python / "
      "FastAPI / PostgreSQL pipeline with four moving parts."),
    P("<b>1. Ingestion (the collector).</b> A worker (" + C % "app/ingestion.py" + ") runs once or "
      "continuously (" + C % "--loop" + ", every 5 min). Each pass asks <b>seven independent online "
      "sources</b>:"),
    T(SOURCES, [6.2 * cm, 2.8 * cm, 8.0 * cm]),
    P("Every reply is saved to " + C % "raw_ingests" + " (JSONB) as an audit trail — JSON verbatim, HTML "
      "as the raw page (compressed) plus the extracted numbers. A timeout, block, server error or unrecognised "
      "page is written explicitly to " + C % "ingestion_errors" + " and named in the snapshot’s warnings. "
      "Old data is never reused quietly and a failed fetch is never presented as a success."),
    P("<b>2. Consensus (the judge).</b> A <b>pure function</b> (" + C % "app/consensus.py" + ") turns the "
      "sources’ numbers into one answer per county and per utility. Every reported value defines a "
      "candidate cluster (values within max(25, 20 %) of it); the cluster carrying the most reliability "
      "weight wins (utility feeds 0.55, aggregators 0.15), its members are averaged by weight and the "
      "rest are rejected as outliers. Confidence starts at 0.90–0.97 by in-cluster spread and falls in "
      "proportion to the weight rejected; <b>verified</b> needs ≥ 2 agreeing sources, confidence ≥ 0.85 "
      "and a weight majority. A lone source, or nobody agreeing, yields the most conservative (highest) "
      "claim at confidence 0.5, never verified. A utility’s own county figure votes as one first-hand "
      "value, breaks 2-vs-2 splits, supplies the restoration <b>ETA</b>, and is a <b>floor</b>: we never "
      "serve fewer customers out than the utility itself admits. A <b>quality score</b> records how "
      "complete each source’s record was."),
    P("<b>3. Storage &amp; lifecycle (the memory).</b> <b>PostgreSQL</b> with indexed <b>JSONB</b>: "
      + C % "raw_ingests" + " (GIN on payload), " + C % "consensus_snapshots" + " (one row per pass; areas "
      "and utilities as JSONB with GIN indexes; provenance on the row so it survives purging), "
      + C % "area_rollups" + ", " + C % "ingestion_errors" + ", " + C % "request_log" + ". A <b>TTL "
      "worker</b> (" + C % "app/ttl.py" + ") summarises snapshots older than 24 h into hourly per-county "
      "rollups (samples, max, avg, min confidence) before deleting them, purges raw payloads older than "
      "6 h, and flags snapshots older than the 15-minute freshness TTL as stale."),
    P("<b>4. Serving (the counter).</b> <b>FastAPI</b> (" + C % "app/main.py" + ") exposes "
      + C % "GET /v1/energy/outages?region=TX" + " (optional " + C % "area" + ", " + C % "min_customers"
      + ", " + C % "limit" + "). It reads <b>only the newest snapshot from Postgres</b> — never a live "
      "provider — so p95 latency stays far under 200 ms. It returns the standardised envelope: "
      + C % "data" + " (outages by county with customers affected, tracked, ETA, sources, confidence; "
      "utility-level figures; total; as_of) and " + C % "meta" + " (request id, product id, freshness "
      "with age/TTL/stale, provenance, trust, license, api, warnings). Unknown regions get a clean 404 "
      "error object; the advertised rate limit (100 / 60 s per client) is enforced with a clean 429. "
      "Every request is logged."),
    Preformatted(DIAGRAM, CODE),

    PageBreak(),

    P("Section 2 — Use of AI (models and prompts)", H2),
    P("<b>Tool:</b> Claude Code (Anthropic’s agentic CLI), September 2026. &nbsp; <b>Model:</b> Claude "
      "Opus 5 (" + C % "claude-opus-5" + ")."),
    P("<b>How it was used.</b> I used Claude Code as a pair-programming assistant against my written "
      "specification and the catalog row for task #81. It probed candidate online sources with me "
      "(PowerOutage.us blocks automated access and FEMA’s EAGLE-I layer needs a token, so both were "
      "dropped; the four Kubra utility feeds and the three aggregator pages were confirmed live and "
      "keyless), then generated the module layout, the seven source adapters, the pure consensus "
      "function, the schema, the FastAPI layer, the TTL worker, the pytest suite and the GitHub Actions "
      "workflows. It ran everything in my environment — migration, live ingestion, the API, the TTL "
      "worker, the tests — and iterated on what the real data showed: the first consensus rule "
      "(median-based) collapsed on 2-vs-2 splits and had no tiebreaker when two aggregators disagreed, "
      "so it was replaced with weighted clustering plus the utility first-hand vote and floor; a third "
      "county-level aggregator was added after the first run showed only two; the outage-pro parser was "
      "fixed when rows with “&lt;0.01 %” were found to be dropped. I directed the design decisions "
      "(sources, weights, tolerances, floor/ETA rules, TTLs, schema) and approved each step."),
    P("<b>Representative prompts.</b>"),
    B("“Above is the API I chose (#81, GET /v1/energy/outages, region TX) — redo everything in here "
      "please and make sure nothing is looked over in any manner at all.”"),
    B("“Add more sources if you can please but using the exact API or task I want in here.”"),
    B("“Make a GitHub workflow inside GitHub Actions for this repo if necessary since we are working "
      "with the API in a minute-wise manner.”"),
    B("“Run the TTL worker once more and add the above results in a structured manner in res.md.”"),
    P("<b>What I verified and changed myself.</b>"),
    B("Confirmed all 40 tests pass and the served payload matches the required envelope exactly "
      "(data + meta: provenance, freshness, trust, license, api, warnings), including both 404 shapes."),
    B("Verified live consensus on real data: Williamson County reported as 400 by Oncor’s own map and "
      "outage-pro but 121 by two lagging aggregators — the first-hand vote resolved it to 400, verified, "
      "with the laggards listed as rejected; Harris County resolved by majority. Overall snapshot "
      "confidence 0.88, verified, quality 0.97."),
    B("Verified the TTL worker rolls old snapshots into " + C % "area_rollups" + " (133 rows from one "
      "snapshot), purges raw rows and marks stale snapshots, with before/after row counts."),
    B("Chose the consensus parameters (weights 0.55/0.15, tolerance max(25, 20 %), verified threshold "
      "0.85, freshness TTL 15 min, raw TTL 6 h, snapshot TTL 24 h) and the seven-source lineup."),
    P("<b>Limits of the AI’s contribution.</b> The AI did not choose the task, the trust model or the "
      "final source lineup; it had no access to the internet beyond the public pages the code fetches. "
      "All generated code was read and run by me before submission."),

    PageBreak(),

    P("Appendix — Results of the final end-to-end run (15 Sep 2026)", H2),
    P("Environment: Windows 10, Python 3.12, PostgreSQL 16.10 (native). Commands as in README.md, run "
      "in order. Full detail in " + C % "docs/res.md" + "."),
    P("<b>A1. Live ingestion</b> — all seven sources, ~15 s. Snapshot: total 11,539 customers affected, "
      "confidence 0.88, quality 0.97, verified. 0 source errors."),
    T(RUN, [3.2 * cm, 1.5 * cm, 2.6 * cm, 3.6 * cm, 2.6 * cm]),
    Spacer(1, 5),
    P("<b>A2. Consensus in action</b> — real rows from this pass."),
    T(CONS, [2.4 * cm, 6.0 * cm, 1.4 * cm, 1.2 * cm, 1.5 * cm, 4.5 * cm]),
    Spacer(1, 5),
    P("<b>A3. API.</b> Envelope matched the Section 3 schema exactly."),
    T(API, [6.4 * cm, 1.4 * cm, 9.2 * cm]),
    Spacer(1, 5),
    P("<b>A4. TTL / lifecycle worker.</b> The demonstration run summarised one snapshot into 133 hourly "
      "per-county rollup rows (276 rows over 143 counties in total) and deleted it; the normal run "
      "purged nothing because everything was younger than its TTL."),
    T(TTL, [3.4 * cm, 3.4 * cm, 2.2 * cm, 3.0 * cm, 2.2 * cm, 2.6 * cm]),
    Spacer(1, 5),
    P("<b>A5. Test suite</b> — 41 passed, 0 failed, 0 skipped, 4.4 s."),
    T(TESTS, [4.2 * cm, 1.3 * cm, 11.5 * cm]),
    Spacer(1, 5),
    P("<b>A6. Database state after the run.</b>"),
    T(DBSTATE, [4.6 * cm, 12.4 * cm]),
]

doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                        title="AI4012 Assignment 1 Report - 23i-2578", author="Haseeb Arshad")
doc.build(story)
print(f"wrote {OUT}")
