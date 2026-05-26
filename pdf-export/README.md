# Market Pulse PDF Export

Generates a high-quality A4 PDF tear sheet for any published Market Pulse report.

## Layout (9 pages for a 7-instrument report)

1. **Cover** — title, outlook chip, macro theme, overview, generated timestamp, version, scannable QR to the live dashboard
2. **Contents** — clickable TOC with deterministic page numbers
3. **Live Market Data + Scorecard** — 7-row data table + scorecard vs the prior report
4. **Top Conviction Trades** — 3 trade cards with Entry / Target / Stop / Rationale
5–8. **Instrument analysis** — 2 instruments per page, each with compact header, S/R cards, 14-day candle chart (SVG), single-line metrics strip, analysis text
9. **Upcoming Events + Risk Scenarios** — event table + 2-column compact scenarios

## Local usage

```bash
cd pdf-export
npm install                                    # one-time
npx tsx generate_report_pdf.tsx                # latest report in manifest
npx tsx generate_report_pdf.tsx 2026-05-24-weekly  # specific slug
```

Output: `../reports/<slug>.pdf`.

## Cron pipeline

`author.yml` runs `generate_report_pdf.tsx` automatically after the orchestrator
writes a new report. The PDF is committed alongside the `.md` and `.json` files
and the ClickUp summary post includes a direct download link to it.

PDF generation is soft-failing in the workflow: a PDF failure does not block
the publish path -- the `.md` + `.json` still commit and the ClickUp post
still goes out (just without the PDF link if generation failed).

## Files

| File | Purpose |
|---|---|
| `generate_report_pdf.tsx` | Main entry. Reads `../reports/<slug>.json`, renders the PDF. |
| `package.json` / `package-lock.json` | Pinned deps: `@react-pdf/renderer`, `react`, `qrcode`, `tsx`. |
| `fonts/` | Inter (400/500/600/700) + JetBrains Mono (400/700). Local files required by react-pdf -- no remote font URLs. |
| `preview/` | Throwaway PNG previews of each page (git-ignored). Generated via `python -c "import fitz; ..."`. |

## Tweaks

All visual choices are constants at the top of `generate_report_pdf.tsx`:

- Palette: `C` object (~80 lines in)
- Decimal places per instrument: `dpFor()`
- Instruments per page: `INSTRUMENTS_PER_PAGE`
- Page sequence: the `<MarketPulsePdf>` component near the bottom
- Cover QR: encoded URL is `${DASHBOARD_URL}/#/${slug}`
- Version label: `PDF_VERSION` constant
