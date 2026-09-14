"""Central configuration and shared constants.

These constants are the single source of truth for both the pure consensus
function (app/consensus.py) and the serving API (app/main.py), so the two can
never drift apart on how confidence maps to `verified` / warnings.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env once, on import, so every module (and pytest) sees the same config.
load_dotenv()


# --- Product identity (matches the assignment's canonical response spec) ---
PRODUCT_ID = "security.cve.v1"
API_VERSION = "1.0.0"
ENDPOINT_PATH = "/v1/security/cve"

# --- Lifecycle / freshness ---
TTL_SECONDS = 3600  # raw_ingests TTL and freshness ceiling for served data

# --- Rate limit reported in the API payload (metadata only) ---
RATE_LIMIT = {"limit": 100, "window_seconds": 60}

# --- Consensus tuning ---
# Reliability weights per source. NVD (NIST) is the authoritative primary;
# CIRCL is a strong secondary. Used for the weighted average and confidence.
SOURCE_WEIGHTS = {"NVD": 0.6, "CIRCL": 0.4}

# Max allowed absolute difference between two sources' CVSS base scores before
# we treat them as disagreeing (outlier disagreement).
CVSS_TOLERANCE = 1.0

# A consensus row counts as two-source "verified" only when >=2 sources
# contributed AND confidence is at least this high. Single-source (0.5) and
# disagreement (0.5) both fall below it, so neither is ever marked verified.
VERIFIED_MIN_CONFIDENCE = 0.9

# Confidence assigned to a single-source or disagreeing-two-source result.
LOW_CONFIDENCE = 0.5

# Fields we expect a complete source record to carry (drives quality_score).
QUALITY_FIELDS = ("cvss", "severity", "affected_products", "published", "description")


# --- Source registry: id -> publisher label used in provenance ---
SOURCES = {
    "NVD": {"publisher": "NIST NVD"},
    "CIRCL": {"publisher": "CIRCL CVE Search"},
}


# --- Watchlist: 11 real, well-known CVEs tracked by the ingestion worker ---
WATCHLIST = [
    "CVE-2021-44228",  # Log4Shell (Apache Log4j RCE)
    "CVE-2021-45046",  # Log4j follow-up
    "CVE-2023-4863",   # libwebp heap overflow (Chrome/everywhere)
    "CVE-2014-0160",   # Heartbleed (OpenSSL)
    "CVE-2014-6271",   # Shellshock (bash)
    "CVE-2017-0144",   # EternalBlue (SMBv1, MS17-010)
    "CVE-2019-0708",   # BlueKeep (RDP)
    "CVE-2020-1472",   # Zerologon (Netlogon)
    "CVE-2021-34527",  # PrintNightmare (Windows Print Spooler)
    "CVE-2022-22965",  # Spring4Shell
    "CVE-2023-44487",  # HTTP/2 Rapid Reset
]


# --- Endpoints (both free, no API key) ---
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
CIRCL_URL = "https://cve.circl.lu/api/cve"


# --- HTTP / ingestion behaviour (overridable via .env) ---
HTTP_TIMEOUT_SECONDS = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
NVD_REQUEST_DELAY_SECONDS = float(os.getenv("NVD_REQUEST_DELAY_SECONDS", "6"))
# Seconds between full watchlist passes when running `app.ingestion --loop`.
# Kept well under TTL_SECONDS so served data never ages past the TTL.
INGEST_INTERVAL_SECONDS = float(os.getenv("INGEST_INTERVAL_SECONDS", "900"))


def db_config() -> dict:
    """Return psycopg2 connection kwargs for the target database."""
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
        "dbname": os.getenv("DB_NAME", "cve_intel"),
    }


def maintenance_db_config() -> dict:
    """Connection kwargs for the maintenance DB used to CREATE the target DB."""
    cfg = db_config()
    cfg["dbname"] = os.getenv("DB_MAINTENANCE", "postgres")
    return cfg
