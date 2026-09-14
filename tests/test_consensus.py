"""Unit tests for the pure consensus function (no DB required).

Covers the four required cases: both sources agree, sources disagree beyond
threshold, only one source available, and both sources fail.
"""
from __future__ import annotations

from app import config
from app.consensus import compute_consensus, severity_from_score


def make_source(source_id, cvss, *, success=True, severity=None,
                products=None, published="2021-12-10T00:00:00Z",
                description="a vulnerability"):
    return {
        "source_id": source_id,
        "publisher": config.SOURCES.get(source_id, {}).get("publisher", source_id),
        "success": success,
        "retrieved_at": "2026-09-14T10:00:00Z",
        "cvss": cvss,
        "severity": severity,
        "affected_products": products if products is not None else ["apache:log4j"],
        "published": published,
        "description": description,
    }


# --- Case 1: both sources agree within tolerance ----------------------------
def test_both_sources_agree():
    sources = [make_source("NVD", 9.8), make_source("CIRCL", 9.9)]
    r = compute_consensus("CVE-2021-44228", sources)

    assert r is not None
    assert r["disagreement"] is False
    assert r["verified"] is True
    assert r["confidence"] >= config.VERIFIED_MIN_CONFIDENCE
    # weighted average of 9.8 (w0.6) and 9.9 (w0.4) rounds to 9.8
    assert r["cvss"] == 9.8
    assert r["severity"] == "critical"
    assert {s["source_id"] for s in r["sources_used"]} == {"NVD", "CIRCL"}
    # no single-source or disagreement warning
    assert r["warnings"] == []


# --- Case 2: sources disagree beyond threshold ------------------------------
def test_sources_disagree_beyond_threshold():
    sources = [make_source("NVD", 9.8), make_source("CIRCL", 4.0)]
    r = compute_consensus("CVE-2021-44228", sources)

    assert r["disagreement"] is True
    assert r["verified"] is False
    assert r["confidence"] == config.LOW_CONFIDENCE
    # conservative: take the higher score
    assert r["cvss"] == 9.8
    assert any("disagreed" in w for w in r["warnings"])


# --- Case 3: only one source available --------------------------------------
def test_only_one_source_available():
    sources = [
        make_source("NVD", 7.5),
        make_source("CIRCL", None, success=False),
    ]
    r = compute_consensus("CVE-2019-0708", sources)

    assert r is not None
    assert len(r["sources_used"]) == 1
    assert r["sources_used"][0]["source_id"] == "NVD"
    assert r["verified"] is False
    assert r["confidence"] <= config.LOW_CONFIDENCE
    assert any("Only one source" in w for w in r["warnings"])


# --- Case 4: both sources fail ----------------------------------------------
def test_both_sources_fail():
    sources = [
        make_source("NVD", None, success=False),
        make_source("CIRCL", None, success=False),
    ]
    r = compute_consensus("CVE-2021-44228", sources)
    assert r is None  # no consensus row is written by the caller


# --- Supporting behaviour ---------------------------------------------------
def test_severity_bands():
    assert severity_from_score(0.0) == "none"
    assert severity_from_score(3.9) == "low"
    assert severity_from_score(5.0) == "medium"
    assert severity_from_score(8.0) == "high"
    assert severity_from_score(9.5) == "critical"


def test_quality_score_reflects_completeness():
    complete = make_source("NVD", 9.8, severity="critical")
    sparse = make_source(
        "CIRCL", 9.8, severity=None, products=[], published=None, description=None
    )
    full = compute_consensus("CVE-A", [complete])
    partial = compute_consensus("CVE-B", [sparse])
    assert full["quality_score"] > partial["quality_score"]
    assert full["quality_score"] == 1.0
