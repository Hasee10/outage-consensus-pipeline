"""Source adapters: fetch and normalise Texas outage data from seven providers.

Four are first-hand utility feeds (Kubra Storm Center JSON) and three are
independent aggregator pages (HTML, scraped). Every adapter returns the same
normalised shape so the consensus function never cares who produced it::

    {
      "generated_at": "2026-09-15T04:29:46Z" | None,   # the source's own stamp
      "utilities": {"Oncor": {"out": 798, "tracked": 4176928}, ...},
      "areas":     {"Dallas": {"out": 20, "tracked": 1023456 | None,
                               "etr": "2026-09-15T14:00:00Z" | None,
                               "n_out": 3 | None}, ...},
    }

Every failure mode (timeout, HTTP error, rate limit, unparseable page) is
raised as a typed SourceError so the worker records it explicitly. Nothing
here falls back to cached or partial data.
"""
from __future__ import annotations

import hashlib
import html as htmllib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from app import config


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #
class SourceError(Exception):
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
# HTTP                                                                         #
# --------------------------------------------------------------------------- #
def _get(url: str, *, expect_json: bool, allow_404: bool = False):
    try:
        resp = requests.get(
            url,
            timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT,
                     "Accept": "application/json" if expect_json else "text/html,*/*"},
        )
    except requests.Timeout as exc:
        raise SourceTimeout(f"request timed out after {config.HTTP_TIMEOUT_SECONDS}s") from exc
    except requests.RequestException as exc:
        raise SourceHTTPError(f"transport error: {exc}") from exc

    if resp.status_code in (403, 429):
        raise SourceRateLimited(f"rate limited / blocked (HTTP {resp.status_code})")
    if resp.status_code >= 500:
        raise SourceHTTPError(f"upstream error (HTTP {resp.status_code})")
    if resp.status_code == 404 and allow_404:
        return None
    if resp.status_code != 200:
        raise SourceHTTPError(f"unexpected status HTTP {resp.status_code}")

    if not expect_json:
        return resp.text
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceSchemaError("response was not valid JSON") from exc


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _int(s) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s).replace(",", "").strip()
    return int(s) if s.isdigit() else None


def _iso(value) -> Optional[str]:
    """Normalise a timestamp to ISO-8601 UTC with a Z suffix, or None."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.upper().startswith("ETR-"):          # Kubra: ETR-NULL / ETR-EXP
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_utility(name: str) -> str:
    n = htmllib.unescape(name).strip()
    return config.UTILITY_ALIASES.get(n.lower(), n)


def canonical_area(name: str) -> str:
    """One spelling per county across sources: 'PALO_PINTO' / 'Palo Pinto' /
    'Palo Pinto County' -> 'Palo Pinto'; 'Mcculloch' -> 'McCulloch'."""
    n = htmllib.unescape(name).replace("_", " ").strip()
    n = re.sub(r"\s+County$", "", n, flags=re.I)
    n = " ".join(w.capitalize() for w in n.split())
    return re.sub(r"(?<![A-Za-z])Mc([a-z])", lambda m: "Mc" + m.group(1).upper(), n)


def _strip_tags(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", htmllib.unescape(t))


# --------------------------------------------------------------------------- #
# Kubra Storm Center (utilities)                                               #
# --------------------------------------------------------------------------- #
def fetch_kubra(source_id: str) -> dict:
    """Fetch the raw Kubra bundle: currentState + summary + county layer."""
    sc_id, view_id = config.KUBRA[source_id]
    state = _get(
        f"{config.KUBRA_BASE}/stormcenter/api/v1/stormcenters/{sc_id}/views/{view_id}"
        f"/currentState?preview=false", expect_json=True)
    try:
        data_path = state["data"]["interval_generation_data"]
    except (KeyError, TypeError) as exc:
        raise SourceSchemaError(f"Kubra currentState missing data path: {exc}") from exc

    summary = _get(f"{config.KUBRA_BASE}/{data_path}/public/summary-1/data.json", expect_json=True)

    # The county layer is one of thematic-1..3 (which one differs per utility);
    # Kubra only publishes them while outages exist, so 404 is legitimate.
    county_layer = other_layer = None
    for i in (1, 2, 3):
        layer = _get(f"{config.KUBRA_BASE}/{data_path}/public/thematic-{i}/thematic_areas.json",
                     expect_json=True, allow_404=True)
        if not layer:
            continue
        if any(str(a.get("id", "")).endswith("|county") for a in layer.get("file_data", [])):
            county_layer = layer
            break
        other_layer = other_layer or layer          # district / ZIP layer

    return {"currentState": state, "summary": summary, "county_layer": county_layer,
            "other_layer": other_layer}


def parse_kubra(source_id: str, raw: dict) -> dict:
    try:
        totals = raw["summary"]["summaryFileData"]["totals"][0]
        out = _int(totals["total_cust_a"]["val"])
        tracked = _int(totals["total_cust_s"])
        generated_at = _iso(raw["summary"]["summaryFileData"].get("date_generated"))
    except (KeyError, TypeError, IndexError) as exc:
        raise SourceSchemaError(f"unexpected Kubra summary schema: {exc}") from exc
    if out is None or tracked is None:
        raise SourceSchemaError("Kubra summary has no customer totals")

    areas: dict[str, dict] = {}
    layer = raw.get("county_layer")
    if layer:
        for a in layer.get("file_data", []):
            try:
                d = a["desc"]
                areas[canonical_area(d["name"])] = {
                    "out": _int(d["cust_a"]["val"]) or 0,
                    "tracked": _int(d.get("cust_s")),
                    "etr": _iso(d.get("etr")),
                    "n_out": _int(d.get("n_out")),
                }
            except (KeyError, TypeError) as exc:
                raise SourceSchemaError(f"unexpected Kubra county schema: {exc}") from exc
    elif out > 0 and source_id in config.HOME_COUNTY:
        # Single-county utility (e.g. Austin Energy) publishes district/ZIP
        # layers only: attribute its total to its home county, taking the
        # restoration estimate from whichever layer it did publish.
        etrs = [_iso(a.get("desc", {}).get("etr"))
                for a in (raw.get("other_layer") or {}).get("file_data", [])]
        etrs = [e for e in etrs if e]
        areas[config.HOME_COUNTY[source_id]] = {
            "out": out, "tracked": tracked, "etr": max(etrs) if etrs else None,
            "n_out": _int(totals.get("total_outages")),
        }
    elif out > 0:
        # Outages exist but no county breakdown was published: that is a
        # real data-quality failure, not something to paper over.
        raise SourceSchemaError("Kubra reports outages but published no county layer")

    utility = config.SOURCES[source_id]["utility"]
    return {"generated_at": generated_at,
            "utilities": {utility: {"out": out, "tracked": tracked}},
            "areas": areas}


# --------------------------------------------------------------------------- #
# Aggregators (HTML)                                                           #
# --------------------------------------------------------------------------- #
def _html_envelope(html: str) -> dict:
    """What we persist for an HTML source: the page fingerprint, not 500 kB."""
    return {"html_bytes": len(html.encode("utf-8")),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()}


def fetch_outage_pro() -> dict:
    html = _get(config.AGGREGATOR_URLS["OUTAGE_PRO"], expect_json=False)
    return {"html": html}


_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}


def _parse_us_et(text: str) -> Optional[str]:
    """'September 15, 2026 at 12:11 AM ET' -> ISO UTC. ET is UTC-4 (EDT) from
    March to early November, else UTC-5."""
    m = re.search(r"([A-Z][a-z]+) (\d{1,2}), (\d{4}) at (\d{1,2}):(\d{2}) (AM|PM) ET", text)
    if not m:
        return None
    mon, day, year, hh, mm, ampm = m.groups()
    h = int(hh) % 12 + (12 if ampm == "PM" else 0)
    local = datetime(int(year), _MONTHS[mon], int(day), h, int(mm))
    offset = 4 if 3 <= local.month <= 10 else 5
    return (local + timedelta(hours=offset)).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_outage_pro(raw: dict) -> dict:
    html = raw["html"]
    # County rows are anchors: <a href="/outages/texas/harris-county">...
    # "Harris County" ... "2,216,061 Served" ... "217" ... "<0.01%" ...</a>
    areas: dict[str, dict] = {}
    for block in re.findall(r'<a [^>]*href="/outages/texas/[a-z0-9-]+"[^>]*>(.*?)</a>', html, re.S):
        m = re.match(r"(.+?) County ([\d,]+) Served ([\d,]+) [<\d.]+%$", _strip_tags(block).strip())
        if m:
            areas[canonical_area(m.group(1))] = {"out": _int(m.group(3)) or 0, "tracked": _int(m.group(2)),
                                                 "etr": None, "n_out": None}
    if not areas:
        raise SourceSchemaError("outage-pro page had no county table")

    # "Most Affected Utilities": "<n> <Utility> <tracked> Served <pct>% <out>"
    text = _strip_tags(html)
    utils = {}
    for n, t, o in re.findall(r"\d+ ([A-Z][A-Za-z&' .-]+?) ([\d,]+) Served [<\d.]+% ([\d,]+)", text):
        if not n.endswith("County"):
            utils[canonical_utility(n)] = {"out": _int(o) or 0, "tracked": _int(t)}
    return {"generated_at": _parse_us_et(text), "utilities": utils, "areas": areas,
            "raw_meta": _html_envelope(html)}


def fetch_outage_online() -> dict:
    return {"html": _get(config.AGGREGATOR_URLS["OUTAGE_ONLINE"], expect_json=False)}


def parse_outage_online(raw: dict) -> dict:
    html = raw["html"]
    counties = re.findall(
        r'<span class="region-name">([^<]+)</span><span class="region-metric">([\d,]+)</span>', html)
    text = _strip_tags(html)
    utilities = re.findall(r"([A-Z][A-Za-z&' .-]+?) Customers out: ([\d,]+) · Tracked: ([\d,]+)", text)
    if not counties:
        raise SourceSchemaError("outage.online page had no county list")
    areas = {canonical_area(n): {"out": _int(o) or 0, "tracked": None, "etr": None, "n_out": None}
             for n, o in counties}
    utils = {}
    for n, o, t in utilities:
        n = re.sub(r"^.*Utility providers in Texas\s*", "", n)
        utils[canonical_utility(n)] = {"out": _int(o) or 0, "tracked": _int(t)}
    return {"generated_at": None, "utilities": utils, "areas": areas,
            "raw_meta": _html_envelope(html)}


def fetch_usoutage() -> dict:
    return {"html": _get(config.AGGREGATOR_URLS["USOUTAGE"], expect_json=False)}


def parse_usoutage(raw: dict) -> dict:
    html = raw["html"]
    rows = re.findall(
        r"<tr><td><a href='https://usoutage\.com/[^']+'>([^<]+)</a></td><td>([\d,]+)</td>"
        r"<td>([\d,]+)</td><td class=\"mobile\">([^<]+)</td></tr>", html)
    counties = re.findall(
        r"<a href='https://usoutage\.com/texas/[a-z0-9-]+/'>([^<]+)</a><span> \(([\d,]+) / ([\d,]+)\)</span>", html)
    if not rows or not counties:
        raise SourceSchemaError("usoutage page had no utility table or county list")
    utils = {canonical_utility(n): {"out": _int(o) or 0, "tracked": _int(t)} for n, o, t, _ in rows}
    areas = {canonical_area(n): {"out": _int(o) or 0, "tracked": _int(t), "etr": None, "n_out": None}
             for n, o, t in counties}
    # Page-level stamp: "as of 2026-09-15 04:06:47 AM" (UTC).
    generated = None
    m = re.search(r"as of (\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2} [AP]M)", html)
    if m:
        try:
            generated = datetime.strptime(m.group(1), "%Y-%m-%d %I:%M:%S %p").replace(
                tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return {"generated_at": generated, "utilities": utils, "areas": areas,
            "raw_meta": _html_envelope(html)}


# --------------------------------------------------------------------------- #
# Registry                                                                     #
# --------------------------------------------------------------------------- #
ADAPTERS = {
    "ONCOR":         (lambda: fetch_kubra("ONCOR"),         lambda r: parse_kubra("ONCOR", r)),
    "CPS":           (lambda: fetch_kubra("CPS"),           lambda r: parse_kubra("CPS", r)),
    "AUSTIN_ENERGY": (lambda: fetch_kubra("AUSTIN_ENERGY"), lambda r: parse_kubra("AUSTIN_ENERGY", r)),
    "TNMP":          (lambda: fetch_kubra("TNMP"),          lambda r: parse_kubra("TNMP", r)),
    "OUTAGE_PRO":    (fetch_outage_pro,    parse_outage_pro),
    "OUTAGE_ONLINE": (fetch_outage_online, parse_outage_online),
    "USOUTAGE":      (fetch_usoutage,      parse_usoutage),
}


def storable_payload(source_id: str, raw: dict, normalized: dict) -> dict:
    """Raw JSON sources are stored verbatim. HTML sources are stored as the
    extracted micro-data plus a fingerprint of the page (size + sha256)."""
    if config.SOURCES[source_id]["kind"] == "utility":
        return raw
    return {"extracted": {k: v for k, v in normalized.items() if k != "raw_meta"},
            "page": normalized.get("raw_meta")}
