"""Shared pytest fixtures.

DB-backed tests (test_api, test_sla) require a reachable Postgres with the
schema applied. If Postgres is unreachable they are SKIPPED with a clear
message rather than failing spuriously. test_consensus and test_sources are
pure and need no database or network.

Seeded snapshots use the synthetic region code "ZZ" (registered into
config.REGIONS for the test session) so they never clash with real TX data.
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from migrations.migrate import apply_schema, ensure_database

TEST_REGION = "ZZ"


@pytest.fixture(scope="session", autouse=True)
def _register_test_region():
    config.REGIONS[TEST_REGION] = "Test Region"
    # SLA tests fire hundreds of requests from one client; the limiter is
    # exercised by its own dedicated test (see test_api.py).
    config.RATE_LIMIT_ENFORCE = False
    yield
    config.REGIONS.pop(TEST_REGION, None)


@pytest.fixture(scope="session")
def pg():
    try:
        ensure_database()
        apply_schema()
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Postgres unavailable — skipping DB-backed tests: {exc}")
    yield
    db.close_pool()


@pytest.fixture
def client(pg):
    with TestClient(app) as c:
        yield c


def _area(name, out, *, conf=0.95, verified=True, sources=("OUTAGE_PRO", "OUTAGE_ONLINE"),
          eta=None, tracked=100000):
    return {"area": name, "customers_affected": out, "customers_tracked": tracked, "eta": eta,
            "utilities_reporting": [], "n_sources": len(sources), "confidence": conf,
            "verified": verified, "disagreement": False, "rejected": [],
            "reports": {s: out for s in sources}, "note": None}


@pytest.fixture
def seed(pg):
    """Insert a snapshot for the test region; clean up afterwards."""
    inserted = False

    def _seed(*, areas=None, confidence=0.95, quality=0.9, verified=True,
              sources=("OUTAGE_PRO", "OUTAGE_ONLINE", "ONCOR"), age_seconds=0, warnings=()):
        nonlocal inserted
        areas = areas if areas is not None else [
            _area("Harris", 2500, eta="2026-09-15T12:00:00Z"),
            _area("Dallas", 120),
            _area("Travis", 0),
        ]
        sources_used = [{"source_id": s, "publisher": config.SOURCES[s]["publisher"],
                         "retrieved_at": "2026-09-15T04:00:00Z"} for s in sources]
        utilities = [{"utility": "Oncor", "customers_affected": 800, "customers_tracked": 4000000,
                      "n_sources": 2, "confidence": 0.97, "verified": True, "disagreement": False,
                      "rejected": [], "reports": {"ONCOR": 800, "OUTAGE_PRO": 800}}]
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM consensus_snapshots WHERE region = %s", (TEST_REGION,))
                cur.execute(
                    """
                    INSERT INTO consensus_snapshots
                        (region, computed_at, as_of, total_customers_affected, confidence,
                         quality_score, verified, areas, utilities, sources_used, warnings)
                    VALUES (%s, now() - make_interval(secs => %s), now() - make_interval(secs => %s),
                            %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (TEST_REGION, age_seconds, age_seconds,
                     sum(a["customers_affected"] for a in areas), confidence, quality, verified,
                     psycopg2.extras.Json(areas), psycopg2.extras.Json(utilities),
                     psycopg2.extras.Json(sources_used), psycopg2.extras.Json(list(warnings))),
                )
            conn.commit()
        finally:
            conn.close()
        inserted = True

    yield _seed

    if inserted:
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM consensus_snapshots WHERE region = %s", (TEST_REGION,))
                cur.execute("DELETE FROM area_rollups WHERE region = %s", (TEST_REGION,))
            conn.commit()
        finally:
            conn.close()
