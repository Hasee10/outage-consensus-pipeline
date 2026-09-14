"""Shared pytest fixtures.

DB-backed tests (test_api, test_sla) require a reachable Postgres with the
schema applied. If Postgres is unreachable, those tests are SKIPPED with a
clear message rather than failing spuriously. test_consensus is pure and needs
no database.

Seeded rows use synthetic CVE ids in the CVE-2099-* range so they never clash
with real ingested watchlist data.
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from migrations.migrate import apply_schema, ensure_database


@pytest.fixture(scope="session")
def pg():
    """Ensure the DB + schema exist; skip all DB tests if Postgres is down."""
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


@pytest.fixture
def seed(pg):
    """Return an insert function; clean up every seeded CVE afterwards."""
    inserted: list[str] = []

    def _seed(
        cve_id: str,
        *,
        cvss: float = 9.8,
        severity: str = "critical",
        confidence: float = 0.95,
        quality_score: float = 0.95,
        sources=("NVD", "CIRCL"),
        age_seconds: int = 0,
        products=None,
        published: str = "2021-12-10T00:00:00Z",
    ) -> None:
        sources_used = [
            {
                "source_id": s,
                "publisher": config.SOURCES.get(s, {}).get("publisher", s),
                "retrieved_at": "2026-09-14T10:00:00Z",
            }
            for s in sources
        ]
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO consensus
                        (cve_id, severity, cvss, affected_products, published_at,
                         consensus_computed_at, confidence, quality_score,
                         sources_used, stale)
                    VALUES (%s, %s, %s, %s, %s,
                            now() - make_interval(secs => %s), %s, %s, %s, FALSE)
                    ON CONFLICT (cve_id) DO UPDATE SET
                        severity = EXCLUDED.severity,
                        cvss = EXCLUDED.cvss,
                        affected_products = EXCLUDED.affected_products,
                        published_at = EXCLUDED.published_at,
                        consensus_computed_at = EXCLUDED.consensus_computed_at,
                        confidence = EXCLUDED.confidence,
                        quality_score = EXCLUDED.quality_score,
                        sources_used = EXCLUDED.sources_used,
                        stale = EXCLUDED.stale
                    """,
                    (
                        cve_id,
                        severity,
                        cvss,
                        psycopg2.extras.Json(products or ["apache:log4j"]),
                        published,
                        age_seconds,
                        confidence,
                        quality_score,
                        psycopg2.extras.Json(sources_used),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        inserted.append(cve_id)

    yield _seed

    # teardown
    if inserted:
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM consensus WHERE cve_id = ANY(%s)", (inserted,))
            conn.commit()
        finally:
            conn.close()
