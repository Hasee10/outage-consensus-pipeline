"""TTL / lifecycle worker.

Three jobs, in order:

1. Roll up fine-grained snapshots older than SNAPSHOT_TTL_SECONDS into
   `area_rollups` (per region / area / hour: samples, max, avg, min
   confidence), then delete those snapshots. History is summarised, not lost.
2. Purge raw_ingests rows older than RAW_TTL_SECONDS. Raw provider payloads
   are micro-data; the snapshot and the rollup already carry what matters.
3. Mark the latest snapshot stale once it ages past TTL_SECONDS, so a reader
   inspecting the table sees the same verdict the API gives.

Runnable standalone:  py -m app.ttl
Importable/testable:  run(raw_ttl=..., snapshot_ttl=..., ttl=...)
"""
from __future__ import annotations

import json

from app import config, db


def run(raw_ttl: int | None = None, snapshot_ttl: int | None = None, ttl: int | None = None) -> dict:
    raw_ttl = raw_ttl or config.RAW_TTL_SECONDS
    snapshot_ttl = snapshot_ttl or config.SNAPSHOT_TTL_SECONDS
    ttl = ttl or config.TTL_SECONDS
    result = {"rollup_rows_upserted": 0, "snapshots_purged": 0,
              "raw_rows_purged": 0, "snapshots_marked_stale": 0}

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            # 1. Summarise expired snapshots into hourly rollups, then delete them.
            cur.execute(
                """
                INSERT INTO area_rollups (region, area, hour_start, samples,
                                          max_affected, avg_affected, min_confidence)
                SELECT s.region,
                       a->>'area',
                       date_trunc('hour', s.computed_at),
                       count(*),
                       max((a->>'customers_affected')::int),
                       avg((a->>'customers_affected')::int),
                       min((a->>'confidence')::float)
                FROM consensus_snapshots s,
                     jsonb_array_elements(s.areas) a
                WHERE s.computed_at < now() - make_interval(secs => %s)
                  AND (a->>'customers_affected')::int > 0
                GROUP BY 1, 2, 3
                ON CONFLICT (region, area, hour_start) DO UPDATE SET
                    samples        = area_rollups.samples + EXCLUDED.samples,
                    max_affected   = GREATEST(area_rollups.max_affected, EXCLUDED.max_affected),
                    avg_affected   = (area_rollups.avg_affected * area_rollups.samples
                                      + EXCLUDED.avg_affected * EXCLUDED.samples)
                                     / (area_rollups.samples + EXCLUDED.samples),
                    min_confidence = LEAST(area_rollups.min_confidence, EXCLUDED.min_confidence)
                """,
                (snapshot_ttl,),
            )
            result["rollup_rows_upserted"] = cur.rowcount
            cur.execute(
                "DELETE FROM consensus_snapshots WHERE computed_at < now() - make_interval(secs => %s)",
                (snapshot_ttl,),
            )
            result["snapshots_purged"] = cur.rowcount

            # 2. Purge expired raw payloads.
            cur.execute(
                "DELETE FROM raw_ingests WHERE fetched_at < now() - make_interval(secs => %s)",
                (raw_ttl,),
            )
            result["raw_rows_purged"] = cur.rowcount

            # 3. Flag snapshots that have aged past the freshness TTL.
            cur.execute(
                """
                UPDATE consensus_snapshots
                SET stale = TRUE
                WHERE computed_at < now() - make_interval(secs => %s) AND stale = FALSE
                """,
                (ttl,),
            )
            result["snapshots_marked_stale"] = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    print("=== ttl lifecycle summary ===")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    run()
