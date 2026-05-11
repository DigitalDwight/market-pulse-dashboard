#!/usr/bin/env python3
"""
Market Pulse — GitHub Actions price refresher.

Reads reports/manifest.json to find the latest report, then pulls live yfinance
data for the 7 tracked instruments and writes the volatile fields into that
report's JSON file. Analytical fields (signal, supports/resistances, analysis
text, scorecard, top trades, scenarios, macro theme) are preserved.

Also bumps `lastUpdated` to the moment the refresh ran.

Volatile fields refreshed per instrument:
    price, previousClose, change, changePercent, dayHigh, dayLow,
    yearHigh, yearLow, sparkline, ohlc

Run with: python3 refresh_dashboard.py [--manifest reports/manifest.json] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


INSTRUMENTS: list[dict[str, str]] = [
    {"symbol": "US30",   "ticker": "^DJI"},
    {"symbol": "NAS100", "ticker": "^NDX"},
    {"symbol": "GER40",  "ticker": "^GDAXI"},
    {"symbol": "AUDUSD", "ticker": "AUDUSD=X"},
    {"symbol": "GBPCAD", "ticker": "GBPCAD=X"},
    {"symbol": "XAGUSD", "ticker": "SI=F"},
    {"symbol": "XAUUSD", "ticker": "GC=F"},
]

VOLATILE_FIELDS = (
    "price", "previousClose", "change", "changePercent",
    "dayHigh", "dayLow", "yearHigh", "yearLow",
)


def _safe(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def fetch_quote(symbol: str, ticker: str) -> dict[str, Any] | None:
    t = yf.Ticker(ticker)
    try:
        fi = t.fast_info
        price        = _safe(fi.last_price)
        prev_close   = _safe(fi.previous_close)
        day_high     = _safe(fi.day_high)
        day_low      = _safe(fi.day_low)
        year_high    = _safe(fi.year_high)
        year_low     = _safe(fi.year_low)
    except Exception as exc:
        print(f"[{symbol}] fast_info failed: {exc}", file=sys.stderr)
        price = prev_close = day_high = day_low = year_high = year_low = None

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=20)
    ohlc: list[dict[str, Any]] = []
    try:
        hist = t.history(start=start.isoformat(),
                         end=(end + timedelta(days=1)).isoformat(),
                         interval="1d")
        if not hist.empty:
            for idx, row in hist.tail(14).iterrows():
                ohlc.append({
                    "date":   idx.strftime("%Y-%m-%d"),
                    "open":   _safe(row.get("Open")),
                    "high":   _safe(row.get("High")),
                    "low":    _safe(row.get("Low")),
                    "close":  _safe(row.get("Close")),
                    "volume": int(_safe(row.get("Volume")) or 0),
                })
            ohlc = [c for c in ohlc if all(c[k] is not None for k in ("open", "high", "low", "close"))]
            if ohlc:
                last = ohlc[-1]
                if price      is None: price      = last["close"]
                if prev_close is None and len(ohlc) >= 2: prev_close = ohlc[-2]["close"]
                if day_high   is None: day_high   = last["high"]
                if day_low    is None: day_low    = last["low"]
                highs = [c["high"] for c in ohlc]
                lows  = [c["low"]  for c in ohlc]
                if year_high is None: year_high = max(highs)
                if year_low  is None: year_low  = min(lows)
    except Exception as exc:
        print(f"[{symbol}] history failed: {exc}", file=sys.stderr)

    if price is None or not ohlc:
        return None

    change      = (price - prev_close) if prev_close else None
    change_pct  = (change / prev_close * 100.0) if (change is not None and prev_close) else None
    sparkline   = [c["close"] for c in ohlc][-10:]

    return {
        "price": round(price, 6),
        "previousClose": round(prev_close, 6) if prev_close is not None else None,
        "change": round(change, 6) if change is not None else None,
        "changePercent": round(change_pct, 4) if change_pct is not None else None,
        "dayHigh": round(day_high, 6) if day_high is not None else None,
        "dayLow":  round(day_low,  6) if day_low  is not None else None,
        "yearHigh": round(year_high, 6) if year_high is not None else None,
        "yearLow":  round(year_low,  6) if year_low  is not None else None,
        "sparkline": [round(x, 6) for x in sparkline],
        "ohlc": ohlc,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="reports/manifest.json",
                        help="Path to reports/manifest.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}. Run build_manifest.py first.",
              file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("reports"):
        print("No reports in manifest — nothing to refresh.", file=sys.stderr)
        return 0

    latest = manifest["reports"][0]
    report_path = manifest_path.parent / latest["jsonFile"]
    if not report_path.exists():
        print(f"ERROR: latest report file missing: {report_path}", file=sys.stderr)
        return 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    print(f"Refreshing prices on: {latest['slug']} ({report_path})", file=sys.stderr)

    quotes: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for inst in INSTRUMENTS:
        print(f"  Fetching {inst['symbol']} ({inst['ticker']})...", file=sys.stderr)
        q = fetch_quote(inst["symbol"], inst["ticker"])
        if q is None:
            failures.append(inst["symbol"])
            continue
        quotes[inst["symbol"]] = q

    if not quotes:
        print("ERROR: every yfinance fetch failed — aborting (file untouched).", file=sys.stderr)
        return 2
    if failures:
        print(f"WARN: {len(failures)} instruments failed: {failures}. Stale values preserved.",
              file=sys.stderr)

    changed = False
    for inst in report.get("instruments", []):
        q = quotes.get(inst["symbol"])
        if not q:
            continue
        for field in VOLATILE_FIELDS:
            v = q.get(field)
            if v is None:
                continue
            if inst.get(field) != v:
                inst[field] = v
                changed = True
        if q.get("sparkline") and inst.get("sparkline") != q["sparkline"]:
            inst["sparkline"] = q["sparkline"]
            changed = True
        if q.get("ohlc") and inst.get("ohlc") != q["ohlc"]:
            inst["ohlc"] = q["ohlc"]
            changed = True

    if not changed:
        print("No field deltas — nothing to write.", file=sys.stderr)
        return 0

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report["lastUpdated"] = now_iso

    if args.dry_run:
        print(f"--dry-run: would write {report_path} with new lastUpdated={now_iso}")
        return 0

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(f"Wrote {report_path}. lastUpdated={now_iso}. Stale: {failures or 'none'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
