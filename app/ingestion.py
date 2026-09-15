"""Ingestion worker.

For each region: fetch every source, record every raw payload and every
failure explicitly, compute the consensus snapshot, and store it.

Runnable standalone:  py -m app.ingestion              (one pass)
                      py -m app.ingestion --loop       (continuous, every
                                                        INGEST_INTERVAL_SECONDS)
Also importable:      run_once(regions=[...])
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

import psycopg2.extras

from app import config, consensus, db, sources


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ingest_source(conn, region: str, source_id: str) -> dict:
    """Fetch + parse one source, persisting the raw payload or the error.
    Returns the normalised source-result dict for the consensus function."""
    fetch, parse = sources.ADAPTERS[source_id]
    meta = config.SOURCES[source_id]
    base = {"source_id": source_id, "publisher": meta["publisher"], "kind": meta["kind"],
            "weight": meta["weight"], "retrieved_at": _now_iso()}

    try:
        raw = fetch()
        normalized = parse(raw)
    except sources.SourceError as exc:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw_ingests (region, source_id, payload, success, error_detail) "
                "VALUES (%s, %s, NULL, FALSE, %s)",
                (region, source_id, psycopg2.extras.Json({"type": exc.error_type, "message": exc.message})),
            )
            cur.execute(
                "INSERT INTO ingestion_errors (region, source_id, error_type, error_message) "
                "VALUES (%s, %s, %s, %s)",
                (region, source_id, exc.error_type, exc.message),
            )
        conn.commit()
        print(f"  [{source_id:13}] FAILED ({exc.error_type}): {exc.message}")
        return {**base, "success": False, "utilities": {}, "areas": {}}

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw_ingests (region, source_id, payload, success) VALUES (%s, %s, %s, TRUE)",
            (region, source_id, psycopg2.extras.Json(sources.storable_payload(source_id, raw, normalized))),
        )
    conn.commit()

    out = sum(u["out"] for u in normalized["utilities"].values())
    print(f"  [{source_id:13}] ok  customers_out={out:<6} utilities={len(normalized['utilities']):<3} "
          f"areas={len(normalized['areas']):<4} generated_at={normalized.get('generated_at')}")
    return {**base, "success": True,
            "generated_at": normalized.get("generated_at"),
            "utilities": normalized["utilities"], "areas": normalized["areas"]}


def _insert_snapshot(conn, snap: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO consensus_snapshots
                (region, as_of, total_customers_affected, confidence, quality_score, verified,
                 areas, utilities, sources_used, warnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (snap["region"], snap["as_of"], snap["total_customers_affected"], snap["confidence"],
             snap["quality_score"], snap["verified"],
             psycopg2.extras.Json(snap["areas"]), psycopg2.extras.Json(snap["utilities"]),
             psycopg2.extras.Json(snap["sources_used"]), psycopg2.extras.Json(snap["warnings"])),
        )
    conn.commit()


def run_once(regions: list[str] | None = None) -> dict:
    """Run one ingestion pass. Returns a run summary."""
    regions = regions or list(config.REGIONS)
    summary = {"regions": 0, "snapshots_written": 0, "no_snapshot": 0, "sources_ok": 0, "errors": 0}
    conn = db.connect()
    try:
        for region in regions:
            print(f"[{region}] {config.REGIONS.get(region, region)}")
            results = [_ingest_source(conn, region, sid) for sid in sources.ADAPTERS]
            summary["regions"] += 1
            summary["sources_ok"] += sum(1 for r in results if r["success"])
            summary["errors"] += sum(1 for r in results if not r["success"])

            snap = consensus.compute_consensus(region, results)
            if snap is None:
                summary["no_snapshot"] += 1
                print("  -> no usable source; snapshot NOT written")
                continue
            _insert_snapshot(conn, snap)
            summary["snapshots_written"] += 1
            top = ", ".join(f"{a['area']}={a['customers_affected']}" for a in snap["areas"][:5])
            print(f"  -> snapshot: total={snap['total_customers_affected']} conf={snap['confidence']} "
                  f"quality={snap['quality_score']} verified={snap['verified']} | top: {top}")
            for w in snap["warnings"]:
                print(f"     ! {w}")
    finally:
        conn.close()

    print("\n=== ingestion summary ===")
    print(json.dumps(summary, indent=2))
    return summary


def run_forever(interval_seconds: float) -> None:
    """Continuously re-ingest, sleeping `interval_seconds` between passes.
    A pass that raises (e.g. DB down) is reported and the loop keeps going —
    nothing is ever swallowed silently."""
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
    parser = argparse.ArgumentParser(description="Outage ingestion worker")
    parser.add_argument("--loop", action="store_true", help="run continuously instead of a single pass")
    parser.add_argument("--interval", type=float, default=config.INGEST_INTERVAL_SECONDS,
                        help="seconds between passes in --loop mode")
    args = parser.parse_args()
    if args.loop:
        run_forever(args.interval)
    else:
        run_once()
