"""Source adapters: fetch and normalize CVE data from NVD and CIRCL.

Every failure mode (timeout, 5xx, rate limit, unparseable schema) is raised as
a typed SourceError so the ingestion worker can record it explicitly. Nothing
here silently falls back to cached or partial data.
"""
from __future__ import annotations

from typing import Optional

import requests

from app import config


class SourceError(Exception):
    """Base class for all source failures. `error_type` categorizes it."""

    error_type = "source_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SourceTimeout(SourceError):
    error_type = "timeout"


class SourceRateLimited(SourceError):
    error_type = "rate_limited"


class SourceHTTPError(SourceError):
    error_type = "http_error"


class SourceSchemaError(SourceError):
    error_type = "bad_schema"


# --------------------------------------------------------------------------- #
# HTTP fetch                                                                   #
# --------------------------------------------------------------------------- #
def _get(url: str, params: Optional[dict] = None) -> dict:
    """GET JSON, translating transport/HTTP problems into SourceErrors."""
    try:
        resp = requests.get(
            url,
            params=params,
            timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"Accept": "application/json", "User-Agent": "cve-intel/1.0"},
        )
    except requests.Timeout as exc:
        raise SourceTimeout(f"request timed out after {config.HTTP_TIMEOUT_SECONDS}s") from exc
    except requests.RequestException as exc:
        raise SourceHTTPError(f"transport error: {exc}") from exc

    if resp.status_code in (403, 429):
        raise SourceRateLimited(f"rate limited (HTTP {resp.status_code})")
    if resp.status_code >= 500:
        raise SourceHTTPError(f"upstream error (HTTP {resp.status_code})")
    if resp.status_code == 404:
        raise SourceSchemaError("CVE not found at source (HTTP 404)")
    if resp.status_code != 200:
        raise SourceHTTPError(f"unexpected status HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        raise SourceSchemaError("response was not valid JSON") from exc


def fetch_nvd(cve_id: str) -> dict:
    return _get(config.NVD_URL, params={"cveId": cve_id})


def fetch_circl(cve_id: str) -> dict:
    return _get(f"{config.CIRCL_URL}/{cve_id}")


# --------------------------------------------------------------------------- #
# Parsing / normalization                                                      #
# --------------------------------------------------------------------------- #
def _product_from_cpe(cpe: str) -> Optional[str]:
    """Extract 'vendor:product' from a CPE 2.3 URI, e.g.
    'cpe:2.3:a:apache:log4j:2.14.1:*:...' -> 'apache:log4j'."""
    parts = cpe.split(":")
    if len(parts) >= 5 and parts[0] == "cpe":
        vendor, product = parts[3], parts[4]
        if product and product != "*":
            return f"{vendor}:{product}"
    return None


def parse_nvd(payload: dict) -> dict:
    """Normalize an NVD API v2.0 response for a single CVE."""
    try:
        vulns = payload["vulnerabilities"]
        if not vulns:
            raise SourceSchemaError("NVD returned no vulnerabilities for this CVE")
        cve = vulns[0]["cve"]
    except (KeyError, TypeError, IndexError) as exc:
        raise SourceSchemaError(f"unexpected NVD schema: {exc}") from exc

    metrics = cve.get("metrics", {})
    cvss = severity = None
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if entries:
            data = entries[0].get("cvssData", {})
            cvss = data.get("baseScore")
            severity = data.get("baseSeverity") or entries[0].get("baseSeverity")
            break
    if cvss is None:
        raise SourceSchemaError("NVD record has no CVSS base score")

    products: list[str] = []
    for conf in cve.get("configurations", []) or []:
        for node in conf.get("nodes", []) or []:
            for match in node.get("cpeMatch", []) or []:
                prod = _product_from_cpe(match.get("criteria", ""))
                if prod:
                    products.append(prod)

    description = None
    for d in cve.get("descriptions", []) or []:
        if d.get("lang") == "en":
            description = d.get("value")
            break

    return {
        "cvss": float(cvss),
        "severity": severity.lower() if severity else None,
        "affected_products": sorted(set(products)),
        "published": _iso(cve.get("published")),
        "description": description,
    }


def parse_circl(payload: dict) -> dict:
    """Normalize a CIRCL response.

    CIRCL migrated to a Vulnerability-Lookup backend, so the shape varies. We
    try the classic flat format first, then the cvelistv5 container format.
    If neither yields a CVSS score, we raise SourceSchemaError (which the
    worker records as a real 'bad_schema' error — not a silent fallback).
    """
    if not isinstance(payload, dict) or not payload:
        raise SourceSchemaError("CIRCL returned an empty or non-object payload")

    # --- Classic flat format: {"cvss": 10.0, "summary": ..., "Published": ...} ---
    cvss = payload.get("cvss3") or payload.get("cvss")
    if cvss is not None:
        products = []
        for cpe in payload.get("vulnerable_product", []) or []:
            prod = _product_from_cpe(cpe)
            if prod:
                products.append(prod)
        return {
            "cvss": float(cvss),
            "severity": None,  # classic CIRCL omits qualitative severity
            "affected_products": sorted(set(products)),
            "published": _iso(payload.get("Published") or payload.get("published")),
            "description": payload.get("summary"),
        }

    # --- cvelistv5 container format ---
    # CVSS metrics may live in the CNA container OR in any ADP (Authorized Data
    # Publisher, e.g. CISA-ADP) container, so scan all of them.
    containers = payload.get("containers")
    if isinstance(containers, dict):
        cna = containers.get("cna", {})
        metric_lists = []
        if cna.get("metrics"):
            metric_lists.append(cna["metrics"])
        for adp in containers.get("adp", []) or []:
            if adp.get("metrics"):
                metric_lists.append(adp["metrics"])

        score = sev = None
        for metrics in metric_lists:
            for m in metrics:
                for ck in ("cvssV3_1", "cvssV3_0", "cvssV4_0"):
                    block = m.get(ck)
                    if isinstance(block, dict) and block.get("baseScore") is not None:
                        score = block.get("baseScore")
                        sev = block.get("baseSeverity")
                        break
                if score is not None:
                    break
            if score is not None:
                break

        if score is not None:
            # Products can appear under cna.affected and adp[].affected.
            products = []
            affected_lists = [cna.get("affected", []) or []]
            for adp in containers.get("adp", []) or []:
                affected_lists.append(adp.get("affected", []) or [])
            for aff_list in affected_lists:
                for aff in aff_list:
                    vendor = aff.get("vendor")
                    product = aff.get("product")
                    if product and product not in ("n/a", "*"):
                        products.append(
                            f"{vendor}:{product}"
                            if vendor and vendor not in ("n/a", "*")
                            else product
                        )
            desc = None
            for d in cna.get("descriptions", []) or []:
                if d.get("lang", "").startswith("en"):
                    desc = d.get("value")
                    break
            meta = payload.get("cveMetadata", {})
            return {
                "cvss": float(score),
                "severity": sev.lower() if sev else None,
                "affected_products": sorted(set(products)),
                "published": _iso(meta.get("datePublished")),
                "description": desc,
            }

    raise SourceSchemaError("CIRCL payload had no recognizable CVSS score")


def _iso(value: Optional[str]) -> Optional[str]:
    """Normalize an assortment of timestamp strings to ISO8601 with a Z suffix."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    # CIRCL classic uses "YYYY-MM-DDTHH:MM:SS" (no zone) or space-separated.
    if "T" not in v and " " in v:
        v = v.replace(" ", "T", 1)
    if v.endswith("Z"):
        return v
    # Trailing +00:00 -> Z; naive -> assume UTC.
    if v.endswith("+00:00"):
        return v[:-6] + "Z"
    if len(v) >= 19 and v[10] == "T" and "+" not in v[19:]:
        return v + "Z"
    return v
