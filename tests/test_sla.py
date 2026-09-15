"""SLA & reliability tests.

- Latency: p95 < 200 ms across repeated requests (measured and asserted).
- Availability: sustained polling never fails.
- Source failover: consensus still produced, honestly degraded, when
  providers fail — at the consensus level and as served by the API.
- Lifecycle: the TTL worker rolls up and purges, and marks stale.

DB-backed — skipped automatically if Postgres is unreachable (see conftest).
"""
from __future__ import annotations

import time

import psycopg2.extras

from app import config, db, ttl
from app.consensus import compute_consensus
from tests.conftest import TEST_REGION
from tests.test_consensus import area, src


# --- Latency profile: p95 < 200ms -------------------------------------------
def test_p95_latency_under_200ms(client, seed):
    seed()
    n = 60
    latencies_ms = []
    for _ in range(n):
        start = time.perf_counter()
        resp = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION})
        latencies_ms.append((time.perf_counter() - start) * 1000)
        assert resp.status_code == 200
        assert isinstance(resp.json()["meta"]["api"]["latency_ms"], int)
    latencies_ms.sort()
    p95 = latencies_ms[int(0.95 * (n - 1))]
    assert p95 < 200, f"p95 latency {p95:.1f}ms exceeds 200ms SLA"


# --- Availability under sustained polling -----------------------------------
def test_sustained_polling_availability(client, seed):
    seed()
    statuses = [client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION}).status_code
                for _ in range(200)]
    assert len(statuses) == 200 and all(s == 200 for s in statuses)


# --- Source failover ----------------------------------------------------------
def test_failover_one_aggregator_down_still_verified():
    """Three aggregators normally; one dies; the other two still cross-verify."""
    snap = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Harris": area(300)}),
        src("OUTAGE_ONLINE", areas={"Harris": area(305)}),
        src("USOUTAGE", success=False),
    ])
    assert snap is not None
    assert snap["areas"][0]["verified"] is True
    assert any("USOUTAGE" in w for w in snap["warnings"])       # failure named, not hidden


def test_failover_only_utility_left_is_degraded_not_silent():
    snap = compute_consensus("TX", [
        src("OUTAGE_PRO", success=False),
        src("OUTAGE_ONLINE", success=False),
        src("USOUTAGE", success=False),
        src("ONCOR", areas={"Dallas": area(20, etr="2026-09-15T14:00:00Z")},
            utilities={"Oncor": {"out": 20, "tracked": 4176928}}),
    ])
    d = snap["areas"][0]
    assert d["customers_affected"] == 20 and d["verified"] is False
    assert d["confidence"] == config.LOW_CONFIDENCE
    assert snap["verified"] is False
    assert len(snap["sources_used"]) == 1


def test_failover_api_serves_degraded_row(client, seed):
    seed(confidence=config.LOW_CONFIDENCE, verified=False, sources=("ONCOR",),
         warnings=["3 source(s) failed this pass: OUTAGE_ONLINE, OUTAGE_PRO, USOUTAGE."])
    body = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION}).json()
    assert body["meta"]["trust"]["verified"] is False
    assert body["meta"]["warnings"] and body["data"]["source"] == "consensus"


# --- Lifecycle: rollup + purge + stale ----------------------------------------
def test_ttl_worker_rolls_up_and_purges(pg, seed):
    seed(age_seconds=2 * 3600)                                   # old enough to roll up
    result = ttl.run(raw_ttl=1, snapshot_ttl=3600, ttl=900)
    assert result["snapshots_purged"] >= 1
    assert result["rollup_rows_upserted"] >= 2                   # Harris + Dallas (Travis is 0)
    conn = db.connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT area, max_affected, samples FROM area_rollups WHERE region = %s ORDER BY area",
                        (TEST_REGION,))
            rows = {r["area"]: r for r in cur.fetchall()}
            cur.execute("SELECT count(*) AS n FROM consensus_snapshots WHERE region = %s", (TEST_REGION,))
            remaining = cur.fetchone()["n"]
    finally:
        conn.close()
    assert rows["Harris"]["max_affected"] == 2500 and rows["Harris"]["samples"] == 1
    assert "Travis" not in rows
    assert remaining == 0


def test_ttl_worker_marks_stale(pg, seed):
    seed(age_seconds=config.TTL_SECONDS + 60)
    result = ttl.run(raw_ttl=config.RAW_TTL_SECONDS, snapshot_ttl=config.SNAPSHOT_TTL_SECONDS)
    assert result["snapshots_marked_stale"] >= 1
