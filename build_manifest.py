#!/usr/bin/env python3
"""
Market Pulse — rebuild reports/manifest.json from the per-report JSON files.

Scans reports/*.json (excluding manifest.json itself), pulls the summary fields
the dashboard needs for the History list, sorts newest-first, and writes
reports/manifest.json.

Run on every push that touches reports/** (see .github/workflows/rebuild-manifest.yml)
and also as the first step of the price refresh workflow.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPORTS_DIR = Path(__file__).resolve().parent / "reports"
MANIFEST_PATH = REPORTS_DIR / "manifest.json"


SUMMARY_FIELDS = (
    "slug",
    "type",
    "date",
    "displayDate",
    "title",
    "markdownFile",
    "lastUpdated",
    "marketSentiment",
    "macroTheme",
)


def collect_reports() -> list[dict]:
    entries: list[dict] = []
    for path in sorted(REPORTS_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"SKIP {path.name}: invalid JSON ({exc})", file=sys.stderr)
            continue
        if not isinstance(data, dict) or "slug" not in data:
            print(f"SKIP {path.name}: missing 'slug' field", file=sys.stderr)
            continue
        summary = {k: data.get(k) for k in SUMMARY_FIELDS}
        summary["jsonFile"] = path.name
        entries.append(summary)

    # Newest first by date (then by slug as tiebreaker)
    entries.sort(key=lambda e: (e.get("date") or "", e.get("slug") or ""), reverse=True)
    return entries


def main() -> int:
    if not REPORTS_DIR.is_dir():
        print(f"ERROR: reports directory not found: {REPORTS_DIR}", file=sys.stderr)
        return 1

    reports = collect_reports()
    manifest = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(reports),
        "reports": reports,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    print(f"Wrote {MANIFEST_PATH} ({len(reports)} reports).")
    for r in reports:
        print(f"  - {r['date']}  {r['type']:8} {r['slug']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
