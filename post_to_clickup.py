#!/usr/bin/env python3
"""
Post a Market Pulse report summary to a ClickUp chat channel.

Reads reports/<slug>.json and posts a concise summary (sentiment, macro theme,
top 3 trades, scorecard) to the configured ClickUp channel.

Designed to be called from .github/workflows/author.yml after a successful
commit. Soft-fails on missing token or API errors -- the report is already
live on the dashboard, ClickUp is a side-channel notification, never block
the publish pipeline on a chat post failing.

Usage:
    python post_to_clickup.py <slug>
    # e.g. python post_to_clickup.py 2026-05-24-weekly

Env vars:
    CLICKUP_API_TOKEN     Required (else soft-fails with WARN). Personal API
                          token from https://app.clickup.com/settings/apps
    CLICKUP_WORKSPACE_ID  Default: 9005093620
    CLICKUP_CHANNEL_ID    Default: 8cbxmqm-64592 (Analysis channel)
    DASHBOARD_URL         Default: https://digitaldwight.github.io/market-pulse-dashboard/
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import request, error

REPO_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = REPO_ROOT / "reports"

DEFAULT_WORKSPACE = "9005093620"
DEFAULT_CHANNEL = "8cbxmqm-64592"
DEFAULT_DASHBOARD = "https://digitaldwight.github.io/market-pulse-dashboard/"
# Direct download URL pattern for files in the main branch. Used to link the
# generated PDF in the ClickUp summary so a click pulls the file.
DEFAULT_RAW_PDF_BASE = "https://raw.githubusercontent.com/DigitalDwight/market-pulse-dashboard/main/reports"

# Display form for the JSON's underscore sentiment values.
SENTIMENT_DISPLAY = {
    "BULLISH":         "BULLISH",
    "NEUTRAL_BULLISH": "NEUTRAL-BULLISH",
    "NEUTRAL":         "NEUTRAL",
    "NEUTRAL_BEARISH": "NEUTRAL-BEARISH",
    "BEARISH":         "BEARISH",
}


def build_summary(report: dict, dashboard_url: str, pdf_url: str | None) -> str:
    type_label = report.get("type", "")
    title_prefix = f"MARKET PULSE — {type_label} Trading Report"
    display_date = report.get("displayDate", report.get("date", ""))
    sentiment_raw = report.get("marketSentiment", "")
    sentiment = SENTIMENT_DISPLAY.get(sentiment_raw, sentiment_raw.replace("_", "-"))
    theme = report.get("macroTheme", "")
    slug = report.get("slug", "")

    trade_lines = []
    for t in report.get("topTrades", [])[:3]:
        trade_lines.append(
            f"{t.get('rank', '?')}. **{t.get('instrument', '?')} {t.get('direction', '?')}** "
            f"— Entry {t.get('entry', '?')} / Target {t.get('target', '?')} / Stop {t.get('stop', '?')}"
        )

    scorecard_chips = " · ".join(
        f"{s.get('instrument', '?')} {s.get('verdict', '?')}"
        for s in report.get("scorecard", [])
    )

    report_url = f"{dashboard_url.rstrip('/')}/#/{slug}"

    links = [f"Live dashboard: {report_url}"]
    if pdf_url:
        links.append(f"PDF: {pdf_url}")

    return "\n".join([
        f"**{title_prefix}**",
        f"{display_date}  |  Sentiment: **{sentiment}**",
        "",
        "**Macro theme**",
        theme,
        "",
        "**Top conviction trades**",
        *trade_lines,
        "",
        f"**Scorecard:** {scorecard_chips}",
        "",
        *links,
    ])


def post_to_clickup(token: str, workspace_id: str, channel_id: str, content: str) -> tuple[bool, str]:
    url = (
        f"https://api.clickup.com/api/v3/workspaces/{workspace_id}"
        f"/chat/channels/{channel_id}/messages"
    )
    body = json.dumps({
        "type": "message",
        "content_format": "text/md",
        "content": content,
    }).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            return True, f"{resp.status} {resp.reason}"
    except error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = ""
        return False, f"HTTP {e.code} {e.reason} — {err_body}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: post_to_clickup.py <slug>", file=sys.stderr)
        return 1

    slug = sys.argv[1]
    report_path = REPORTS_DIR / f"{slug}.json"
    if not report_path.exists():
        print(f"ERROR: report not found: {report_path}", file=sys.stderr)
        return 2

    token = os.environ.get("CLICKUP_API_TOKEN")
    if not token:
        print(
            "WARN: CLICKUP_API_TOKEN not set -- skipping ClickUp post.",
            file=sys.stderr,
        )
        return 0  # Soft-fail; report is already live on the dashboard.

    workspace_id = os.environ.get("CLICKUP_WORKSPACE_ID", DEFAULT_WORKSPACE)
    channel_id = os.environ.get("CLICKUP_CHANNEL_ID", DEFAULT_CHANNEL)
    dashboard_url = os.environ.get("DASHBOARD_URL", DEFAULT_DASHBOARD)
    raw_pdf_base = os.environ.get("PDF_RAW_BASE", DEFAULT_RAW_PDF_BASE)

    # Include the PDF download link if the PDF was generated alongside the JSON.
    pdf_path = report_path.with_suffix(".pdf")
    pdf_url = f"{raw_pdf_base.rstrip('/')}/{slug}.pdf" if pdf_path.exists() else None

    report = json.loads(report_path.read_text(encoding="utf-8"))
    content = build_summary(report, dashboard_url, pdf_url)

    print(
        f"Posting {slug} summary ({len(content)} chars) to ClickUp "
        f"channel {channel_id} in workspace {workspace_id}...",
        file=sys.stderr,
    )
    ok, info = post_to_clickup(token, workspace_id, channel_id, content)
    if ok:
        print(f"  OK: {info}", file=sys.stderr)
        return 0

    # Soft-fail: don't fail the workflow just because ClickUp had a hiccup.
    # The report is on the dashboard regardless.
    print(f"  FAILED (non-fatal): {info}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
