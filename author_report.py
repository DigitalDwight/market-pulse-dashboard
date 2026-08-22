#!/usr/bin/env python3
"""
Market Pulse -- author a new trading report via OpenRouter (DeepSeek).

Reads cron_tracking/<cron_id>/last_run.md for prior-run context, pre-fetches
yfinance prices for the 7 tracked instruments, calls the model (OpenRouter web
plugin + publish_report tool) to author the analytical content, then writes:
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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import openai
except ImportError:
    print("openai not installed. Run: pip install -r requirements.txt", file=sys.stderr)
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

# Full instrument names live here, not in the LLM output — the model only has to
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

# --- Provider: OpenRouter (OpenAI-compatible) -------------------------------
# Swapped off the Anthropic API on 2026-08-16. The Anthropic key ran out of
# credit and every author run had been failing 400 "credit balance is too low"
# since 2026-08-05, so the dashboard sat on the 12 Jul report for five weeks
# while refresh.yml kept topping up prices on it.
#
# OpenRouter is OpenAI-wire-compatible, so this is a plain base_url + key swap
# rather than a rewrite. DeepSeek v3.2 costs ~$0.02/run against ~$0.30-0.60 on
# Sonnet, and its 65,536-token output ceiling clears the ~24k tokens a full
# report set (markdown + jsonPayload + trackingMd) actually needs.
#
# Override MARKET_PULSE_MODEL to trade cost for quality without touching code:
#   deepseek/deepseek-v4-flash  cheaper  (~$0.005/run, 384k out)
#   deepseek/deepseek-v4-pro    stronger (~$0.09/run,  393k out)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = os.environ.get("MARKET_PULSE_MODEL", "deepseek/deepseek-v3.2")

# Max output tokens to request. A full report set measures ~24k tokens; 48k
# leaves headroom without exceeding v3.2's 65,536 ceiling. Lower it if you
# switch to a model with a smaller cap.
MAX_OUTPUT_TOKENS = int(os.environ.get("MARKET_PULSE_MAX_TOKENS", "48000"))

# OpenRouter's web plugin (Exa-backed) runs the searches server-side and injects
# the results into the prompt BEFORE the model sees it. This replaces
# Anthropic's web_search_20260209 server tool, which has no OpenRouter
# equivalent. The model does not call a search tool itself -- it just receives
# grounded results, which is why the prompt below describes search that way.
#
# Grounding is deliberately mandatory for a *trading* report: without it the
# model confabulates economic prints and central-bank headlines, and the
# scorecard becomes fiction. Set MARKET_PULSE_WEB_SEARCH=0 only for offline
# schema testing, never for a published run.
WEB_SEARCH_ENABLED = os.environ.get("MARKET_PULSE_WEB_SEARCH", "1") != "0"
WEB_SEARCH_MAX_RESULTS = int(os.environ.get("MARKET_PULSE_WEB_RESULTS", "5"))


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
2. Use verbatim words "CORRECT", "PARTIALLY", "WRONG" in scorecards. Never "right" / "incorrect" / "wrong-ish". The single exception is "NOT GRADED", which is used only when the prior tracking file explicitly instructs a re-baseline because the pipeline missed runs. Never reach for it to avoid a hard judgement call.
3. Conviction trades MUST include Entry, Target, Stop, and Rationale.
4. State broken support / broken resistance explicitly (e.g. "broken support 4,635 now firm as resistance").
5. Conditional biases must state the condition explicitly (e.g. "BULLISH conditional on RBA delivering hike").
6. Signal field uses underscore form in JSON: BULLISH, BEARISH, NEUTRAL, NEUTRAL_BULLISH, NEUTRAL_BEARISH. In the markdown narrative the hyphen form (NEUTRAL-BULLISH) is fine.
7. signalStrength: integer in [-100, 100]. eventImpactProbability: integer in [0, 100].
8. Ground the report in current macro reality using ONLY the web search results supplied to you and the live price block: economic prints in the last 7 days, central bank speakers, geopolitical developments, earnings, commodity moves, ETF flows. Do not invent data. If you cannot verify a number from the supplied sources, omit it rather than estimating it.
9. All price levels in the narrative must match the live data block in the user message.

# Process

1. Read the previous tracking file in the user message. It contains the biases set last time -- those are the things you will scorecard against.
2. Read the web search results supplied with this request. They are retrieved for you automatically -- there is no search tool for you to call, so do not attempt one and do not ask for more results. Use them to establish what actually happened in markets since the previous run, and verify the directional outcome for each instrument so the scorecard is honest. Where the results do not cover an instrument, say the outcome could not be verified rather than guessing it.
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

Web search results covering the period between {prior_display_date} and {display_date} (macro prints, central bank speakers, geopolitical headlines, conviction-trade instruments) have been retrieved and supplied with this request. Read them, then call publish_report with the full markdown narrative, the structured jsonPayload, and the trackingMd content that future runs will read.
"""


# OpenAI/OpenRouter function-calling form: the schema nests under
# function.parameters, where the Anthropic form used a top-level input_schema.
PUBLISH_REPORT_TOOL = {
  "type": "function",
  "function": {
    "name": "publish_report",
    "description": (
        "Publish the new Market Pulse trading report. Call exactly ONCE at the end of your turn "
        "with ALL THREE fields populated. None of the three may be empty, null, or omitted -- "
        "an empty markdown, empty jsonPayload, or empty trackingMd causes the report to be "
        "REJECTED and the entire run fails. If you find yourself low on output budget, prefer "
        "shorter content over an empty field. Do not emit multiple publish_report calls; "
        "compose the full report internally, then submit it once."
    ),
    "parameters": {
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
  },
}


# Transient streaming errors we retry the whole API call on. The SDK's built-in
# retry doesn't help mid-stream -- once the stream has started emitting tokens,
# a dropped connection means the partial response is lost.
# Wed 10 Jun 2026 (run 27265537419) died with httpcore.RemoteProtocolError
# 3.5 min into a normal-length call -- that's the failure mode this guards.
# Kept on the OpenRouter swap: it's an httpx-level failure mode, not an
# Anthropic-specific one, and OpenRouter proxies to upstream providers so a
# mid-stream drop is if anything more likely, not less.
_RETRYABLE_EXC_NAMES = frozenset({
    "RemoteProtocolError",   # httpcore / httpx -- peer closed connection
    "ReadError",             # httpx / httpcore
    "ConnectError",          # httpx / httpcore
    "ReadTimeout",           # httpx
    "ConnectTimeout",        # httpx
    "WriteTimeout",          # httpx
    "WriteError",            # httpx
    "PoolTimeout",           # httpx
    "ProtocolError",         # h11 lower-level
})
MAX_API_ATTEMPTS = 3


def _is_retryable_stream_error(exc: BaseException) -> bool:
    # openai.APIConnectionError covers timeouts and connection resets;
    # InternalServerError covers upstream 5xx. RateLimitError is added because
    # OpenRouter shares upstream provider capacity, so a 429 here is a transient
    # queueing signal rather than a hard quota wall the way it was on Anthropic.
    if isinstance(
        exc,
        (openai.APIConnectionError, openai.InternalServerError, openai.RateLimitError),
    ):
        return True
    return type(exc).__name__ in _RETRYABLE_EXC_NAMES


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    Best-effort recovery of a JSON object from free-form model content.

    Open-weight models occasionally answer in prose with a fenced JSON block
    instead of emitting a tool call. Rather than fail the whole run on that, we
    try to recover the payload. Scans for the outermost balanced {...} while
    respecting string literals and escapes, so a brace inside the report
    narrative doesn't truncate the match the way a naive regex would.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def call_llm_api(api_key: str, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    client = openai.OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        # A full report is a long generation. The SDK default (10 min) is not
        # enough headroom on a slow upstream provider; the workflow allows 60.
        timeout=1800.0,
        max_retries=0,  # we own the retry loop below
    )

    request_kwargs: dict[str, Any] = dict(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[PUBLISH_REPORT_TOOL],
        # Force the tool rather than merely offering it. DeepSeek is more prone
        # than Sonnet was to answering in prose when a tool is optional, and a
        # prose answer means no publishable payload.
        tool_choice={"type": "function", "function": {"name": "publish_report"}},
        # Streaming is required, not stylistic: a ~24k-token generation held on
        # a non-streaming connection gets dropped by intermediaries well before
        # it completes.
        stream=True,
        stream_options={"include_usage": True},
        extra_headers={
            "HTTP-Referer": "https://digitaldwight.github.io/market-pulse-dashboard/",
            "X-Title": "Market Pulse Dashboard",
        },
    )
    if WEB_SEARCH_ENABLED:
        # OpenRouter runs these searches server-side and prepends the results to
        # the prompt. Replaces Anthropic's web_search server tool.
        request_kwargs["extra_body"] = {
            "plugins": [{"id": "web", "max_results": WEB_SEARCH_MAX_RESULTS}]
        }
    else:
        print(
            "  WARNING: web grounding DISABLED (MARKET_PULSE_WEB_SEARCH=0). "
            "The report will not be grounded in real market events -- schema "
            "testing only, do not publish this.",
            file=sys.stderr,
        )

    tool_args = ""
    content_text = ""
    finish_reason = None
    usage = None

    for attempt in range(1, MAX_API_ATTEMPTS + 1):
        tool_args, content_text, finish_reason, usage = "", "", None, None
        try:
            stream = client.chat.completions.create(**request_kwargs)
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                if delta.content:
                    content_text += delta.content
                # Tool-call arguments arrive as a stream of string fragments
                # that must be concatenated in order before parsing. We pin to
                # the publish_report call and ignore any other index, since
                # tool_choice forces exactly one.
                for tc in (delta.tool_calls or []):
                    fn = getattr(tc, "function", None)
                    if fn is not None and fn.arguments:
                        tool_args += fn.arguments
            break
        except Exception as exc:
            if not _is_retryable_stream_error(exc):
                raise
            if attempt == MAX_API_ATTEMPTS:
                print(
                    f"  Stream error on attempt {attempt}/{MAX_API_ATTEMPTS} "
                    f"({type(exc).__name__}: {exc}) -- no retries left.",
                    file=sys.stderr,
                )
                raise
            wait_s = 30 * attempt  # 30s, 60s
            print(
                f"  Stream error on attempt {attempt}/{MAX_API_ATTEMPTS} "
                f"({type(exc).__name__}: {exc}). Retrying in {wait_s}s...",
                file=sys.stderr,
            )
            time.sleep(wait_s)

    if usage is not None:
        print(
            f"  {MODEL} usage: input={getattr(usage, 'prompt_tokens', '?')} "
            f"output={getattr(usage, 'completion_tokens', '?')} "
            f"finish_reason={finish_reason}",
            file=sys.stderr,
        )
    else:
        print(f"  {MODEL} finish_reason={finish_reason} (no usage reported)", file=sys.stderr)

    # A length-capped generation yields truncated, unparseable tool arguments.
    # Say so explicitly -- otherwise it surfaces as a confusing JSON error.
    if finish_reason == "length":
        raise RuntimeError(
            f"Model hit the output cap (max_tokens={MAX_OUTPUT_TOKENS}) before finishing "
            f"publish_report. Raise MARKET_PULSE_MAX_TOKENS, or switch "
            f"MARKET_PULSE_MODEL to one with a larger output ceiling."
        )

    if tool_args:
        try:
            parsed = json.loads(tool_args)
        except json.JSONDecodeError as exc:
            debug_path = REPO_ROOT / "_failed_tool_args.json"
            try:
                debug_path.write_text(tool_args, encoding="utf-8")
                hint = f" Raw arguments dumped to {debug_path}."
            except Exception:
                hint = ""
            raise RuntimeError(
                f"publish_report arguments were not valid JSON ({exc}). "
                f"Received {len(tool_args)} chars, finish_reason={finish_reason}.{hint}"
            ) from exc
        if isinstance(parsed, dict):
            return parsed
        raise RuntimeError(
            f"publish_report arguments parsed to {type(parsed).__name__}, expected object."
        )

    # Fallback: no tool call, but the model may have emitted the payload as
    # prose/fenced JSON. Recover it rather than throwing the whole run away.
    if content_text:
        recovered = _extract_json_object(content_text)
        if recovered is not None:
            print(
                "  Note: model returned content instead of a tool call; "
                "recovered the JSON payload from the message body.",
                file=sys.stderr,
            )
            return recovered

    raise RuntimeError(
        f"Model did not call publish_report and no JSON payload could be recovered "
        f"from its message. finish_reason={finish_reason}, "
        f"content length={len(content_text)}"
    )


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

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print(
            "ERROR: OPENROUTER_API_KEY not set. Create a key at "
            "https://openrouter.ai/keys and add it as a repo secret.",
            file=sys.stderr,
        )
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

    grounding = f"web grounding on, {WEB_SEARCH_MAX_RESULTS} results" if WEB_SEARCH_ENABLED else "web grounding OFF"
    print(f"Calling OpenRouter ({MODEL}; {grounding})...", file=sys.stderr)
    result = call_llm_api(api_key, system_prompt, user_prompt)

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

    # Emit the slug to a known file so the workflow's downstream PDF step
    # can pick up the right report. Reading manifest.reports[0].slug doesn't
    # work for backfills -- a backfill of an older date sorts behind newer
    # already-published reports in the manifest. (.gitignored.)
    (REPO_ROOT / "_LAST_SLUG").write_text(slug, encoding="utf-8")

    print(f"Wrote {out_md_path}", file=sys.stderr)
    print(f"Wrote {out_json_path}", file=sys.stderr)
    print(f"Updated {tracking_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
