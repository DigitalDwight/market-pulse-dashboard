#!/usr/bin/env python3
"""
Market Pulse — rebuild reports/manifest.json and reports/accuracy.json.

Scans reports/*.json (excluding the two generated files), then writes:

  manifest.json  summary fields the dashboard needs for the History list,
                 sorted newest-first.
  accuracy.json  the scorecard record aggregated across every report: overall
                 counts, per-instrument breakdown, a per-report series, and the
                 list of misses with the size of the move that beat the call.

Both are generated artefacts. Never hand-edit them; they are overwritten on
every push that touches reports/** (see .github/workflows/rebuild-manifest.yml)
and also as the first step of the price refresh workflow.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


REPORTS_DIR = Path(__file__).resolve().parent / "reports"
MANIFEST_PATH = REPORTS_DIR / "manifest.json"
ACCURACY_PATH = REPORTS_DIR / "accuracy.json"

# Files this script writes. Excluded from the scan so a rebuild never treats
# its own output as a report.
GENERATED = {MANIFEST_PATH.name, ACCURACY_PATH.name}


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

# The three verdicts that count toward the record. NOT GRADED is tracked
# separately: it means a run could not be fairly scored (a pipeline outage, so
# far), and folding it into either the numerator or the denominator would
# misstate the record.
GRADED_VERDICTS = ("CORRECT", "PARTIALLY", "WRONG")

# Pulls the percentage moves out of a scorecard's result narrative, e.g.
# "reversed violently to 29,825 (+2.24% above bias price)". Used to rank misses
# by how hard the market went the other way. Purely for ordering and emphasis;
# the verdict itself is always the model's, never inferred from this.
_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")


def _normalise_verdict(raw: object) -> str:
    """Fold verdict spelling variants onto the canonical uppercase form."""
    v = str(raw or "").strip().upper().replace("_", " ")
    return v if v in GRADED_VERDICTS or v == "NOT GRADED" else v


def _largest_move_pct(result: object) -> float | None:
    """
    Largest absolute percentage quoted in a result narrative, signed.

    Returns None when the narrative quotes no percentage, which is common on
    older reports. Callers must treat None as "size unknown", not as zero.
    """
    matches = _PCT_RE.findall(str(result or ""))
    if not matches:
        return None
    values = [float(m) for m in matches]
    return max(values, key=abs)


def collect_reports() -> list[tuple[dict, dict]]:
    """Return (summary, full_data) pairs, newest-first."""
    entries: list[tuple[dict, dict]] = []
    for path in sorted(REPORTS_DIR.glob("*.json")):
        if path.name in GENERATED:
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
        entries.append((summary, data))

    # Newest first by date (then by slug as tiebreaker)
    entries.sort(key=lambda e: (e[0].get("date") or "", e[0].get("slug") or ""), reverse=True)
    return entries


def _blank_counts() -> dict[str, int]:
    return {"CORRECT": 0, "PARTIALLY": 0, "WRONG": 0, "NOT GRADED": 0}


def _score(counts: dict[str, int]) -> dict:
    """
    Hit rate over graded calls only.

    PARTIALLY counts half. That is a judgement call, so the formula is carried
    in the payload and rendered on the page rather than left implicit: a single
    percentage with an unstated definition is a vanity metric.
    """
    graded = sum(counts[v] for v in GRADED_VERDICTS)
    if not graded:
        return {"graded": 0, "hitRate": None, "correctPct": None}
    return {
        "graded": graded,
        "hitRate": round(100 * (counts["CORRECT"] + 0.5 * counts["PARTIALLY"]) / graded, 1),
        "correctPct": round(100 * counts["CORRECT"] / graded, 1),
    }


def build_accuracy(entries: list[tuple[dict, dict]]) -> dict:
    totals = _blank_counts()
    by_instrument: dict[str, dict[str, int]] = {}
    by_report: list[dict] = []
    misses: list[dict] = []

    # Oldest-first so the series reads left to right as time.
    for summary, data in reversed(entries):
        counts = _blank_counts()
        for row in data.get("scorecard") or []:
            if not isinstance(row, dict):
                continue
            verdict = _normalise_verdict(row.get("verdict"))
            instrument = str(row.get("instrument") or "UNKNOWN").strip()
            if verdict in counts:
                counts[verdict] += 1
                totals[verdict] += 1
            inst = by_instrument.setdefault(instrument, _blank_counts())
            if verdict in inst:
                inst[verdict] += 1
            if verdict == "WRONG":
                misses.append({
                    "slug": summary.get("slug"),
                    "date": summary.get("date"),
                    "displayDate": summary.get("displayDate"),
                    "type": summary.get("type"),
                    "instrument": instrument,
                    "previousBias": row.get("previousBias"),
                    "result": row.get("result"),
                    "movePct": _largest_move_pct(row.get("result")),
                })

        by_report.append({
            "slug": summary.get("slug"),
            "date": summary.get("date"),
            "displayDate": summary.get("displayDate"),
            "type": summary.get("type"),
            **counts,
            **_score(counts),
        })

    # Misses ranked by how far the market went the other way. Unknown size sorts
    # last rather than first, so an unquantified miss never displaces a measured
    # one at the top of the list.
    misses.sort(key=lambda m: (m["movePct"] is not None, abs(m["movePct"] or 0)), reverse=True)

    instruments = {
        name: {**counts, **_score(counts)}
        for name, counts in sorted(by_instrument.items())
    }

    return {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hitRateFormula": "(CORRECT + 0.5 x PARTIALLY) / graded calls. NOT GRADED is excluded from both sides.",
        "reportCount": len(by_report),
        "totals": {**totals, **_score(totals)},
        "byInstrument": instruments,
        "byReport": by_report,
        "misses": misses,
    }


def main() -> int:
    if not REPORTS_DIR.is_dir():
        print(f"ERROR: reports directory not found: {REPORTS_DIR}", file=sys.stderr)
        return 1

    entries = collect_reports()
    reports = [summary for summary, _ in entries]

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

    accuracy = build_accuracy(entries)
    ACCURACY_PATH.write_text(json.dumps(accuracy, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    t = accuracy["totals"]
    print(
        f"Wrote {ACCURACY_PATH}: {t['graded']} graded calls, "
        f"{t['CORRECT']}C / {t['PARTIALLY']}P / {t['WRONG']}W, "
        f"hit rate {t['hitRate']}%, {len(accuracy['misses'])} misses."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
