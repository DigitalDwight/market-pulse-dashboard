# Last Run: Sunday 12 July 2026 (Weekly Report)
# Next Expected Run: RE-BASELINE RUN (see gap notice below)

## GAP NOTICE: READ THIS BEFORE ANYTHING ELSE

There is a six-week hole in this pipeline. The last published report is
2026-07-12-weekly. Every scheduled author run from 15 July 2026 onward failed
with an Anthropic API 400 "credit balance is too low", twelve consecutive
failures, until the provider was swapped to OpenRouter on 22 August 2026.

`refresh.yml` never failed, because it only pulls yfinance prices and needs no
API key. So the dashboard kept topping up live prices on the 12 July report.
Anyone reading it between 15 July and 22 August saw six-week-old biases, key
levels and conviction trades presented alongside current prices.

## Instructions for the re-baseline run

1. **Do not produce a scorecard against the biases below.** They were set for a
   three-to-five day horizon on 12 July. Grading them against a date six weeks
   later measures nothing. Every scorecard entry for this run takes the verdict
   `NOT GRADED` with the reason `re-baseline after 6-week pipeline outage`.

2. **Do not advance the running record.** It stands at 61 CORRECT / 40
   PARTIALLY / 26 WRONG across 127 calls since inception, and it must be carried
   into your trackingMd unchanged. Adding seven ungraded calls to it would
   corrupt a record built over 127 real ones.

3. **Treat the 12 July levels below as historical reference only.** Silver at
   60.165 and gold at 4,113.70 are six weeks stale. Support and resistance from
   that report should be assumed broken or irrelevant unless your search results
   confirm the level still matters. Do not carry any of these numbers into the
   new report as current structure.

4. **The three 12 July conviction trades are closed and unresolved.** XAUUSD
   long 4,080-4,100, US30 long 52,200-52,400, and GER40 short 25,195-25,280 were
   all left WAITING at publication and were never followed up. Record them as
   abandoned, not as wins or losses, and open three fresh trades.

5. **Establish current market state from your search results, not from this
   file.** The forward events listed in the 12 July report, June CPI on 14 July,
   the BoC decision on 15 July, the ECB on 24 July, have all long since
   resolved. Do not present any of them as upcoming.

## Biases set on 12 July 2026 (HISTORICAL, DO NOT SCORECARD)

| Instrument | Bias | Price at Report |
|---|---|---|
| US30 | NEUTRAL-BULLISH | 52,637 |
| NAS100 | NEUTRAL | 29,825 |
| GER40 | NEUTRAL-BEARISH | 25,067 |
| AUDUSD | NEUTRAL | 0.6952 |
| GBPCAD | NEUTRAL | 1.8957 |
| XAGUSD | NEUTRAL-BEARISH | 60.165 |
| XAUUSD | NEUTRAL-BULLISH | 4,113.70 |

Running record carried forward unchanged: 61 CORRECT / 40 PARTIALLY / 26 WRONG
(127 total calls since inception).

## Pipeline Status

- Report: 2026-07-12-weekly.md (last successful author run)
- Outage: 15 July 2026 to 22 August 2026, twelve failed author runs
- Cause: Anthropic API credit exhausted; refresh.yml unaffected and kept running
- Fix: provider swapped to OpenRouter on 22 August 2026
- This run: RE-BASELINE. No scorecard, running record frozen, fresh biases.
