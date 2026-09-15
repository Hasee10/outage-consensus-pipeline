"""API tests: exact response-schema shape, filters, 404 handling, freshness.

DB-backed — skipped automatically if Postgres is unreachable (see conftest).
"""
from __future__ import annotations

from app import config
from tests.conftest import TEST_REGION


def test_response_schema_shape(client, seed):
    seed()
    resp = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"data", "meta"}

    data = body["data"]
    assert set(data.keys()) == {"region", "outages", "utilities", "total_customers_affected",
                                "as_of", "source"}
    assert data["region"] == TEST_REGION and data["source"] == "consensus"
    assert isinstance(data["outages"], list) and data["outages"]
    for o in data["outages"]:
        assert set(o.keys()) == {"area", "customers_affected", "customers_tracked", "eta",
                                 "utilities_reporting", "sources", "confidence", "verified"}
        assert isinstance(o["customers_affected"], int)
    assert data["outages"][0] == {
        "area": "Harris", "customers_affected": 2500, "customers_tracked": 100000,
        "eta": "2026-09-15T12:00:00Z", "utilities_reporting": [],
        "sources": ["OUTAGE_ONLINE", "OUTAGE_PRO"], "confidence": 0.95, "verified": True,
    }
    assert data["as_of"].endswith("Z")

    meta = body["meta"]
    assert set(meta.keys()) == {"request_id", "product_id", "version", "served_at",
                                "source_last_updated_at", "freshness", "provenance", "trust",
                                "license", "api", "warnings"}
    assert meta["product_id"] == config.PRODUCT_ID and meta["version"] == config.API_VERSION
    assert meta["request_id"].startswith("req_")
    assert set(meta["freshness"]) == {"age_seconds", "ttl_seconds", "stale"}
    assert meta["freshness"]["ttl_seconds"] == config.TTL_SECONDS
    assert set(meta["trust"]) == {"confidence", "quality_score", "verified"}
    assert meta["trust"] == {"confidence": 0.95, "quality_score": 0.9, "verified": True}
    assert meta["license"] == {"type": "public", "usage": "agent_runtime"}
    assert set(meta["api"]) == {"latency_ms", "rate_limit"}
    assert isinstance(meta["api"]["latency_ms"], int)
    assert meta["api"]["rate_limit"] == {"limit": 100, "window_seconds": 60}
    assert len(meta["provenance"]) == 3
    for p in meta["provenance"]:
        assert set(p) == {"source_id", "publisher", "retrieved_at"}
    assert meta["warnings"] == []


def test_default_filter_hides_zero_outage_areas(client, seed):
    seed()
    areas = [o["area"] for o in client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION}).json()["data"]["outages"]]
    assert "Travis" not in areas
    areas = [o["area"] for o in client.get(config.ENDPOINT_PATH,
             params={"region": TEST_REGION, "min_customers": 0}).json()["data"]["outages"]]
    assert "Travis" in areas


def test_area_filter_is_case_insensitive(client, seed):
    seed()
    body = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION, "area": "dallas"}).json()
    assert [o["area"] for o in body["data"]["outages"]] == ["Dallas"]
    assert body["data"]["total_customers_affected"] == 2620        # region total, not filtered


def test_unknown_region_returns_clean_404(client):
    resp = client.get(config.ENDPOINT_PATH, params={"region": "XX"})
    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"error", "meta"}
    assert body["error"]["code"] == "region_not_tracked" and body["error"]["region"] == "XX"
    assert body["meta"]["product_id"] == config.PRODUCT_ID


def test_region_without_snapshot_returns_404(client, pg):
    resp = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION})
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "no_snapshot"


def test_stale_flag_when_age_exceeds_ttl(client, seed):
    seed(age_seconds=config.TTL_SECONDS + 300)
    meta = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION}).json()["meta"]
    assert meta["freshness"]["stale"] is True
    assert meta["freshness"]["age_seconds"] > config.TTL_SECONDS
    assert any("stale" in w for w in meta["warnings"])


def test_fresh_flag_when_within_ttl(client, seed):
    seed(age_seconds=30)
    meta = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION}).json()["meta"]
    assert meta["freshness"]["stale"] is False
    assert meta["freshness"]["age_seconds"] <= config.TTL_SECONDS


def test_unverified_snapshot_is_served_as_unverified(client, seed):
    seed(confidence=config.LOW_CONFIDENCE, verified=False, sources=("USOUTAGE",),
         warnings=["Fewer than two aggregators contributed; county figures are not cross-verified."])
    meta = client.get(config.ENDPOINT_PATH, params={"region": TEST_REGION}).json()["meta"]
    assert meta["trust"]["verified"] is False
    assert len(meta["provenance"]) == 1 and meta["warnings"]
