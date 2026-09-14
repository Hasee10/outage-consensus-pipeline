"""FastAPI serving layer.

GET /v1/security/cve?cve=<CVE-ID>

Reads ONLY from Postgres (never calls NVD/CIRCL live). Emits the exact
standardized response envelope from the assignment spec, logs every request to
request_log, and returns a clean 404 for CVEs not present in the DB.
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from app import config, consensus, db


def _iso_z(dt: datetime | None) -> str | None:
    """Render a datetime as ISO8601 UTC with a trailing Z."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_id() -> str:
    return "req_" + uuid.uuid4().hex[:24]


def _log_request(endpoint: str, latency_ms: int, status_code: int) -> None:
    """Best-effort audit log; never let logging failure break the response."""
    try:
        with db.pooled_cursor(dict_rows=False) as cur:
            cur.execute(
                "INSERT INTO request_log (endpoint, latency_ms, status_code) "
                "VALUES (%s, %s, %s)",
                (endpoint, latency_ms, status_code),
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"request_log insert failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    db.close_pool()


app = FastAPI(title="CVE Intelligence API", version=config.API_VERSION, lifespan=lifespan)


def _fetch_consensus(cve_id: str) -> dict | None:
    with db.pooled_cursor() as cur:
        cur.execute("SELECT * FROM consensus WHERE cve_id = %s", (cve_id,))
        return cur.fetchone()


def _build_response(row: dict, latency_ms: int, request_id: str) -> dict:
    now = datetime.now(timezone.utc)
    computed_at = row["consensus_computed_at"]
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)

    age_seconds = max(0, int((now - computed_at).total_seconds()))
    stale = age_seconds > config.TTL_SECONDS

    sources_used = row.get("sources_used") or []
    n_sources = len(sources_used)
    confidence = row.get("confidence")
    verified = n_sources >= 2 and (confidence or 0) >= config.VERIFIED_MIN_CONFIDENCE

    warnings = consensus.build_warnings(
        n_sources=n_sources,
        confidence=confidence or 0.0,
        stale=stale,
        contributors=sources_used,
    )

    provenance = [
        {
            "source_id": s.get("source_id"),
            "publisher": s.get("publisher"),
            "retrieved_at": s.get("retrieved_at"),
        }
        for s in sources_used
    ]

    return {
        "data": {
            "cve": row["cve_id"],
            "severity": row["severity"],
            "cvss": row["cvss"],
            "affected_products": row.get("affected_products") or [],
            "published": _iso_z(row.get("published_at")),
            "source": "consensus",
        },
        "meta": {
            "request_id": request_id,
            "product_id": config.PRODUCT_ID,
            "version": config.API_VERSION,
            "served_at": _iso_z(now),
            "source_last_updated_at": _iso_z(computed_at),
            "freshness": {
                "age_seconds": age_seconds,
                "ttl_seconds": config.TTL_SECONDS,
                "stale": stale,
            },
            "provenance": provenance,
            "trust": {
                "confidence": confidence,
                "quality_score": row.get("quality_score"),
                "verified": verified,
            },
            "license": {"type": "public", "usage": "agent_runtime"},
            "api": {
                "latency_ms": latency_ms,
                "rate_limit": config.RATE_LIMIT,
            },
            "warnings": warnings,
        },
    }


@app.get(config.ENDPOINT_PATH)
def get_cve(cve: str = Query(..., description="CVE identifier, e.g. CVE-2021-44228")):
    start = time.perf_counter()
    request_id = _request_id()
    cve_id = cve.strip().upper()

    row = _fetch_consensus(cve_id)
    latency_ms = int((time.perf_counter() - start) * 1000)

    if row is None:
        _log_request(config.ENDPOINT_PATH, latency_ms, 404)
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "cve_not_found",
                    "message": f"{cve_id} is not tracked in the consensus store.",
                    "cve": cve_id,
                },
                "meta": {
                    "request_id": request_id,
                    "product_id": config.PRODUCT_ID,
                    "version": config.API_VERSION,
                    "served_at": _iso_z(datetime.now(timezone.utc)),
                },
            },
        )

    body = _build_response(row, latency_ms, request_id)
    _log_request(config.ENDPOINT_PATH, latency_ms, 200)
    return body
