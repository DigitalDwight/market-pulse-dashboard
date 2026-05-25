#!/usr/bin/env python3
"""
Market Pulse -- author a new trading report via Claude API.

Reads cron_tracking/<cron_id>/last_run.md for prior-run context, pre-fetches
yfinance prices for the 7 tracked instruments, calls Claude (web_search +
publish_report tool) to author the analytical content, then writes:
    reports/<slug>.md
    reports/<slug>.json
    cron_tracking/<cron_id>/last_run.md  (updated)

Designed to run from GitHub Actions on Wed 05:30 UTC and Sun 19:30 UTC.

Usage:
    python author_report.py                # auto-detect type from weekday
    python author_report.py --type weekly  # force weekly
    python author_report.py --type midweek # force midweek
    python author_report.py --dry-run      # skip writes; print plan
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import anthropic
except ImportError:
    print("anthropic not installed. Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

from refresh_dashboard import INSTRUMENTS, fetch_quote

REPO_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = REPO_ROOT / "reports"
TRACKING_DIR = REPO_ROOT / "cron_tracking"

# Cron IDs come from the original Market Pulse spec.
CRON_IDS = {
    "weekly":  "18fb2115",  # Sunday  20:00 UTC
    "midweek": "92ba41d1",  # Wednesday 06:00 UTC
}

# Full instrument names live here, not in the LLM output — Claude only has to
# produce the analytical fields; we set name + price fields ourselves.
INSTRUMENT_NAMES = {
    "US30":   "Dow Jones Industrial Average",
    "NAS100": "Nasdaq 100",
    "GER40":  "DAX Performance Index",
    "AUDUSD": "Australian Dollar vs US Dollar",
    "GBPCAD": "British Pound vs Canadian Dollar",
    "XAGUSD": "Silver Spot",
    "XAUUSD": "Gold Spot",
}

EXPECTED_SYMBOLS = ["US30", "NAS100", "GER40", "AUDUSD", "GBPCAD", "XAGUSD", "XAUUSD"]

REQUIRED_TOP_KEYS = (
    "slug", "type", "date", "displayDate", "title", "markdownFile",
    "lastUpdated", "marketSentiment", "macroTheme", "macroOverview",
    "instruments", "scorecard", "upcomingEvents", "topTrades", "riskScenarios",
)

VOLATILE_FIELDS = (
    "price", "previousClose", "change", "changePercent",
    "dayHigh", "dayLow", "yearHigh", "yearLow",
)

EXAMPLE_SLUG = "2026-05-10-weekly"
MODEL = "claude-sonnet-4-6"


def determine_run_type(now: datetime, override: str | None) -> str:
    if override:
        return override
    wd = now.weekday()  # Mon=0 ... Sun=6
    if wd == 2:
        return "midweek"
    if wd == 6:
        return "weekly"
    raise SystemExit(
        f"author_report only runs on Wed or Sun (today weekday={wd}). "
        f"Use --type weekly|midweek to force."
    )


def slug_for(run_type: str, date: datetime) -> str:
    label = "weekly" if run_type == "weekly" else "midweek"
    return f"{date.strftime('%Y-%m-%d')}-{label}"


def fetch_all_quotes() -> tuple[dict[str, dict[str, Any]], list[str]]:
    quotes: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for inst in INSTRUMENTS:
        print(f"  Fetching {inst['symbol']} ({inst['ticker']})...", file=sys.stderr)
        q = fetch_quote(inst["symbol"], inst["ticker"])
        if q is None:
            failures.append(inst["symbol"])
        else:
            quotes[inst["symbol"]] = q
    return quotes, failures


def _cron_id_for_report_type(type_str: str) -> str:
    """Map a manifest report's 'type' string to the cron_id whose tracking file holds it."""
    t = (type_str or "").lower()
    if "mid" in t:
        return CRON_IDS["midweek"]
    return CRON_IDS["weekly"]


def load_prior_tracking(current_slug: str, current_date: datetime) -> tuple[str, str, str]:
    """
    Return (prior_slug, prior_display_date, tracking_md) for the most recent
    report strictly older than the one being authored.

    Reads manifest.json (newest-first) and picks the first entry whose slug
    differs from current_slug AND whose date < current_date. This means a
    Wednesday run scorecards against the prior Sunday (if there is one), not
    the prior Wednesday -- matching how a trader actually consumes the report
    cadence.

    Falls back to the matching-type tracking file if the manifest is missing
    or empty (first-ever run).
    """
    manifest_path = REPORTS_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_date_str = current_date.strftime("%Y-%m-%d")
        for entry in manifest.get("reports", []):
            entry_slug = entry.get("slug", "")
            entry_date = entry.get("date", "")
            if entry_slug == current_slug:
                continue
            if entry_date >= current_date_str:
                # Lexical compare works because dates are ISO YYYY-MM-DD.
                continue
            cron_id = _cron_id_for_report_type(entry.get("type", ""))
            path = TRACKING_DIR / cron_id / "last_run.md"
            if path.exists():
                return entry_slug, entry.get("displayDate", entry_slug), path.read_text(encoding="utf-8")
    # Fallback: same-type tracking file (original behaviour). Used on the very
    # first run when manifest is empty.
    return "(no prior report in manifest)", "(unknown)", "(no prior tracking file)"


def load_examples() -> tuple[str, str]:
    md = (REPORTS_DIR / f"{EXAMPLE_SLUG}.md").read_text(encoding="utf-8")
    js = (REPORTS_DIR / f"{EXAMPLE_SLUG}.json").read_text(encoding="utf-8")
    return md, js


def build_system_prompt() -> str:
    example_md, example_json = load_examples()
    return f"""You are the lead market analyst for Market Pulse, a trading intelligence service for prop firm day traders.

You author two report types on a strict twice-weekly schedule:
- WEEKLY  (Sunday 20:00 GMT): full week recap + bias for the upcoming week
- MIDWEEK (Wednesday 06:00 GMT): mid-week update + bias for the back half of the week

Instruments tracked, ALWAYS in this exact order:
US30, NAS100, GER40, AUDUSD, GBPCAD, XAGUSD, XAUUSD

# CRITICAL RULES -- non-negotiable

1. NO emoji anywhere -- markdown, JSON, or tracking note.
2. Use verbatim words "CORRECT", "PARTIALLY", "WRONG" in scorecards. Never "right" / "incorrect" / "wrong-ish".
3. Conviction trades MUST include Entry, Target, Stop, and Rationale.
4. State broken support / broken resistance explicitly (e.g. "broken support 4,635 now firm as resistance").
5. Conditional biases must state the condition explicitly (e.g. "BULLISH conditional on RBA delivering hike").
6. Signal field uses underscore form in JSON: BULLISH, BEARISH, NEUTRAL, NEUTRAL_BULLISH, NEUTRAL_BEARISH. In the markdown narrative the hyphen form (NEUTRAL-BULLISH) is fine.
7. signalStrength: integer in [-100, 100]. eventImpactProbability: integer in [0, 100].
8. Use web_search to ground the report in current macro reality: economic prints in the last 7 days, central bank speakers, geopolitical developments, earnings, commodity moves, ETF flows. Do not invent data; if you cannot verify a number, omit it.
9. All price levels in the narrative must match the live data block in the user message.

# Process

1. Read the previous tracking file in the user message. It contains the biases set last time -- those are the things you will scorecard against.
2. Use web_search (up to 5 queries) to research what actually happened in markets since that previous run. Verify the directional outcome for each instrument so the scorecard is honest. Spend your queries on the highest-value targets: the macro prints, the central bank speakers, the geopolitical headlines, the conviction-trade instruments.
3. Form fresh biases for the next 3-5 trading days for all 7 instruments.
4. Pick the top 3 conviction trades for the period.
5. Call the publish_report tool exactly once with the markdown, jsonPayload, and trackingMd fields fully populated.

# Output

End your turn by calling publish_report. Do not return free-form text instead of the tool call. The dashboard parses the JSON payload strictly -- it must match the example schema exactly. Top-level keys required: {", ".join(REQUIRED_TOP_KEYS)}.

# Reference report (Sunday weekly 10 May 2026)

Mimic this structure exactly. The example uses fictional prices; in your output the volatile price fields (price, previousClose, change, changePercent, dayHigh, dayLow, yearHigh, yearLow, sparkline, ohlc) MUST come from the live data block in the user message.

## Example markdown

```markdown
{example_md}
```

## Example JSON payload

```json
{example_json}
```
"""


def build_user_prompt(
    run_type: str,
    today: datetime,
    slug: str,
    prior_slug: str,
    prior_display_date: str,
    prior_tracking: str,
    prefetched: dict[str, dict[str, Any]],
    failures: list[str],
) -> str:
    type_label = "Weekly" if run_type == "weekly" else "Mid-Week"
    display_date = today.strftime("%A %d %B %Y")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prices_block = json.dumps(prefetched, indent=2, ensure_ascii=False)
    fail_line = (
        f"\nyfinance failures (no fresh data, use prior tracking values or omit): {failures}"
        if failures else ""
    )
    return f"""Author the {type_label} Market Pulse report for {display_date}.

Top-level identifiers (use these verbatim in jsonPayload):
- slug: {slug}
- type: {type_label}
- date: {today.strftime("%Y-%m-%d")}
- displayDate: {display_date}
- title: MARKET PULSE -- {type_label} Trading Report
- markdownFile: {slug}.md
- lastUpdated: {now_iso}

# Pre-fetched live quotes (yfinance, captured {now_iso})

Use these prices verbatim for the volatile JSON fields. Use them as the ground truth in your narrative too -- the numbers in the markdown must match.{fail_line}

```json
{prices_block}
```

# Previous report: {prior_slug} ({prior_display_date})

Your scorecard MUST evaluate the biases set in THIS report (the most recent prior publication). The tracking note below is what that previous run wrote for you; the "Biases Set This Run" table inside it is what you must scorecard against -- not biases from any earlier report. In the scorecard markdown table and the jsonPayload.scorecard array, reference "{prior_display_date}" as the prior-bias date.

```markdown
{prior_tracking}
```

Now run web_search (target news between {prior_display_date} and {display_date} -- macro prints, central bank speakers, geopolitical headlines, conviction-trade instruments), then call publish_report with the full markdown narrative, the structured jsonPayload, and the trackingMd content that future runs will read.
"""


PUBLISH_REPORT_TOOL = {
    "name": "publish_report",
    "description": (
        "Publish the new Market Pulse trading report. Call exactly ONCE at the end of your turn "
        "with ALL THREE fields populated. None of the three may be empty, null, or omitted -- "
        "an empty markdown, empty jsonPayload, or empty trackingMd causes the report to be "
        "REJECTED and the entire run fails. If you find yourself low on output budget, prefer "
        "shorter content over an empty field. Do not emit multiple publish_report calls; "
        "compose the full report internally, then submit it once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "markdown": {
                "type": "string",
                "description": "The full narrative trading report in markdown, matching the example layout (macro theme, scorecard, live data table, macro events, instrument analysis, summary, top trades, risks, scenarios, notes).",
            },
            "jsonPayload": {
                "type": "object",
                "description": (
                    "The structured payload the dashboard consumes. Must include all required top-level keys "
                    "(slug, type, date, displayDate, title, markdownFile, lastUpdated, marketSentiment, "
                    "macroTheme, macroOverview, instruments, scorecard, upcomingEvents, topTrades, riskScenarios). "
                    "instruments must be an array of 7 items in the order US30, NAS100, GER40, AUDUSD, GBPCAD, "
                    "XAGUSD, XAUUSD. scorecard must have 7 entries. topTrades must have 3 entries."
                ),
            },
            "trackingMd": {
                "type": "string",
                "description": (
                    "Markdown content for cron_tracking/<id>/last_run.md -- a concise summary that the NEXT "
                    "run will read: biases set this run (table), scorecard vs previous biases, pipeline status, "
                    "key events to track, notes for the next agent."
                ),
            },
        },
        "required": ["markdown", "jsonPayload", "trackingMd"],
    },
}


def call_claude_api(api_key: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    client = anthropic.Anthropic(api_key=api_key)
    web_search_tool = {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": 5,
    }
    # Streaming because Sonnet 4.6 reports can produce > 16K output tokens.
    with client.messages.stream(
        model=MODEL,
        max_tokens=64000,
        thinking={"type": "adaptive"},
        # effort=medium bounds thinking depth -- the report has a fixed structure,
        # so deep reasoning is wasted budget that crowds out the final tool call.
        # (default is high; the previous run blew through 32K output without ever
        # reaching publish_report because thinking + dynamic-filter code execution
        # consumed it all.)
        output_config={"effort": "medium"},
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[web_search_tool, PUBLISH_REPORT_TOOL],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        final = stream.get_final_message()

    print(
        f"  Claude usage: input={final.usage.input_tokens} "
        f"output={final.usage.output_tokens} "
        f"cache_read={final.usage.cache_read_input_tokens} "
        f"cache_creation={final.usage.cache_creation_input_tokens} "
        f"stop_reason={final.stop_reason}",
        file=sys.stderr,
    )

    publish_calls = [
        b for b in final.content
        if getattr(b, "type", None) == "tool_use" and b.name == "publish_report"
    ]
    if not publish_calls:
        block_types = [getattr(b, "type", "?") for b in final.content]
        raise RuntimeError(
            f"Claude did not call publish_report. stop_reason={final.stop_reason} "
            f"content blocks={block_types}"
        )

    # Claude can emit multiple publish_report calls (e.g. a partial early
    # attempt then a final one). Prefer the last one that has all required
    # fields populated; fall back to the last call so the caller's diagnostic
    # path can surface what's missing.
    required = ("markdown", "jsonPayload", "trackingMd")
    for block in reversed(publish_calls):
        if all(block.input.get(f) for f in required):
            if len(publish_calls) > 1:
                print(
                    f"  Note: Claude emitted {len(publish_calls)} publish_report calls; "
                    f"using the last complete one.",
                    file=sys.stderr,
                )
            return block.input
    print(
        f"  WARN: {len(publish_calls)} publish_report call(s) found, none with all "
        f"required fields populated. Returning last call for diagnostic dump.",
        file=sys.stderr,
    )
    return publish_calls[-1].input


def merge_prices_into_payload(
    payload: dict[str, Any],
    quotes: dict[str, dict[str, Any]],
) -> None:
    """Overwrite the LLM's price fields with our pre-fetched yfinance data."""
    for inst in payload.get("instruments", []):
        symbol = inst.get("symbol")
        q = quotes.get(symbol)
        if symbol in INSTRUMENT_NAMES:
            inst["name"] = INSTRUMENT_NAMES[symbol]
        if not q:
            continue
        for field in VOLATILE_FIELDS:
            if q.get(field) is not None:
                inst[field] = q[field]
        if q.get("sparkline"):
            inst["sparkline"] = q["sparkline"]
        if q.get("ohlc"):
            inst["ohlc"] = q["ohlc"]


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F9FF"
    "]"
)


def validate_payload(payload: dict[str, Any], slug: str) -> list[str]:
    errs: list[str] = []
    for k in REQUIRED_TOP_KEYS:
        if k not in payload:
            errs.append(f"missing top-level key: {k}")
    if payload.get("slug") != slug:
        errs.append(f"slug mismatch: expected {slug}, got {payload.get('slug')!r}")
    instruments = payload.get("instruments", [])
    symbols = [i.get("symbol") for i in instruments if isinstance(i, dict)]
    if symbols != EXPECTED_SYMBOLS:
        errs.append(f"instruments order/symbols wrong: {symbols} (expected {EXPECTED_SYMBOLS})")
    if len(payload.get("scorecard", [])) != 7:
        errs.append(f"scorecard length != 7 (got {len(payload.get('scorecard', []))})")
    if len(payload.get("topTrades", [])) != 3:
        errs.append(f"topTrades length != 3 (got {len(payload.get('topTrades', []))})")
    text = json.dumps(payload, ensure_ascii=False)
    if _EMOJI_RE.search(text):
        errs.append("emoji detected in jsonPayload (CRITICAL RULE 1)")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--type", choices=["weekly", "midweek"], help="Override run type (default: derived from weekday).")
    ap.add_argument("--date", help="Override report date YYYY-MM-DD (default: today UTC).")
    ap.add_argument("--dry-run", action="store_true", help="Skip writing files.")
    ap.add_argument("--skip-yfinance", action="store_true", help="Skip yfinance fetch (testing only).")
    args = ap.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    if args.date:
        today = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        today = now

    run_type = determine_run_type(today, args.type)
    slug = slug_for(run_type, today)
    out_md_path = REPORTS_DIR / f"{slug}.md"
    out_json_path = REPORTS_DIR / f"{slug}.json"
    tracking_path = TRACKING_DIR / CRON_IDS[run_type] / "last_run.md"

    if (out_md_path.exists() or out_json_path.exists()) and not args.dry_run:
        print(
            f"Report files for {slug} already exist; refusing to overwrite. "
            f"Delete them and rerun if you want to regenerate.",
            file=sys.stderr,
        )
        return 1

    print(f"Run type: {run_type}  Slug: {slug}", file=sys.stderr)
    print(f"Fetching live quotes for {len(INSTRUMENTS)} instruments...", file=sys.stderr)
    if args.skip_yfinance:
        quotes, failures = {}, ["(yfinance skipped)"]
    else:
        quotes, failures = fetch_all_quotes()
    if failures:
        print(f"  yfinance failures: {failures}", file=sys.stderr)
    if not quotes and not args.skip_yfinance:
        print("ERROR: every yfinance fetch failed -- aborting.", file=sys.stderr)
        return 2

    prior_slug, prior_display_date, prior_tracking = load_prior_tracking(slug, today)
    print(f"Scorecarding against prior report: {prior_slug} ({prior_display_date})", file=sys.stderr)
    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(
        run_type, today, slug,
        prior_slug, prior_display_date, prior_tracking,
        quotes, failures,
    )

    print(f"Calling Claude API ({MODEL})...", file=sys.stderr)
    result = call_claude_api(api_key, system_prompt, user_prompt)

    markdown = result.get("markdown", "")
    payload = result.get("jsonPayload", {})
    tracking_md = result.get("trackingMd", "")
    missing = [
        name for name, val in [
            ("markdown", markdown), ("jsonPayload", payload), ("trackingMd", tracking_md),
        ] if not val
    ]
    if missing:
        print(f"ERROR: publish_report tool_use missing/empty fields: {missing}", file=sys.stderr)
        sizes = {k: (len(v) if isinstance(v, (str, list, dict)) else "n/a") for k, v in result.items()}
        print(f"  Field sizes in returned input: {sizes}", file=sys.stderr)
        debug_path = REPO_ROOT / f"_failed_{slug}_publish_report.json"
        try:
            debug_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print(f"  Raw publish_report input dumped to {debug_path}", file=sys.stderr)
        except Exception as exc:
            print(f"  Could not dump raw input: {exc}", file=sys.stderr)
        return 2

    merge_prices_into_payload(payload, quotes)
    # Normalize the identifiers we control rather than trust the LLM.
    payload["slug"] = slug
    payload["markdownFile"] = f"{slug}.md"
    payload["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    errs = validate_payload(payload, slug)
    if errs:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        if not args.dry_run:
            debug_path = REPO_ROOT / f"_failed_{slug}.json"
            debug_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  (raw payload written to {debug_path} for inspection)", file=sys.stderr)
        return 3

    if args.dry_run:
        print(
            f"--dry-run: would write {out_md_path.name} "
            f"({len(markdown)} bytes), {out_json_path.name}, and "
            f"cron_tracking/{CRON_IDS[run_type]}/last_run.md",
            file=sys.stderr,
        )
        return 0

    out_md_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    out_json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    tracking_path.write_text(tracking_md.rstrip() + "\n", encoding="utf-8")

    print(f"Wrote {out_md_path}", file=sys.stderr)
    print(f"Wrote {out_json_path}", file=sys.stderr)
    print(f"Updated {tracking_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
