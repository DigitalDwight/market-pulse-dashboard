# Market Pulse Dashboard

Self-updating trading intelligence dashboard, fully driven by the weekly /
mid-week trading reports. Hosted on GitHub Pages at
https://digitaldwight.github.io/market-pulse-dashboard/ and embedded into
the Wix site at engvision.co/dashboard.

**Instruments tracked:** US30 · NAS100 · GER40 · AUDUSD · GBPCAD · XAGUSD · XAUUSD

## How the dashboard works

The dashboard is **report-driven**. Every report lives in `reports/` as a
paired `.md` (human-readable) + `.json` (structured payload). On page load,
`index.html` fetches `reports/manifest.json`, then loads the newest report's
`.json` to populate the dashboard.

Each historical report is available in the **Report History** section of the
dashboard:

- Click **"Load on dashboard"** to time-travel — the dashboard re-renders with
  that report's data.
- Click **"View Report"** to read the full `.md` rendered inline in a modal.
- Click **"Download .md"** (in history list and in the topbar) to save the
  source markdown.

A deep link like `#/2026-05-06-midweek` loads that report directly when shared.

## How reports stay current

Two GitHub Actions keep everything in sync — no local machine required.

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| **refresh.yml** | `cron: 0 20 * * 0` (Sun) + `cron: 0 6 * * 3` (Wed) + manual | Rebuilds manifest, pulls live yfinance prices, writes volatile fields into the latest report's `.json`, commits, pushes |
| **rebuild-manifest.yml** | Push to `reports/**` | Regenerates `reports/manifest.json` so newly-added reports appear immediately in the dashboard's History |

This matches the two cron jobs from the Market Pulse system spec (`18fb2115`
and `92ba41d1`).

### What the refresh updates

Volatile fields refreshed every cron run on the **latest** report:
`price`, `previousClose`, `change`, `changePercent`, `dayHigh`, `dayLow`,
`yearHigh`, `yearLow`, `sparkline`, `ohlc`, top-level `lastUpdated`.

Analytical fields are **never** touched by the Action — they're the bias /
analysis / scorecard / trades / scenarios authored in the report. Regenerate
those by adding a new report (next section).

## Adding a new report

1. Create `reports/<yyyy-mm-dd>-<weekly|midweek>.md` — the full markdown
   trading report (any structure).
2. Create `reports/<yyyy-mm-dd>-<weekly|midweek>.json` — the structured
   payload that drives the dashboard. Same shape as the existing files;
   the required top-level keys are:
   ```
   slug, type, date, displayDate, title, markdownFile, lastUpdated,
   marketSentiment, macroTheme, macroOverview, instruments,
   scorecard, upcomingEvents, topTrades, riskScenarios
   ```
3. Commit and push.
4. The **rebuild-manifest** Action runs automatically and updates
   `reports/manifest.json`. The dashboard's History section now shows the
   new report, and because it has the most recent `date` it becomes the
   default view.

If you only update prices (rare), just run **refresh.yml** manually from the
Actions tab.

## File layout

```
.
├── index.html                       single-page dashboard
├── reports/
│   ├── manifest.json                index — auto-generated, sorted newest-first
│   ├── 2026-05-10-weekly.md         full markdown report
│   ├── 2026-05-10-weekly.json       dashboard payload
│   ├── 2026-05-06-midweek.md
│   └── 2026-05-06-midweek.json
├── build_manifest.py                rebuilds manifest from reports/*.json
├── refresh_dashboard.py             yfinance fetcher → writes prices into latest report
├── requirements.txt                 Python deps (just yfinance)
├── .gitignore
└── .github/workflows/
    ├── refresh.yml                  scheduled price refresh
    └── rebuild-manifest.yml         runs on push to reports/**
```

## Local sanity check (optional)

```bash
pip install -r requirements.txt
python3 build_manifest.py
python3 refresh_dashboard.py --dry-run
# Serve locally to test the dashboard:
python3 -m http.server 8000
# → http://localhost:8000
```

## GitHub Pages setup (one-time)

1. Settings → Pages → Source: **"Deploy from a branch"** / `main` / `/ (root)`.
2. Settings → Actions → General → Workflow permissions: **"Read and write
   permissions"** (required so the Actions can push commits).
3. After the first push, trigger **refresh.yml** manually to verify yfinance
   reaches Yahoo and the prices update.

## Troubleshooting

- **"Could not load reports/manifest.json" banner** — the manifest file is
  missing or 404ing. Run `python build_manifest.py` locally then push, or
  trigger the rebuild-manifest workflow from the Actions tab.
- **History list is empty** — manifest loaded but has zero reports.
  Make sure `reports/*.json` exist and that `slug` is a top-level field
  in each.
- **A historical report renders blank** — open dev tools and check the
  fetch for `reports/<slug>.json`. The most common cause is a JSON syntax
  error in the report file.
- **Action fails with "permission denied" on push** — set Workflow
  permissions to "Read and write" (see above).
- **XAGUSD price is stale** — yfinance occasionally can't resolve `SI=F`.
  The script preserves the stale value rather than wiping it. Re-trigger
  the refresh workflow if you want a retry.

## Wix embed

The Wix dashboard page iframes the GitHub Pages URL with embed ID
`770a5456-807c-461a-8840-0c6acfaa4a15`. No changes needed there — the iframe
keeps showing whatever the latest `index.html` looks like.
