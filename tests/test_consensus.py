"""Unit tests for the pure consensus function — no DB, no network."""
from __future__ import annotations

from app import config
from app.consensus import DIRECT, agree, compute_consensus


def src(source_id, *, success=True, utilities=None, areas=None, generated_at="2026-09-15T04:00:00Z"):
    meta = config.SOURCES[source_id]
    return {"source_id": source_id, "publisher": meta["publisher"], "kind": meta["kind"],
            "weight": meta["weight"], "success": success, "retrieved_at": "2026-09-15T04:01:00Z",
            "generated_at": generated_at, "utilities": utilities or {}, "areas": areas or {}}


def area(out, tracked=100000, etr=None):
    return {"out": out, "tracked": tracked, "etr": etr, "n_out": None}


# --- agree(): the core rule ---------------------------------------------------
def test_agree_all_sources_match():
    r = agree({"OUTAGE_PRO": 500, "OUTAGE_ONLINE": 505, "USOUTAGE": 498})
    assert r["value"] == 501 and r["verified"] is True
    assert r["confidence"] >= 0.96 and r["rejected"] == []


def test_agree_rejects_outlier_by_majority():
    r = agree({"OUTAGE_PRO": 300, "OUTAGE_ONLINE": 2480, "USOUTAGE": 2670},
              {"OUTAGE_PRO": .15, "OUTAGE_ONLINE": .15, "USOUTAGE": .15})
    assert r["rejected"] == ["OUTAGE_PRO"]
    assert 2480 <= r["value"] <= 2670
    assert r["verified"] is True and r["confidence"] < 0.97   # penalised, still verified


def test_agree_first_hand_weight_breaks_a_tie():
    # 2 vs 2 split; the utility's own feed sides with the first pair.
    r = agree({DIRECT: 610, "OUTAGE_PRO": 611, "OUTAGE_ONLINE": 121, "USOUTAGE": 121},
              {DIRECT: .55, "OUTAGE_PRO": .15, "OUTAGE_ONLINE": .15, "USOUTAGE": .15})
    assert r["value"] in (610, 611) and r["verified"] is True
    assert set(r["rejected"]) == {"OUTAGE_ONLINE", "USOUTAGE"}


def test_agree_single_source_is_never_verified():
    r = agree({"USOUTAGE": 42})
    assert r == {"value": 42, "confidence": config.LOW_CONFIDENCE, "verified": False,
                 "agreeing": ["USOUTAGE"], "rejected": [], "disagreement": False, "n_sources": 1}


def test_agree_total_disagreement_is_conservative():
    r = agree({"OUTAGE_PRO": 10, "OUTAGE_ONLINE": 500, "USOUTAGE": 5000})
    assert r["value"] == 5000                         # highest claim
    assert r["confidence"] == config.LOW_CONFIDENCE and r["verified"] is False
    assert r["disagreement"] is True


def test_agree_returns_none_for_nothing():
    assert agree({}) is None


# --- compute_consensus(): the snapshot ----------------------------------------
def test_snapshot_shape_and_ordering():
    snap = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Harris": area(300), "Dallas": area(50)},
            utilities={"Oncor": {"out": 800, "tracked": 4000000}}),
        src("OUTAGE_ONLINE", areas={"Harris": area(310), "Dallas": area(48)},
            utilities={"Oncor": {"out": 790, "tracked": 4000000}}),
        src("ONCOR", areas={"Dallas": area(45, etr="2026-09-15T09:00:00Z")},
            utilities={"Oncor": {"out": 798, "tracked": 4176928}}),
    ])
    assert snap["region"] == "TX"
    assert [a["area"] for a in snap["areas"]] == ["Harris", "Dallas"]
    assert snap["total_customers_affected"] == sum(a["customers_affected"] for a in snap["areas"])
    dallas = snap["areas"][1]
    assert dallas["eta"] == "2026-09-15T09:00:00Z"          # from the utility's map
    assert dallas["utilities_reporting"] == ["Oncor"]
    assert snap["verified"] is True and snap["confidence"] >= 0.85
    assert snap["utilities"][0]["utility"] == "Oncor" and snap["utilities"][0]["verified"] is True
    assert {s["source_id"] for s in snap["sources_used"]} == {"OUTAGE_PRO", "OUTAGE_ONLINE", "ONCOR"}


def test_utility_floor_raises_underreporting_aggregators():
    snap = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Bexar": area(0)}),
        src("OUTAGE_ONLINE", areas={"Bexar": area(0)}),
        src("CPS", areas={"Bexar": area(900)}, utilities={"CPS Energy": {"out": 900, "tracked": 987088}}),
    ])
    bexar = snap["areas"][0]
    assert bexar["customers_affected"] == 900
    assert bexar["note"] == "raised to utility-reported floor"
    assert any("floor" in w for w in snap["warnings"])


def test_lower_first_hand_figure_is_not_an_outlier():
    # Oncor serves only part of Denton; aggregators count every utility there.
    snap = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Denton": area(154)}),
        src("OUTAGE_ONLINE", areas={"Denton": area(155)}),
        src("USOUTAGE", areas={"Denton": area(150)}),
        src("ONCOR", areas={"Denton": area(28)}, utilities={"Oncor": {"out": 28, "tracked": 1}}),
    ])
    d = snap["areas"][0]
    assert d["customers_affected"] in range(150, 156)
    assert d["rejected"] == [] and d["verified"] is True


def test_failed_sources_are_reported_not_hidden():
    snap = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Harris": area(300)}),
        src("OUTAGE_ONLINE", success=False),
        src("USOUTAGE", success=False),
    ])
    assert snap["verified"] is False                          # < 2 aggregators
    assert any("2 source(s) failed" in w for w in snap["warnings"])
    assert any("Fewer than two aggregators" in w for w in snap["warnings"])
    assert snap["areas"][0]["confidence"] == config.LOW_CONFIDENCE


def test_all_sources_fail_yields_no_snapshot():
    assert compute_consensus("TX", [src("OUTAGE_PRO", success=False), src("ONCOR", success=False)]) is None


def test_quality_reflects_completeness():
    full = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Harris": area(300)}, utilities={"Oncor": {"out": 1, "tracked": 5}}),
        src("OUTAGE_ONLINE", areas={"Harris": area(300)}, utilities={"Oncor": {"out": 1, "tracked": 5}}),
    ])
    thin = compute_consensus("TX", [
        src("OUTAGE_PRO", areas={"Harris": area(300, tracked=None)}, generated_at=None),
        src("OUTAGE_ONLINE", areas={"Harris": area(300, tracked=None)}, generated_at=None),
    ])
    assert full["quality_score"] > thin["quality_score"]
