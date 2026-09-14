"""TTL / lifecycle worker.

Purges raw_ingests rows older than TTL_SECONDS and writes ONE rollup summary
row per (cve_id, source_id) capturing what was purged, so the audit trail keeps
a historical marker instead of vanishing. Also marks consensus rows whose
underlying raw data has aged past the TTL as stale.

Runnable standalone:  py -m app.ttl
Importable/testable:  purge_expired(now=..., ttl_seconds=...)
"""
from __future__ import annotations

import json

import psycopg2.extras

from app import config, db


def purge_expired(ttl_seconds: int | None = None) -> dict:
    """Roll up + purge expired raw_ingests. Returns a summary dict.

    A summary row is a raw_ingests row with source_id suffixed ':SUMMARY',
    success=TRUE, and an error_detail-free payload describing the rollup. It is
    itself exempt from purging (we never purge ':SUMMARY' rows)."""
    ttl_seconds = ttl_seconds or config.TTL_SECONDS
    conn = db.connect()
    result = {"rollups_written": 0, "rows_purged": 0, "consensus_marked_stale": 0}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Summarize expired, non-summary rows grouped by cve/source.
            cur.execute(
                """
                SELECT cve_id,
                       source_id,
                       count(*)                AS n,
                       sum((success)::int)     AS n_ok,
                       min(fetched_at)         AS first_at,
                       max(fetched_at)         AS last_at
                FROM raw_ingests
                WHERE fetched_at < now() - make_interval(secs => %s)
                  AND source_id NOT LIKE '%%:SUMMARY'
                GROUP BY cve_id, source_id
                """,
                (ttl_seconds,),
            )
            groups = cur.fetchall()

            for g in groups:
                summary_payload = {
                    "rollup": True,
                    "purged_count": g["n"],
                    "success_count": g["n_ok"],
                    "first_fetched_at": g["first_at"].isoformat(),
                    "last_fetched_at": g["last_at"].isoformat(),
                    "ttl_seconds": ttl_seconds,
                }
                cur.execute(
                    "INSERT INTO raw_ingests (cve_id, source_id, payload, success) "
                    "VALUES (%s, %s, %s, TRUE)",
                    (
                        g["cve_id"],
                        f"{g['source_id']}:SUMMARY",
                        psycopg2.extras.Json(summary_payload),
                    ),
                )
                result["rollups_written"] += 1

            # 2. Purge the expired raw (non-summary) rows.
            cur.execute(
                """
                DELETE FROM raw_ingests
                WHERE fetched_at < now() - make_interval(secs => %s)
                  AND source_id NOT LIKE '%%:SUMMARY'
                """,
                (ttl_seconds,),
            )
            result["rows_purged"] = cur.rowcount

            # 3. Mark consensus rows as stale once they age past the TTL.
            cur.execute(
                """
                UPDATE consensus
                SET stale = TRUE
                WHERE consensus_computed_at < now() - make_interval(secs => %s)
                  AND stale = FALSE
                """,
                (ttl_seconds,),
            )
            result["consensus_marked_stale"] = cur.rowcount

        conn.commit()
    finally:
        conn.close()

    print("=== ttl purge summary ===")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    purge_expired()
