"""API tests: exact response-schema shape, 404 handling, stale-flag correctness.

DB-backed — skipped automatically if Postgres is unreachable (see conftest).
"""
from __future__ import annotations

from app import config

FRESH = "CVE-2099-0001"
STALE = "CVE-2099-0002"
SINGLE = "CVE-2099-0003"
MISSING = "CVE-2099-9999"


# --- Exact schema shape ------------------------------------------------------
def test_response_schema_shape(client, seed):
    seed(FRESH, cvss=10.0, severity="critical", sources=("NVD", "CIRCL"))
    resp = client.get(config.ENDPOINT_PATH, params={"cve": FRESH})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"data", "meta"}

    data = body["data"]
    assert set(data.keys()) == {
        "cve", "severity", "cvss", "affected_products", "published", "source",
    }
    assert data["cve"] == FRESH
    assert data["source"] == "consensus"
    assert isinstance(data["cvss"], (int, float))
    assert isinstance(data["affected_products"], list)

    meta = body["meta"]
    assert set(meta.keys()) == {
        "request_id", "product_id", "version", "served_at",
        "source_last_updated_at", "freshness", "provenance", "trust",
        "license", "api", "warnings",
    }
    assert meta["product_id"] == config.PRODUCT_ID
    assert meta["version"] == config.API_VERSION
    assert meta["request_id"].startswith("req_")

    assert set(meta["freshness"].keys()) == {"age_seconds", "ttl_seconds", "stale"}
    assert isinstance(meta["freshness"]["age_seconds"], int)
    assert meta["freshness"]["ttl_seconds"] == config.TTL_SECONDS
    assert isinstance(meta["freshness"]["stale"], bool)

    assert set(meta["trust"].keys()) == {"confidence", "quality_score", "verified"}
    assert isinstance(meta["trust"]["verified"], bool)

    assert meta["license"] == {"type": "public", "usage": "agent_runtime"}

    assert set(meta["api"].keys()) == {"latency_ms", "rate_limit"}
    assert isinstance(meta["api"]["latency_ms"], int)
    assert meta["api"]["rate_limit"] == {"limit": 100, "window_seconds": 60}

    assert isinstance(meta["provenance"], list) and meta["provenance"]
    for p in meta["provenance"]:
        assert set(p.keys()) == {"source_id", "publisher", "retrieved_at"}

    assert isinstance(meta["warnings"], list)


def test_two_source_fresh_is_verified(client, seed):
    seed(FRESH, confidence=0.95, sources=("NVD", "CIRCL"), age_seconds=0)
    body = client.get(config.ENDPOINT_PATH, params={"cve": FRESH}).json()
    assert body["meta"]["trust"]["verified"] is True
    assert body["meta"]["freshness"]["stale"] is False
    assert body["meta"]["warnings"] == []


# --- 404 handling ------------------------------------------------------------
def test_missing_cve_returns_clean_404(client):
    resp = client.get(config.ENDPOINT_PATH, params={"cve": MISSING})
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "cve_not_found"
    assert body["error"]["cve"] == MISSING
    assert body["meta"]["product_id"] == config.PRODUCT_ID


# --- Stale flag correctness --------------------------------------------------
def test_stale_flag_when_age_exceeds_ttl(client, seed):
    seed(STALE, age_seconds=config.TTL_SECONDS * 2)
    body = client.get(config.ENDPOINT_PATH, params={"cve": STALE}).json()
    fr = body["meta"]["freshness"]
    assert fr["age_seconds"] > fr["ttl_seconds"]
    assert fr["stale"] is True
    assert any("stale" in w.lower() for w in body["meta"]["warnings"])


def test_fresh_flag_when_within_ttl(client, seed):
    seed(FRESH, age_seconds=10)
    body = client.get(config.ENDPOINT_PATH, params={"cve": FRESH}).json()
    fr = body["meta"]["freshness"]
    assert fr["age_seconds"] <= fr["ttl_seconds"]
    assert fr["stale"] is False


# --- Single-source is never presented as verified ---------------------------
def test_single_source_not_verified(client, seed):
    seed(SINGLE, confidence=config.LOW_CONFIDENCE, sources=("NVD",))
    body = client.get(config.ENDPOINT_PATH, params={"cve": SINGLE}).json()
    assert body["meta"]["trust"]["verified"] is False
    assert len(body["meta"]["provenance"]) == 1
    assert any("Only one source" in w for w in body["meta"]["warnings"])
