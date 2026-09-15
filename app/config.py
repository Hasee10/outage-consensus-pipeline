"""Central configuration and shared constants.

Single source of truth for the pure consensus function (app/consensus.py),
the ingestion worker, the TTL worker and the serving API, so none of them can
drift apart on what "verified", "stale" or a tolerance means.

Task: catalog #81 — Energy & Utilities / Grid / Outage status.
      GET /v1/energy/outages   {"region": "TX"}
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# --- Product identity (matches the assignment's canonical response spec) ---
PRODUCT_ID = "energy.outages.v1"
API_VERSION = "1.0.0"
ENDPOINT_PATH = "/v1/energy/outages"

# --- Regions served. Only Texas is wired up; others get a clean 404. ---
REGIONS = {"TX": "Texas"}

# --- Lifecycle / freshness -------------------------------------------------
# Catalog says freshness "minutes": a consensus snapshot older than this is
# served with stale=true and a warning.
TTL_SECONDS = int(os.getenv("TTL_SECONDS", "900"))            # 15 min
# Raw provider payloads are micro-data: keep them for a few hours, then purge.
RAW_TTL_SECONDS = int(os.getenv("RAW_TTL_SECONDS", "21600"))  # 6 h
# Fine-grained snapshots are summarised into hourly rollups after this age.
SNAPSHOT_TTL_SECONDS = int(os.getenv("SNAPSHOT_TTL_SECONDS", "86400"))  # 24 h
# Ingestion loop cadence (well under TTL_SECONDS).
INGEST_INTERVAL_SECONDS = float(os.getenv("INGEST_INTERVAL_SECONDS", "300"))

# --- Rate limit reported in the API payload (metadata only) ---
RATE_LIMIT = {"limit": 100, "window_seconds": 60}

# --- Source registry --------------------------------------------------------
# kind: "utility" = the electric company's own outage map (first-hand data);
#       "aggregator" = an independent site that reads every utility's map.
# weight: reliability weight used in the weighted average. First-hand feeds
#         outrank aggregators, which lag the utility by minutes.
SOURCES = {
    "ONCOR":         {"publisher": "Oncor Electric Delivery (Kubra Storm Center)",
                      "kind": "utility", "weight": 0.55, "utility": "Oncor"},
    "CPS":           {"publisher": "CPS Energy (Kubra Storm Center)",
                      "kind": "utility", "weight": 0.55, "utility": "CPS Energy"},
    "AUSTIN_ENERGY": {"publisher": "Austin Energy (Kubra Storm Center)",
                      "kind": "utility", "weight": 0.55, "utility": "Austin Energy"},
    "TNMP":          {"publisher": "Texas-New Mexico Power (Kubra Storm Center)",
                      "kind": "utility", "weight": 0.55, "utility": "TNMP"},
    "OUTAGE_PRO":    {"publisher": "outage-pro.com",
                      "kind": "aggregator", "weight": 0.15},
    "OUTAGE_ONLINE": {"publisher": "outage.online",
                      "kind": "aggregator", "weight": 0.15},
    "USOUTAGE":      {"publisher": "usoutage.com",
                      "kind": "aggregator", "weight": 0.15},
}

# Kubra Storm Center instance / view ids (public, embedded in each utility's
# outage-map page). Order matters: (stormcenter_id, view_id).
KUBRA = {
    "ONCOR":         ("560abba3-7881-4741-b538-ca416b58ba1e", "ca124b24-9a06-4b19-aeb3-1841a9c962e1"),
    "CPS":           ("912c6202-c3f4-491c-a8c1-726157725e92", "812092c0-153f-4a7f-8c58-e1af1cb740b7"),
    "AUSTIN_ENERGY": ("dd9c446f-f6b8-43f9-8f80-83f5245c60a1", "76446308-a901-4fa3-849c-3dd569933a51"),
    "TNMP":          ("6f00c909-0ab9-46c1-94c5-c07435d5baf6", "5bb75bf1-56fa-4d46-ac74-dd9b2106611a"),
}
KUBRA_BASE = "https://kubra.io"

# Utilities whose service area is (essentially) one county and whose Kubra
# instance publishes district/ZIP layers but no county layer. Their total is
# attributed to this county when no county layer is available.
HOME_COUNTY = {"AUSTIN_ENERGY": "Travis", "CPS": "Bexar"}

AGGREGATOR_URLS = {
    "OUTAGE_PRO":    "https://outage-pro.com/outages/texas",
    "OUTAGE_ONLINE": "https://outage.online/texas/",
    "USOUTAGE":      "https://usoutage.com/texas/",
}

# Aggregators spell utility names differently; map them to one canonical key.
UTILITY_ALIASES = {
    "oncor": "Oncor",
    "oncor electric delivery": "Oncor",
    "cps energy": "CPS Energy",
    "austin energy": "Austin Energy",
    "tnmp": "TNMP",
    "texas-new mexico power": "TNMP",
    "texas new mexico power": "TNMP",
    "texas-new mexico power company": "TNMP",
    "centerpoint energy": "CenterPoint Energy",
    "american electric power texas": "AEP Texas",
    "aep texas": "AEP Texas",
}

# --- Consensus tuning -------------------------------------------------------
# Two reports of the same customers-out figure "agree" when they differ by no
# more than max(ABS_TOL, REL_TOL * value). Outages change minute to minute
# and aggregators poll on their own schedules, so a relative band is needed.
ABS_TOLERANCE = 25
REL_TOLERANCE = 0.20
# A consensus figure is "verified" only when >=2 sources agree AND confidence
# clears this bar. Single-source and all-disagree cases sit at LOW_CONFIDENCE.
VERIFIED_MIN_CONFIDENCE = 0.85
LOW_CONFIDENCE = 0.5
OUTLIER_PENALTY = 0.10  # confidence lost per unit of (outlier weight / agreeing weight)

# Fields a complete source record should carry (drives quality_score).
QUALITY_FIELDS = ("generated_at", "utilities", "areas", "tracked", "etr")

# --- HTTP / ingestion behaviour (overridable via .env) ----------------------
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "25"))
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 outage-intel/1.0")


def db_config() -> dict:
    """Return psycopg2 connection kwargs for the target database."""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
        "dbname": os.getenv("DB_NAME", "outage_intel"),
    }


def maintenance_db_config() -> dict:
    """Connection kwargs for the maintenance DB used to CREATE the target DB."""
    cfg = db_config()
    cfg["dbname"] = os.getenv("DB_MAINTENANCE", "postgres")
    return cfg
