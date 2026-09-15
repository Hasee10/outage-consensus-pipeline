"""Cross-source consensus for outage snapshots.

`compute_consensus` is a PURE function: no I/O, no clock, no DB. It takes the
normalised result of every attempted source and returns one region snapshot —
the single source of truth — with dynamic confidence, quality and warnings.

Two kinds of fact are reconciled, each across every source that reports it:

  * county-level customers out   (aggregators report it; utilities add a
                                  first-hand floor and the restoration ETA)
  * utility-level customers out  (the utility's own feed + every aggregator)

The same rule serves both: median-based outlier rejection, then a
reliability-weighted average of the sources that agree. Confidence falls with
spread and with every rejected outlier; a lone source or an all-disagree case
is capped at LOW_CONFIDENCE and never presented as verified.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

from app import config

# Pseudo-source id for the combined first-hand (utility) figure of a county.
DIRECT = "UTILITY_DIRECT"


# --------------------------------------------------------------------------- #
# Core rule                                                                    #
# --------------------------------------------------------------------------- #
def agree(reports: dict[str, int], weights: dict[str, float] | None = None) -> Optional[dict]:
    """Reconcile one figure reported by several sources.

    reports: {source_id: value}. Returns None when nothing was reported.

    Rule: every value defines a candidate cluster (all reports within
    max(ABS_TOLERANCE, REL_TOLERANCE * value) of it). The cluster carrying the
    most reliability weight wins (ties: more members, then the higher — more
    conservative — figure). Its members are averaged by weight; everything
    else is an outlier. Confidence starts at 0.90–0.97 depending on the spread
    inside the winning cluster and loses ground in proportion to how much
    weight the outliers carried."""
    if not reports:
        return None
    weights = weights or {}
    w = {s: weights.get(s, 0.15) for s in reports}
    n = len(reports)

    if n == 1:
        (sid, val), = reports.items()
        return {"value": int(val), "confidence": config.LOW_CONFIDENCE, "verified": False,
                "agreeing": [sid], "rejected": [], "disagreement": False, "n_sources": 1}

    best, best_key = None, None
    for centre in set(reports.values()):
        tol = max(config.ABS_TOLERANCE, config.REL_TOLERANCE * centre)
        members = {s: v for s, v in reports.items() if abs(v - centre) <= tol}
        key = (sum(w[s] for s in members), len(members), centre)
        if best_key is None or key > best_key:
            best, best_key = members, key
    agreeing = best
    rejected = sorted(set(reports) - set(agreeing))

    if len(agreeing) < 2:
        # Nobody agrees with anybody. Averaging conflicting signals would invent
        # a number no one reported; take the most conservative (highest) claim
        # and say plainly that confidence is low.
        return {"value": int(max(reports.values())), "confidence": config.LOW_CONFIDENCE,
                "verified": False, "agreeing": [], "rejected": sorted(reports),
                "disagreement": True, "n_sources": n}

    agree_w = sum(w[s] for s in agreeing)
    reject_w = sum(w[s] for s in rejected)
    value = round(sum(v * w[s] for s, v in agreeing.items()) / agree_w)
    centre = max(best_key[2], 1.0)
    spread = (max(agreeing.values()) - min(agreeing.values())) / centre
    confidence = 0.90 + 0.07 * (1.0 - min(spread, 1.0))
    confidence -= config.OUTLIER_PENALTY * (reject_w / agree_w)
    confidence = round(max(confidence, config.LOW_CONFIDENCE), 2)
    verified = confidence >= config.VERIFIED_MIN_CONFIDENCE and agree_w > reject_w
    return {"value": int(value), "confidence": confidence, "verified": verified,
            "agreeing": sorted(agreeing), "rejected": rejected,
            "disagreement": bool(rejected), "n_sources": n}


def _completeness(src: dict) -> float:
    """Fraction of expected signals a successful source record carried."""
    utils = src.get("utilities") or {}
    areas = src.get("areas") or {}
    total_out = sum(u.get("out", 0) for u in utils.values())
    checks = [
        src.get("generated_at") is not None,
        bool(utils),
        bool(areas) or (src.get("kind") == "utility" and total_out == 0),
        any(u.get("tracked") for u in utils.values()) or any(a.get("tracked") for a in areas.values()),
        (src.get("kind") != "utility") or total_out == 0 or any(a.get("etr") for a in areas.values()),
    ]
    return sum(checks) / len(checks)


# --------------------------------------------------------------------------- #
# Snapshot                                                                     #
# --------------------------------------------------------------------------- #
def compute_consensus(region: str, sources: list[dict]) -> Optional[dict]:
    """Build the consensus snapshot for one region.

    `sources` is a list of normalised dicts, one per attempted source::

        {"source_id": "ONCOR", "publisher": "...", "kind": "utility",
         "weight": 0.55, "success": True, "retrieved_at": "...Z",
         "generated_at": "...Z" | None,
         "utilities": {"Oncor": {"out": 798, "tracked": 4176928}},
         "areas": {"Dallas": {"out": 20, "tracked": 1023456, "etr": "...Z", "n_out": 3}}}

    Returns None when no source succeeded — the caller then writes only
    ingestion_errors, never a snapshot.
    """
    ok = [s for s in sources if s.get("success")]
    if not ok:
        return None
    weights = {s["source_id"]: s.get("weight", 0.15) for s in ok}
    aggregators = [s for s in ok if s.get("kind") == "aggregator"]
    utilities = [s for s in ok if s.get("kind") == "utility"]
    warnings: list[str] = []

    failed = [s["source_id"] for s in sources if not s.get("success")]
    if failed:
        warnings.append(f"{len(failed)} source(s) failed this pass: {', '.join(sorted(failed))}.")

    # --- County-level areas ------------------------------------------------
    area_names = set()
    for s in ok:
        area_names.update(s.get("areas", {}).keys())

    areas = []
    for name in sorted(area_names):
        reports = {s["source_id"]: s["areas"][name]["out"]
                   for s in aggregators if name in s.get("areas", {})}
        direct = [(s["source_id"], s["areas"][name]) for s in utilities if name in s.get("areas", {})]
        floor = sum(a["out"] for _, a in direct)

        # The utilities' own maps enter the vote as ONE first-hand figure
        # (different utilities serve different customers in a county, so their
        # numbers add up rather than corroborate each other).
        votes = dict(reports)
        if direct:
            votes[DIRECT] = floor
        vote_weights = {**weights, DIRECT: max(weights[sid] for sid, _ in direct)} if direct else weights
        r = agree(votes, vote_weights)

        if DIRECT in r["rejected"] and floor <= r["value"]:
            # A lower first-hand figure is consistent with the aggregators
            # (they also count utilities we don't fetch): not a conflict.
            r = agree(reports, weights)  # re-judge without the first-hand vote
        elif len(direct) >= 2 and not reports:
            # Only utilities reported this county: their figures add, and no
            # independent source has confirmed the total.
            r = {"value": floor, "confidence": config.LOW_CONFIDENCE, "verified": False,
                 "agreeing": [DIRECT], "rejected": [], "disagreement": False, "n_sources": 1}

        value = r["value"]
        note = None
        if floor > value:
            # Never serve fewer customers out than a utility itself admits.
            value = floor
            note = "raised to utility-reported floor"

        etrs = [a["etr"] for _, a in direct if a.get("etr")]
        tracked = next((s["areas"][name].get("tracked") for s in aggregators
                        if name in s.get("areas", {}) and s["areas"][name].get("tracked")), None)
        if tracked is None and direct:
            tracked = sum(a.get("tracked") or 0 for _, a in direct) or None

        areas.append({
            "area": name,
            "customers_affected": value,
            "customers_tracked": tracked,
            "eta": max(etrs) if etrs else None,      # when the last outage is due back
            "utilities_reporting": [config.SOURCES[sid]["utility"] for sid, _ in direct],
            "n_sources": len(reports) + len(direct),
            "confidence": r["confidence"],
            "verified": r["verified"],
            "disagreement": r["disagreement"],
            "rejected": r["rejected"],
            "reports": {**reports, **{sid: a["out"] for sid, a in direct}},
            "note": note,
        })
    areas.sort(key=lambda a: (-a["customers_affected"], a["area"]))

    # --- Utility-level figures --------------------------------------------
    util_names = set()
    for s in ok:
        util_names.update(s.get("utilities", {}).keys())
    utility_rows = []
    for name in sorted(util_names):
        reports = {s["source_id"]: s["utilities"][name]["out"] for s in ok if name in s.get("utilities", {})}
        r = agree(reports, weights)
        tracked = next((s["utilities"][name].get("tracked") for s in utilities
                        if name in s.get("utilities", {})), None) or \
                  next((s["utilities"][name].get("tracked") for s in ok
                        if name in s.get("utilities", {}) and s["utilities"][name].get("tracked")), None)
        utility_rows.append({
            "utility": name, "customers_affected": r["value"], "customers_tracked": tracked,
            "n_sources": r["n_sources"], "confidence": r["confidence"], "verified": r["verified"],
            "disagreement": r["disagreement"], "rejected": r["rejected"], "reports": reports,
        })
    utility_rows.sort(key=lambda u: (-u["customers_affected"], u["utility"]))

    # --- Region roll-up -----------------------------------------------------
    total = sum(a["customers_affected"] for a in areas)
    active = [a for a in areas if a["customers_affected"] > 0]
    if active:
        wsum = sum(a["customers_affected"] for a in active)
        confidence = round(sum(a["confidence"] * a["customers_affected"] for a in active) / wsum, 2)
    else:
        confidence = round(0.90 + 0.07 * (len(ok) / max(len(sources), 1)), 2)
    quality = round(sum(_completeness(s) for s in ok) / len(ok), 2)
    verified = len(aggregators) >= 2 and confidence >= config.VERIFIED_MIN_CONFIDENCE

    n_single = sum(1 for a in active if a["n_sources"] < 2)
    n_disagree = sum(1 for a in active if a["disagreement"])
    n_floor = sum(1 for a in active if a["note"])
    if len(aggregators) < 2:
        warnings.append("Fewer than two aggregators contributed; county figures are not cross-verified.")
    if n_single:
        warnings.append(f"{n_single} area(s) rest on a single source.")
    if n_disagree:
        warnings.append(f"{n_disagree} area(s) had an outlier source rejected or unresolved disagreement.")
    if n_floor:
        warnings.append(f"{n_floor} area(s) raised to the utility-reported floor.")

    stamps = [s.get("generated_at") or s.get("retrieved_at") for s in ok]
    return {
        "region": region,
        "as_of": max(x for x in stamps if x),
        "total_customers_affected": total,
        "areas": areas,
        "utilities": utility_rows,
        "confidence": confidence,
        "quality_score": quality,
        "verified": verified,
        "sources_used": [{"source_id": s["source_id"], "publisher": s["publisher"],
                          "retrieved_at": s["retrieved_at"]} for s in ok],
        "warnings": warnings,
    }


def stale_warning() -> str:
    return "Served data age exceeds the TTL; value is stale and should be refreshed."
