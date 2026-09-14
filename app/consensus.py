"""Cross-source consensus computation.

`compute_consensus` is a PURE function: no I/O, no clock, no DB. It takes the
normalized results of each source and returns the single source of truth plus
dynamic confidence, quality, and warnings. This is what the unit tests target.
"""
from __future__ import annotations

from typing import Optional

from app import config


def severity_from_score(score: float) -> str:
    """Map a CVSS v3 base score to its qualitative severity band."""
    if score <= 0:
        return "none"
    if score < 4.0:
        return "low"
    if score < 7.0:
        return "medium"
    if score < 9.0:
        return "high"
    return "critical"


def _completeness(source: dict) -> float:
    """Fraction of expected fields present (non-empty) in a source record."""
    present = 0
    for field in config.QUALITY_FIELDS:
        value = source.get(field)
        if value not in (None, "", [], {}):
            present += 1
    return present / len(config.QUALITY_FIELDS)


def compute_consensus(cve_id: str, sources: list[dict]) -> Optional[dict]:
    """Compute the consensus record for one CVE.

    `sources` is a list of normalized dicts, one per attempted source::

        {
          "source_id": "NVD",
          "publisher": "NIST NVD",
          "success":   True,
          "retrieved_at": "2026-09-14T10:00:00Z",   # ISO8601
          "cvss":      10.0,          # float or None
          "severity":  "critical",    # str or None
          "affected_products": [...], # list
          "published": "2021-12-10T00:00:00Z",  # ISO8601 or None
          "description": "...",       # str or None
        }

    Returns the consensus dict, or ``None`` when no source produced a usable
    CVSS score (e.g. both sources failed) — in that case the caller writes
    only ingestion_errors, never a consensus row.
    """
    # A source contributes only if it succeeded AND yielded a CVSS score.
    contributors = [
        s for s in sources if s.get("success") and s.get("cvss") is not None
    ]
    if not contributors:
        return None

    # --- CVSS: weighted average when sources agree, conservative on conflict ---
    scores = [float(s["cvss"]) for s in contributors]
    disagreement = False
    if len(contributors) >= 2 and (max(scores) - min(scores)) > config.CVSS_TOLERANCE:
        # Outlier disagreement: don't average conflicting signals. Take the
        # more conservative (higher) score and flag reduced confidence.
        disagreement = True
        cvss = round(max(scores), 1)
    else:
        weights = [config.SOURCE_WEIGHTS.get(s["source_id"], 0.5) for s in contributors]
        total_w = sum(weights) or 1.0
        cvss = round(sum(v * w for v, w in zip(scores, weights)) / total_w, 1)

    severity = severity_from_score(cvss)

    # --- Confidence: agreement + number of sources ---
    if len(contributors) >= 2 and not disagreement:
        # Tighter agreement -> higher confidence, in [0.90, 0.97].
        spread = max(scores) - min(scores)
        closeness = 1.0 - min(spread / config.CVSS_TOLERANCE, 1.0)
        confidence = round(0.90 + 0.07 * closeness, 2)
    else:
        # Single source, or two sources that disagree beyond tolerance.
        confidence = config.LOW_CONFIDENCE

    verified = len(contributors) >= 2 and confidence >= config.VERIFIED_MIN_CONFIDENCE

    # --- Quality: completeness of the successful source records ---
    successful = [s for s in sources if s.get("success")]
    quality_score = round(
        sum(_completeness(s) for s in successful) / len(successful), 2
    ) if successful else 0.0

    # --- Affected products: union across contributors, deduped, capped ---
    products: list[str] = []
    seen = set()
    for s in contributors:
        for p in (s.get("affected_products") or []):
            if p not in seen:
                seen.add(p)
                products.append(p)
    products = products[:50]

    # --- Published date: prefer NVD's, else first available ---
    published = None
    for s in sorted(contributors, key=lambda x: x["source_id"] != "NVD"):
        if s.get("published"):
            published = s["published"]
            break

    # --- Provenance of contributing sources (survives raw TTL purge) ---
    sources_used = [
        {
            "source_id": s["source_id"],
            "publisher": s.get("publisher")
            or config.SOURCES.get(s["source_id"], {}).get("publisher", s["source_id"]),
            "retrieved_at": s.get("retrieved_at"),
        }
        for s in contributors
    ]

    warnings = build_warnings(
        n_sources=len(contributors),
        confidence=confidence,
        stale=False,
        disagreement=disagreement,
        contributors=contributors,
    )

    return {
        "cve_id": cve_id,
        "severity": severity,
        "cvss": cvss,
        "affected_products": products,
        "published": published,
        "confidence": confidence,
        "quality_score": quality_score,
        "verified": verified,
        "sources_used": sources_used,
        "disagreement": disagreement,
        "warnings": warnings,
    }


def build_warnings(
    *,
    n_sources: int,
    confidence: float,
    stale: bool,
    disagreement: bool | None = None,
    contributors: list[dict] | None = None,
) -> list[str]:
    """Assemble human-readable warnings.

    Derivable purely from (n_sources, confidence, stale), so the API can rebuild
    the exact same warnings from stored consensus columns without extra state.
    When called from the consensus function we also have `disagreement`
    directly; the API infers it as "2+ sources but confidence below verified".
    """
    warnings: list[str] = []

    if n_sources < 2:
        which = ""
        if contributors:
            which = f" ({contributors[0].get('source_id')})"
        warnings.append(
            f"Only one source{which} contributed; result is not two-source verified."
        )
    else:
        is_disagreement = (
            disagreement
            if disagreement is not None
            else confidence < config.VERIFIED_MIN_CONFIDENCE
        )
        if is_disagreement:
            warnings.append(
                "Sources disagreed beyond CVSS tolerance; using the conservative "
                "(higher) score with reduced confidence."
            )

    if stale:
        warnings.append(
            "Served data age exceeds the TTL; value is stale and should be refreshed."
        )

    return warnings
