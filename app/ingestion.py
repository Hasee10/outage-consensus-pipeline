"""Ingestion worker.

For each CVE in the watchlist: fetch both sources, record every raw payload
and every failure explicitly, compute consensus, and upsert the result.

Runnable standalone:  py -m app.ingestion              (one pass)
                      py -m app.ingestion --loop       (continuous, every
                                                        INGEST_INTERVAL_SECONDS)
Also importable:      run_once(cve_ids=[...])
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import psycopg2.extras

from app import config, consensus, db, sources

# Map source_id -> (fetch fn, parse fn)
_ADAPTERS = {
    "NVD": (sources.fetch_nvd, sources.parse_nvd),
    "CIRCL": (sources.fetch_circl, sources.parse_circl),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ingest_source(conn, cve_id: str, source_id: str) -> dict:
    """Fetch + parse one source, persisting raw payload / error. Returns a
    normalized source-result dict for the consensus function."""
    fetch, parse = _ADAPTERS[source_id]
    retrieved_at = _now_iso()
    publisher = config.SOURCES[source_id]["publisher"]

    try:
        raw = fetch(cve_id)
        normalized = parse(raw)
    except sources.SourceError as exc:
        # Record the raw failure and a structured ingestion error. No retry
        # with stale data, no crash — the run continues.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw_ingests (cve_id, source_id, payload, success, error_detail) "
                "VALUES (%s, %s, %s, FALSE, %s)",
                (
                    cve_id,
                    source_id,
                    None,
                    psycopg2.extras.Json({"type": exc.error_type, "message": exc.message}),
                ),
            )
            cur.execute(
                "INSERT INTO ingestion_errors (cve_id, source_id, error_type, error_message) "
                "VALUES (%s, %s, %s, %s)",
                (cve_id, source_id, exc.error_type, exc.message),
            )
        conn.commit()
        print(f"  [{source_id}] FAILED ({exc.error_type}): {exc.message}")
        return {
            "source_id": source_id,
            "publisher": publisher,
            "success": False,
            "retrieved_at": retrieved_at,
            "cvss": None,
        }

    # Success: store the raw payload for the audit trail.
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_ingests (cve_id, source_id, payload, success) "
            "VALUES (%s, %s, %s, TRUE)",
            (cve_id, source_id, psycopg2.extras.Json(raw)),
        )
    conn.commit()
    print(f"  [{source_id}] ok  cvss={normalized['cvss']} "
          f"products={len(normalized['affected_products'])}")

    result = {
        "source_id": source_id,
        "publisher": publisher,
        "success": True,
        "retrieved_at": retrieved_at,
    }
    result.update(normalized)
    return result


def _upsert_consensus(conn, record: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO consensus
                (cve_id, severity, cvss, affected_products, published_at,
                 consensus_computed_at, confidence, quality_score, sources_used, stale)
            VALUES (%s, %s, %s, %s, %s, now(), %s, %s, %s, FALSE)
            ON CONFLICT (cve_id) DO UPDATE SET
                severity              = EXCLUDED.severity,
                cvss                  = EXCLUDED.cvss,
                affected_products     = EXCLUDED.affected_products,
                published_at          = EXCLUDED.published_at,
                consensus_computed_at = EXCLUDED.consensus_computed_at,
                confidence            = EXCLUDED.confidence,
                quality_score         = EXCLUDED.quality_score,
                sources_used          = EXCLUDED.sources_used,
                stale                 = FALSE
            """,
            (
                record["cve_id"],
                record["severity"],
                record["cvss"],
                psycopg2.extras.Json(record["affected_products"]),
                record["published"],
                record["confidence"],
                record["quality_score"],
                psycopg2.extras.Json(record["sources_used"]),
            ),
        )
    conn.commit()


def run_once(cve_ids: list[str] | None = None) -> dict:
    """Run one ingestion pass over the watchlist. Returns a run summary."""
    cve_ids = cve_ids or config.WATCHLIST
    summary = {"processed": 0, "consensus_written": 0, "no_consensus": 0, "errors": 0}
    conn = db.connect()
    try:
        for i, cve_id in enumerate(cve_ids):
            print(f"[{i + 1}/{len(cve_ids)}] {cve_id}")
            results = [_ingest_source(conn, cve_id, sid) for sid in _ADAPTERS]
            summary["processed"] += 1
            summary["errors"] += sum(1 for r in results if not r["success"])

            record = consensus.compute_consensus(cve_id, results)
            if record is None:
                summary["no_consensus"] += 1
                print(f"  -> no usable source; consensus NOT written")
            else:
                _upsert_consensus(conn, record)
                summary["consensus_written"] += 1
                print(f"  -> consensus: {record['severity']} cvss={record['cvss']} "
                      f"conf={record['confidence']} verified={record['verified']}")

            # Be polite to NVD's public rate limit between CVEs.
            if i < len(cve_ids) - 1 and config.NVD_REQUEST_DELAY_SECONDS > 0:
                time.sleep(config.NVD_REQUEST_DELAY_SECONDS)
    finally:
        conn.close()

    print("\n=== ingestion summary ===")
    print(json.dumps(summary, indent=2))
    return summary


def run_forever(interval_seconds: float) -> None:
    """Continuously re-ingest the watchlist, sleeping `interval_seconds`
    between passes. A pass that raises (e.g. DB down) is reported and the loop
    keeps going — nothing is ever swallowed silently."""
    while True:
        started = time.monotonic()
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 - report, don't hide
            print(f"!!! ingestion pass failed: {type(exc).__name__}: {exc}")
        wait = max(0.0, interval_seconds - (time.monotonic() - started))
        print(f"--- next pass in {wait:.0f}s ---")
        time.sleep(wait)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CVE ingestion worker")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously instead of a single pass")
    parser.add_argument("--interval", type=float,
                        default=config.INGEST_INTERVAL_SECONDS,
                        help="seconds between passes in --loop mode")
    args = parser.parse_args()
    if args.loop:
        run_forever(args.interval)
    else:
        run_once()
