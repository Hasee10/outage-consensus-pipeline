"""FastAPI serving layer.

GET /v1/energy/outages?region=TX[&area=Harris][&min_customers=1][&limit=50]

Reads ONLY from Postgres (never calls a provider live). Emits the exact
standardized response envelope from the assignment spec, logs every request
to request_log, and returns a clean 404 for regions that are not tracked or
have no snapshot yet.
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
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_id() -> str:
    return "req_" + uuid.uuid4().hex[:24]


def _log_request(latency_ms: int, status_code: int) -> None:
    """Best-effort audit log; never let logging failure break the response."""
    try:
        with db.pooled_cursor(dict_rows=False) as cur:
            cur.execute(
                "INSERT INTO request_log (endpoint, latency_ms, status_code) VALUES (%s, %s, %s)",
                (config.ENDPOINT_PATH, latency_ms, status_code),
            )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"request_log insert failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    db.close_pool()


app = FastAPI(title="Outage Intelligence API", version=config.API_VERSION, lifespan=lifespan)


def _latest_snapshot(region: str) -> dict | None:
    with db.pooled_cursor() as cur:
        cur.execute(
            "SELECT * FROM consensus_snapshots WHERE region = %s ORDER BY computed_at DESC LIMIT 1",
            (region,),
        )
        return cur.fetchone()


def _error(status: int, code: str, message: str, request_id: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, **extra},
            "meta": {
                "request_id": request_id,
                "product_id": config.PRODUCT_ID,
                "version": config.API_VERSION,
                "served_at": _iso_z(datetime.now(timezone.utc)),
            },
        },
    )


def _build_response(row: dict, *, area: str | None, min_customers: int, limit: int,
                    latency_ms: int, request_id: str) -> dict:
    now = datetime.now(timezone.utc)
    computed_at = row["computed_at"]
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    age_seconds = max(0, int((now - computed_at).total_seconds()))
    stale = age_seconds > config.TTL_SECONDS

    areas = row["areas"] or []
    if area:
        areas = [a for a in areas if a["area"].lower() == area.lower()]
    areas = [a for a in areas if a["customers_affected"] >= min_customers][:limit]

    outages = [
        {
            "area": a["area"],
            "customers_affected": a["customers_affected"],
            "customers_tracked": a.get("customers_tracked"),
            "eta": a.get("eta"),
            "utilities_reporting": a.get("utilities_reporting", []),
            "sources": sorted(a.get("reports", {}).keys()),
            "confidence": a["confidence"],
            "verified": a["verified"],
        }
        for a in areas
    ]
    utilities = [
        {
            "utility": u["utility"],
            "customers_affected": u["customers_affected"],
            "customers_tracked": u.get("customers_tracked"),
            "sources": sorted(u.get("reports", {}).keys()),
            "confidence": u["confidence"],
            "verified": u["verified"],
        }
        for u in (row["utilities"] or [])
        if u["customers_affected"] >= min_customers
    ][:limit]

    warnings = list(row["warnings"] or [])
    if stale:
        warnings.append(consensus.stale_warning())

    return {
        "data": {
            "region": row["region"],
            "outages": outages,
            "utilities": utilities,
            "total_customers_affected": row["total_customers_affected"],
            "as_of": _iso_z(row["as_of"]),
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
            "provenance": [
                {"source_id": s.get("source_id"), "publisher": s.get("publisher"),
                 "retrieved_at": s.get("retrieved_at")}
                for s in (row["sources_used"] or [])
            ],
            "trust": {
                "confidence": row["confidence"],
                "quality_score": row["quality_score"],
                "verified": bool(row["verified"]),
            },
            "license": {"type": "public", "usage": "agent_runtime"},
            "api": {"latency_ms": latency_ms, "rate_limit": config.RATE_LIMIT},
            "warnings": warnings,
        },
    }


@app.get(config.ENDPOINT_PATH)
def get_outages(
    region: str = Query(..., description="Region code, e.g. TX"),
    area: str | None = Query(None, description="Optional county filter, e.g. Harris"),
    min_customers: int = Query(1, ge=0, description="Only areas with at least this many customers out"),
    limit: int = Query(50, ge=1, le=500),
):
    start = time.perf_counter()
    request_id = _request_id()
    region = region.strip().upper()

    if region not in config.REGIONS:
        latency_ms = int((time.perf_counter() - start) * 1000)
        _log_request(latency_ms, 404)
        return _error(404, "region_not_tracked",
                      f"Region {region!r} is not tracked; supported: {', '.join(config.REGIONS)}.",
                      request_id, region=region)

    row = _latest_snapshot(region)
    latency_ms = int((time.perf_counter() - start) * 1000)
    if row is None:
        _log_request(latency_ms, 404)
        return _error(404, "no_snapshot",
                      f"No consensus snapshot exists yet for {region}; run the ingestion worker.",
                      request_id, region=region)

    body = _build_response(row, area=area, min_customers=min_customers, limit=limit,
                           latency_ms=latency_ms, request_id=request_id)
    _log_request(latency_ms, 200)
    return body
