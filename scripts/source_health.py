"""Fetch + parse every source live and report; exit 1 if any adapter fails.

Used by .github/workflows/source-health.yml; also handy locally:
    python scripts/source_health.py
No database needed.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
from app import sources  # noqa: E402

report = {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "sources": {}}
failed = []
for source_id, (fetch, parse) in sources.ADAPTERS.items():
    started = time.perf_counter()
    try:
        normalized = parse(fetch())
        out = sum(u["out"] for u in normalized["utilities"].values())
        report["sources"][source_id] = {
            "ok": True, "seconds": round(time.perf_counter() - started, 2),
            "generated_at": normalized.get("generated_at"),
            "utilities": len(normalized["utilities"]), "areas": len(normalized["areas"]),
            "customers_out": out,
        }
        print(f"[{source_id:13}] ok   {report['sources'][source_id]}")
    except sources.SourceError as exc:
        report["sources"][source_id] = {"ok": False, "seconds": round(time.perf_counter() - started, 2),
                                        "error_type": exc.error_type, "message": exc.message}
        failed.append(source_id)
        print(f"[{source_id:13}] FAIL {exc.error_type}: {exc.message}")

json.dump(report, open("source_health.json", "w"), indent=2)
if failed:
    print(f"\n{len(failed)} source(s) failed: {', '.join(failed)}")
    sys.exit(1)
print("\nall sources healthy")
