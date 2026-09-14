"""SLA & reliability tests.

- Latency: p95 < 200ms across N repeated requests (measured and asserted).
- Source failover: consensus/warnings still produced correctly with one source
  down, and the API serves the degraded row without presenting it as verified.
- Availability: sustained polling does not crash the API.

DB-backed — skipped automatically if Postgres is unreachable (see conftest).
"""
from __future__ import annotations

import time

from app import config
from app.consensus import compute_consensus

PERF = "CVE-2099-1000"
FAILOVER = "CVE-2099-1001"
AVAIL = "CVE-2099-1002"


# --- Latency profile: p95 < 200ms -------------------------------------------
def test_p95_latency_under_200ms(client, seed):
    seed(PERF, cvss=10.0, sources=("NVD", "CIRCL"))
    n = 60
    latencies_ms = []
    for _ in range(n):
        start = time.perf_counter()
        resp = client.get(config.ENDPOINT_PATH, params={"cve": PERF})
        latencies_ms.append((time.perf_counter() - start) * 1000)
        assert resp.status_code == 200
        # the reported handler latency is an int and within the SLA envelope
        assert isinstance(resp.json()["meta"]["api"]["latency_ms"], int)

    latencies_ms.sort()
    p95 = latencies_ms[int(0.95 * (n - 1))]
    assert p95 < 200, f"p95 latency {p95:.1f}ms exceeds 200ms SLA"


# --- Source failover: one provider down -------------------------------------
def test_failover_consensus_when_one_source_down():
    """Consensus logic degrades gracefully when a provider fails."""
    sources = [
        {"source_id": "NVD", "publisher": "NIST NVD", "success": True,
         "retrieved_at": "2026-09-14T10:00:00Z", "cvss": 9.8, "severity": "critical",
         "affected_products": ["apache:log4j"], "published": "2021-12-10T00:00:00Z",
         "description": "rce"},
        {"source_id": "CIRCL", "publisher": "CIRCL CVE Search", "success": False,
         "retrieved_at": "2026-09-14T10:00:00Z", "cvss": None},
    ]
    r = compute_consensus("CVE-2021-44228", sources)
    assert r is not None                      # still produced
    assert r["verified"] is False             # not two-source verified
    assert len(r["sources_used"]) == 1
    assert any("Only one source" in w for w in r["warnings"])


def test_failover_api_serves_degraded_row(client, seed):
    """API serves a single-source (failed-over) row with correct warnings."""
    seed(FAILOVER, confidence=config.LOW_CONFIDENCE, sources=("NVD",))
    body = client.get(config.ENDPOINT_PATH, params={"cve": FAILOVER}).json()
    assert body["meta"]["trust"]["verified"] is False
    assert body["meta"]["warnings"]
    assert body["data"]["source"] == "consensus"


# --- Availability under sustained polling -----------------------------------
def test_sustained_polling_availability(client, seed):
    seed(AVAIL, sources=("NVD", "CIRCL"))
    statuses = []
    for _ in range(200):
        statuses.append(client.get(config.ENDPOINT_PATH, params={"cve": AVAIL}).status_code)
    assert all(s == 200 for s in statuses)
    assert len(statuses) == 200
