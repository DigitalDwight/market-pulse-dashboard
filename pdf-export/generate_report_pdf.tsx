/**
 * Market Pulse — high-quality PDF generator for a published trading report.
 *
 * Reads ../reports/<slug>.json and writes ../reports/<slug>.pdf.
 *
 * Design: print-friendly light theme with dark sentiment accents, A4 portrait,
 * Inter (body) + JetBrains Mono (prices). Fixed header on every page,
 * page numbers in the footer.
 *
 * Usage:
 *   npx tsx generate_report_pdf.tsx              # latest report in manifest
 *   npx tsx generate_report_pdf.tsx 2026-05-24-weekly
 */

import React from "react";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  Document, Page, Text, View, Image, StyleSheet, Font, Link,
  Svg, Rect, Line, Path, renderToFile,
} from "@react-pdf/renderer";
import QRCode from "qrcode";

const PDF_VERSION = "v1.0";
const DASHBOARD_URL = "https://digitaldwight.github.io/market-pulse-dashboard";

// ---------------------------------------------------------------------------
//  Paths and report loading
// ---------------------------------------------------------------------------

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const REPORTS_DIR = path.join(REPO_ROOT, "reports");
const FONTS_DIR = path.join(__dirname, "fonts");

function resolveSlug(arg: string | undefined): string {
  if (arg) return arg;
  const manifest = JSON.parse(
    fs.readFileSync(path.join(REPORTS_DIR, "manifest.json"), "utf-8"),
  );
  return manifest.reports[0].slug;
}

const slug = resolveSlug(process.argv[2]);
const REPORT_JSON_PATH = path.join(REPORTS_DIR, `${slug}.json`);
const OUTPUT_PATH = path.join(REPORTS_DIR, `${slug}.pdf`);

if (!fs.existsSync(REPORT_JSON_PATH)) {
  console.error(`Report not found: ${REPORT_JSON_PATH}`);
  process.exit(1);
}
// Preprocess: the reports use "--" as an em-dash placeholder (a holdover from
// the no-emoji rule and Markdown-friendly source). PDF body text reads better
// with real em-dashes, so swap them site-wide before rendering.
function dashify(s: unknown): unknown {
  if (typeof s === "string") return s.replace(/(\s)--(\s)/g, "$1—$2").replace(/^--\s/, "— ");
  if (Array.isArray(s)) return s.map(dashify);
  if (s && typeof s === "object") {
    const out: any = {};
    for (const [k, v] of Object.entries(s)) out[k] = dashify(v);
    return out;
  }
  return s;
}
const report: any = dashify(JSON.parse(fs.readFileSync(REPORT_JSON_PATH, "utf-8")));

// ---------------------------------------------------------------------------
//  Fonts (local files only — remote URLs do not work in react-pdf)
// ---------------------------------------------------------------------------

Font.register({
  family: "Inter",
  fonts: [
    { src: path.join(FONTS_DIR, "Inter-Regular.ttf"),  fontWeight: 400 },
    { src: path.join(FONTS_DIR, "Inter-Medium.ttf"),   fontWeight: 500 },
    { src: path.join(FONTS_DIR, "Inter-SemiBold.ttf"), fontWeight: 600 },
    { src: path.join(FONTS_DIR, "Inter-Bold.ttf"),     fontWeight: 700 },
  ],
});
Font.register({
  family: "JetBrains Mono",
  fonts: [
    { src: path.join(FONTS_DIR, "JetBrainsMono-Regular.ttf"), fontWeight: 400 },
    { src: path.join(FONTS_DIR, "JetBrainsMono-Bold.ttf"),    fontWeight: 700 },
  ],
});
// Custom fonts lack hyphenation dictionaries — disable hyphenation to prevent
// broken words / crashes.
Font.registerHyphenationCallback((word) => [word]);

// ---------------------------------------------------------------------------
//  Color palette + helpers
// ---------------------------------------------------------------------------

const C = {
  bg:        "#ffffff",
  panel:     "#f8f9fb",
  panel2:    "#f1f3f7",
  border:    "#e3e7ee",
  borderStrong: "#cfd5e0",
  text:      "#0f172a",
  textMuted: "#5b6577",
  textFaint: "#94a0b3",
  accent:    "#0d6efd",
  bull:      "#15803d",
  bullBg:    "#e7f6ed",
  bear:      "#b91c1c",
  bearBg:    "#fdecec",
  neutral:   "#475569",
  neutralBg: "#eef1f6",
  dark:      "#0b1220",
} as const;

function sentimentColor(s: string): { fg: string; bg: string } {
  const v = (s || "").toUpperCase();
  if (v.includes("BULLISH") && !v.includes("BEARISH")) return { fg: C.bull, bg: C.bullBg };
  if (v.includes("BEARISH") && !v.includes("BULLISH")) return { fg: C.bear, bg: C.bearBg };
  return { fg: C.neutral, bg: C.neutralBg };
}
function signalColor(signal: string, strength?: number): string {
  const s = (signal || "").toUpperCase();
  if (s.includes("BULLISH") && !s.includes("BEARISH")) return C.bull;
  if (s.includes("BEARISH") && !s.includes("BULLISH")) return C.bear;
  return C.neutral;
}
function verdictColor(v: string): string {
  const t = (v || "").toUpperCase();
  if (t === "CORRECT")    return C.bull;
  if (t === "WRONG")      return C.bear;
  if (t === "PARTIALLY")  return "#b45309"; // amber
  return C.neutral;
}
function impactColor(impact: string): string {
  const t = (impact || "").toLowerCase();
  if (t.includes("very high")) return C.bear;
  if (t === "high")            return "#b45309";
  if (t === "medium")          return C.neutral;
  return C.textFaint;
}
// Decimal-places convention per instrument so price + change render at the
// same precision (e.g. XAGUSD price 76.20 + change -0.55, both 2dp).
function dpFor(sym?: string): number {
  if (!sym) return 2;
  const s = sym.toUpperCase();
  if (s === "US30" || s === "NAS100" || s === "GER40" || s === "XAUUSD") return 0;
  if (s === "XAGUSD") return 2;
  if (/^[A-Z]{6}$/.test(s)) return 4;  // FX pairs (AUDUSD, GBPCAD, etc.)
  return 2;
}
function fmtPrice(v: number | undefined, sym?: string): string {
  if (v === undefined || v === null || isNaN(v as number)) return "—";
  const dp = dpFor(sym);
  return (v as number).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
function fmtPct(v: number | undefined): string {
  if (v === undefined || v === null || isNaN(v as number)) return "—";
  return (v as number).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function shortSignal(s: string): string {
  // Compact labels so the rightmost data-table column never overflows.
  const v = (s || "").toUpperCase().replace(/_/g, "-");
  if (v === "NEUTRAL-BULLISH") return "NEU·BULL";
  if (v === "NEUTRAL-BEARISH") return "NEU·BEAR";
  return v;
}
function cleanTitle(t: string): string {
  // Old reports used "--" as an em-dash placeholder; render it as a real em-dash.
  return (t || "").replace(/\s*--\s*/g, " — ");
}
function sign(v: number | undefined): string {
  if (v === undefined || v === null || isNaN(v as number)) return "";
  return (v as number) > 0 ? "+" : "";
}
function todayStr(): string {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
//  Candlestick chart (SVG) — drawn from inst.ohlc, 14 days, ~210x110pt
// ---------------------------------------------------------------------------

const CandleChart: React.FC<{ ohlc: any[]; width?: number; height?: number }> = ({
  ohlc, width = 210, height = 110,
}) => {
  const data = (ohlc || []).filter(c =>
    c && [c.open, c.high, c.low, c.close].every(v => typeof v === "number" && !isNaN(v))
  );
  if (data.length < 2) {
    return (
      <View style={{ width, height, justifyContent: "center", alignItems: "center", border: `0.5pt solid ${C.border}`, borderRadius: 4 }}>
        <Text style={{ fontSize: 8, color: C.textFaint }}>insufficient OHLC data</Text>
      </View>
    );
  }
  const pad = { top: 8, right: 6, bottom: 14, left: 6 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;
  const hi = Math.max(...data.map(c => c.high));
  const lo = Math.min(...data.map(c => c.low));
  const range = (hi - lo) || 1;
  const xStep = w / data.length;
  const bodyW = Math.max(2, xStep * 0.62);

  const yPx = (price: number) => pad.top + (1 - (price - lo) / range) * h;

  // 3 gridlines (top/mid/bottom of price range)
  const gridY = [hi, lo + range / 2, lo].map(yPx);

  return (
    <View>
      <Svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
        <Rect x={0} y={0} width={width} height={height} fill={C.panel} rx={4} ry={4} />
        {gridY.map((y, i) => (
          <Line key={`g${i}`} x1={pad.left} y1={y} x2={width - pad.right} y2={y}
                stroke={C.border} strokeWidth={0.4} />
        ))}
        {data.map((c, i) => {
          const cx = pad.left + xStep * i + xStep / 2;
          const yHigh = yPx(c.high);
          const yLow  = yPx(c.low);
          const yOpen = yPx(c.open);
          const yClose = yPx(c.close);
          const color = c.close >= c.open ? C.bull : C.bear;
          const bodyTop = Math.min(yOpen, yClose);
          const bodyH = Math.max(0.6, Math.abs(yOpen - yClose));
          return (
            <React.Fragment key={i}>
              <Line x1={cx} y1={yHigh} x2={cx} y2={yLow} stroke={color} strokeWidth={0.6} />
              <Rect x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} />
            </React.Fragment>
          );
        })}
      </Svg>
      {/* Date footer rendered outside the SVG (react-pdf Text doesn't position
          inside an Svg viewBox via absolute positioning). */}
      <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 2 }}>
        <Text style={{ fontSize: 6, color: C.textFaint }}>{data[0].date}</Text>
        <Text style={{ fontSize: 6, color: C.textFaint }}>{data[data.length - 1].date}</Text>
      </View>
    </View>
  );
};

// ---------------------------------------------------------------------------
//  Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  page: {
    fontFamily: "Inter",
    fontSize: 9.5,
    color: C.text,
    backgroundColor: C.bg,
    paddingTop:    52,
    paddingBottom: 44,
    paddingHorizontal: 36,
    lineHeight: 1.4,
  },
  // Fixed header / footer
  header: {
    position: "absolute", top: 18, left: 36, right: 36,
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingBottom: 8, borderBottom: `0.5pt solid ${C.border}`,
  },
  headerTitle: { fontSize: 9, fontWeight: 700, letterSpacing: 1.2, color: C.dark },
  headerMeta:  { fontSize: 8, color: C.textMuted },
  footer: {
    position: "absolute", bottom: 18, left: 36, right: 36,
    flexDirection: "row", justifyContent: "space-between",
    paddingTop: 6, borderTop: `0.5pt solid ${C.border}`,
    fontSize: 8, color: C.textFaint,
  },
  // Cover
  coverWrap: { paddingTop: 60, flexGrow: 1, position: "relative" },
  coverEyebrow: { fontSize: 9, color: C.textMuted, letterSpacing: 2.5, marginBottom: 10, fontWeight: 600 },
  coverTitle:  { fontSize: 30, fontWeight: 700, color: C.dark, lineHeight: 1.15, marginBottom: 14 },
  coverDate:   { fontSize: 13, color: C.textMuted, marginBottom: 30 },
  sentimentChip: {
    alignSelf: "flex-start",
    paddingVertical: 6, paddingHorizontal: 14,
    borderRadius: 999, marginBottom: 36, fontSize: 10, fontWeight: 700, letterSpacing: 0.8,
  },
  // Sections
  section: { marginTop: 18 },
  sectionHead: {
    fontSize: 9, color: C.textMuted, letterSpacing: 2, fontWeight: 700,
    paddingBottom: 5, borderBottom: `0.5pt solid ${C.border}`, marginBottom: 10,
  },
  body: { fontSize: 10, color: C.text, lineHeight: 1.55 },
  themeText: { fontSize: 11, color: C.text, lineHeight: 1.55 },
  // Generic table
  tRow: { flexDirection: "row", borderBottom: `0.5pt solid ${C.border}`, paddingVertical: 6 },
  tHead: { fontSize: 7.5, color: C.textMuted, letterSpacing: 1, fontWeight: 700, textTransform: "uppercase" },
  tCell: { fontSize: 9.5 },
  mono: { fontFamily: "JetBrains Mono", fontSize: 9 },
  // Cards
  card: {
    border: `0.5pt solid ${C.border}`, borderRadius: 6,
    padding: 12, marginBottom: 8, backgroundColor: C.bg,
  },
  cardHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  // Signal chip
  signalChip: {
    paddingVertical: 2, paddingHorizontal: 8, borderRadius: 4,
    fontSize: 8, fontWeight: 700, letterSpacing: 0.5,
  },
  // Trade card
  tradeCard: {
    border: `0.5pt solid ${C.borderStrong}`, borderRadius: 8,
    padding: 14, marginBottom: 10, backgroundColor: C.panel,
  },
});

// ---------------------------------------------------------------------------
//  Page chrome
// ---------------------------------------------------------------------------

const Chrome: React.FC = () => (
  <>
    <View style={styles.header} fixed>
      <Text style={styles.headerTitle}>MARKET PULSE  ·  {report.type?.toUpperCase()} REPORT</Text>
      <Text style={styles.headerMeta}>{report.displayDate}</Text>
    </View>
    <Text
      style={styles.footer}
      fixed
      render={({ pageNumber, totalPages }) => `Generated ${todayStr()}     digitaldwight.github.io/market-pulse-dashboard`}
    />
    <Text
      fixed
      style={{
        position: "absolute", bottom: 22, right: 36,
        fontSize: 8, color: C.textFaint,
      }}
      render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`}
    />
  </>
);

// ---------------------------------------------------------------------------
//  Sub-components
// ---------------------------------------------------------------------------

const Cover: React.FC<{ qrPng: Buffer; generatedAt: string; reportUrl: string }> = ({ qrPng, generatedAt, reportUrl }) => {
  const s = sentimentColor(report.marketSentiment);
  return (
    <View style={styles.coverWrap}>
      <Text style={styles.coverEyebrow}>MARKET PULSE  ·  {report.type?.toUpperCase()} TRADING REPORT</Text>
      <Text style={styles.coverTitle}>{cleanTitle(report.title) || `${report.type} Trading Report`}</Text>
      <Text style={styles.coverDate}>{report.displayDate}</Text>
      <Text style={[styles.sentimentChip, { backgroundColor: s.bg, color: s.fg }]}>
        OUTLOOK · {(report.marketSentiment || "").replace(/_/g, "-")}
      </Text>
      <View style={{ marginTop: 4 }}>
        <Text style={[styles.sectionHead]}>MACRO THEME</Text>
        <Text style={styles.themeText}>{report.macroTheme}</Text>
      </View>
      {report.macroOverview ? (
        <View style={{ marginTop: 22 }}>
          <Text style={[styles.sectionHead]}>OVERVIEW</Text>
          <Text style={styles.body}>{report.macroOverview}</Text>
        </View>
      ) : null}

      {/* QR + version pinned near the bottom of the cover */}
      <View style={{
        position: "absolute", bottom: 0, left: 0, right: 0,
        flexDirection: "row", justifyContent: "space-between", alignItems: "flex-end",
      }}>
        <View style={{ flex: 1, paddingRight: 16 }}>
          <Text style={{ fontSize: 7, color: C.textMuted, letterSpacing: 1.2, fontWeight: 700, marginBottom: 3 }}>
            GENERATED
          </Text>
          <Text style={[styles.mono, { fontSize: 9, color: C.text, fontWeight: 600 }]}>
            {generatedAt}
          </Text>
          <Text style={{ fontSize: 8, color: C.textMuted, marginTop: 6 }}>
            Market Pulse PDF Exporter  ·  {PDF_VERSION}
          </Text>
          <Text style={{ fontSize: 7.5, color: C.textFaint, marginTop: 2 }}>
            Scan to open this report on the live dashboard.
          </Text>
        </View>
        <View style={{ alignItems: "center" }}>
          <Image src={{ data: qrPng, format: "png" }} style={{ width: 80, height: 80 }} />
          <Text style={{ fontSize: 7, color: C.textMuted, marginTop: 4, maxWidth: 100, textAlign: "center" }}>
            {reportUrl.replace(/^https?:\/\//, "")}
          </Text>
        </View>
      </View>
    </View>
  );
};

const Scorecard: React.FC = () => (
  <View style={styles.section} wrap={false} id="scorecard">
    <Text style={styles.sectionHead}>SCORECARD VS PREVIOUS REPORT</Text>
    <View style={[styles.tRow, { borderBottom: `0.75pt solid ${C.borderStrong}`, paddingBottom: 4 }]}>
      <Text style={[styles.tHead, { flex: 1.2 }]}>Instrument</Text>
      <Text style={[styles.tHead, { flex: 1.5 }]}>Previous bias</Text>
      <Text style={[styles.tHead, { flex: 3 }]}>Result</Text>
      <Text style={[styles.tHead, { flex: 1, textAlign: "right" }]}>Verdict</Text>
    </View>
    {(report.scorecard || []).map((row: any, i: number) => (
      <View style={styles.tRow} key={i}>
        <Text style={[styles.tCell, { flex: 1.2, fontWeight: 600 }]}>{row.instrument}</Text>
        <Text style={[styles.tCell, { flex: 1.5, color: C.textMuted }]}>
          {(row.previousBias || "").replace(/_/g, "-")}
        </Text>
        <Text style={[styles.tCell, { flex: 3, color: C.text }]}>{row.result}</Text>
        <Text style={[styles.tCell, {
          flex: 1, textAlign: "right",
          color: verdictColor(row.verdict), fontWeight: 700, fontSize: 9,
        }]}>{row.verdict}</Text>
      </View>
    ))}
  </View>
);

const LiveData: React.FC = () => (
  <View style={styles.section} id="data">
    <Text style={styles.sectionHead}>LIVE MARKET DATA</Text>
    <View style={[styles.tRow, { borderBottom: `0.75pt solid ${C.borderStrong}`, paddingBottom: 4 }]}>
      <Text style={[styles.tHead, { flex: 1 }]}>Instrument</Text>
      <Text style={[styles.tHead, { flex: 1.2, textAlign: "right" }]}>Price</Text>
      <Text style={[styles.tHead, { flex: 1, textAlign: "right" }]}>Day chg %</Text>
      <Text style={[styles.tHead, { flex: 1.6, textAlign: "right" }]}>Day range</Text>
      <Text style={[styles.tHead, { flex: 1.6, textAlign: "right" }]}>52W range</Text>
      <Text style={[styles.tHead, { flex: 1.5, textAlign: "right" }]}>Signal</Text>
    </View>
    {(report.instruments || []).map((inst: any, i: number) => {
      const chg = inst.changePercent ?? 0;
      const chgCol = chg > 0 ? C.bull : chg < 0 ? C.bear : C.neutral;
      return (
        <View style={styles.tRow} key={i}>
          <Text style={[styles.tCell, { flex: 1, fontWeight: 600 }]}>{inst.symbol}</Text>
          <Text style={[styles.mono, { flex: 1.2, textAlign: "right", fontWeight: 700 }]}>{fmtPrice(inst.price, inst.symbol)}</Text>
          <Text style={[styles.mono, { flex: 1, textAlign: "right", color: chgCol, fontWeight: 700 }]}>
            {sign(chg)}{fmtPct(chg)}%
          </Text>
          <Text style={[styles.mono, { flex: 1.6, textAlign: "right", color: C.textMuted }]}>
            {fmtPrice(inst.dayLow, inst.symbol)} – {fmtPrice(inst.dayHigh, inst.symbol)}
          </Text>
          <Text style={[styles.mono, { flex: 1.6, textAlign: "right", color: C.textMuted }]}>
            {fmtPrice(inst.yearLow, inst.symbol)} – {fmtPrice(inst.yearHigh, inst.symbol)}
          </Text>
          <Text style={[styles.tCell, {
            flex: 1.5, textAlign: "right",
            color: signalColor(inst.signal), fontWeight: 700, fontSize: 8.5,
          }]}>
            {shortSignal(inst.signal)}
          </Text>
        </View>
      );
    })}
  </View>
);

const TopTrades: React.FC = () => (
  <View style={styles.section} id="trades">
    <Text style={[styles.sectionHead, { marginBottom: 14 }]}>TOP CONVICTION TRADES</Text>
    {(report.topTrades || []).map((t: any, i: number) => {
      const dirCol = (t.direction || "").toUpperCase() === "LONG" ? C.bull : C.bear;
      return (
        <View style={styles.tradeCard} key={i} wrap={false}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
            <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
              <Text style={{ fontSize: 11, color: C.textFaint, fontWeight: 700 }}>#{t.rank}</Text>
              <Text style={{ fontSize: 16, fontWeight: 700 }}>{t.instrument}</Text>
              <Text style={{ fontSize: 11, fontWeight: 700, color: dirCol, letterSpacing: 1 }}>
                {(t.direction || "").toUpperCase()}
              </Text>
            </View>
          </View>
          <View style={{ flexDirection: "row", gap: 14, marginBottom: 10 }}>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 7.5, color: C.textMuted, letterSpacing: 1, fontWeight: 700 }}>ENTRY</Text>
              <Text style={[styles.mono, { fontSize: 11, marginTop: 2, fontWeight: 700 }]}>{t.entry}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 7.5, color: C.textMuted, letterSpacing: 1, fontWeight: 700 }}>TARGET</Text>
              <Text style={[styles.mono, { fontSize: 11, marginTop: 2, fontWeight: 700, color: C.bull }]}>{t.target}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ fontSize: 7.5, color: C.textMuted, letterSpacing: 1, fontWeight: 700 }}>STOP</Text>
              <Text style={[styles.mono, { fontSize: 11, marginTop: 2, fontWeight: 700, color: C.bear }]}>{t.stop}</Text>
            </View>
          </View>
          <Text style={{ fontSize: 9.5, color: C.textMuted, lineHeight: 1.55 }}>{t.rationale}</Text>
        </View>
      );
    })}
  </View>
);

// Half-A4 instrument tear sheet, sized to fit 2 per A4.
// Total budget per card ~340pt:
//   Header (~38pt) + body (S/R + chart, 88pt) + metrics strip (24pt)
//   + analysis (~120pt for ~6 lines at 8pt × 1.45) + margins (~70pt buffer).
const InstrumentCard: React.FC<{ inst: any; isFirst: boolean }> = ({ inst, isFirst }) => {
  const chg = inst.changePercent ?? 0;
  const chgCol = chg > 0 ? C.bull : chg < 0 ? C.bear : C.neutral;
  const sigCol = signalColor(inst.signal);
  const sigBg = sigCol === C.bull ? C.bullBg : sigCol === C.bear ? C.bearBg : C.neutralBg;
  return (
    <View
      id={`inst-${inst.symbol}`}
      style={{
        marginTop: isFirst ? 0 : 10,
        paddingTop: isFirst ? 0 : 10,
        borderTop: isFirst ? "none" : `0.5pt solid ${C.border}`,
      }}
      wrap={false}
    >
      {/* Header row */}
      <View style={{ flexDirection: "row", alignItems: "center", marginBottom: 6 }}>
        <View style={{ flex: 1, paddingRight: 12 }}>
          <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
            <Text style={{ fontSize: 15, fontWeight: 700 }}>{inst.symbol}</Text>
            <Text style={{ fontSize: 8.5, color: C.textMuted }}>{inst.name}</Text>
          </View>
        </View>
        <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8 }}>
          <Text style={[styles.mono, { fontSize: 14, fontWeight: 700 }]}>{fmtPrice(inst.price, inst.symbol)}</Text>
          <Text style={[styles.mono, { fontSize: 8.5, color: chgCol, fontWeight: 700 }]}>
            {sign(inst.change)}{fmtPrice(inst.change, inst.symbol)}  ·  {sign(chg)}{fmtPct(chg)}%
          </Text>
          <Text style={[styles.signalChip, {
            color: sigCol, backgroundColor: sigBg,
            paddingVertical: 2, paddingHorizontal: 7, fontSize: 7.5,
          }]}>
            {shortSignal(inst.signal)} {sign(inst.signalStrength)}{inst.signalStrength}
          </Text>
        </View>
      </View>

      {/* Body: S/R columns (left) + Candle chart (right) */}
      <View style={{ flexDirection: "row", gap: 8, marginBottom: 5 }}>
        <View style={{ flex: 1, flexDirection: "row", gap: 6 }}>
          <View style={{ flex: 1, backgroundColor: C.panel, padding: 5, borderRadius: 3 }}>
            <Text style={{ fontSize: 6.5, color: C.textMuted, letterSpacing: 1, fontWeight: 700, marginBottom: 2 }}>SUPPORTS</Text>
            {(inst.supports || []).map((s: any, i: number) => (
              <View key={i} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", paddingVertical: 0.5 }}>
                <Text style={{ fontSize: 7.5, color: C.text, fontWeight: 700, width: 14 }}>{s.level}</Text>
                <Text style={[styles.mono, { fontSize: 7.5, color: C.bull, fontWeight: 700 }]}>{fmtPrice(s.price, inst.symbol)}</Text>
              </View>
            ))}
          </View>
          <View style={{ flex: 1, backgroundColor: C.panel, padding: 5, borderRadius: 3 }}>
            <Text style={{ fontSize: 6.5, color: C.textMuted, letterSpacing: 1, fontWeight: 700, marginBottom: 2 }}>RESISTANCES</Text>
            {(inst.resistances || []).map((r: any, i: number) => (
              <View key={i} style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", paddingVertical: 0.5 }}>
                <Text style={{ fontSize: 7.5, color: C.text, fontWeight: 700, width: 14 }}>{r.level}</Text>
                <Text style={[styles.mono, { fontSize: 7.5, color: C.bear, fontWeight: 700 }]}>{fmtPrice(r.price, inst.symbol)}</Text>
              </View>
            ))}
          </View>
        </View>
        <View>
          <CandleChart ohlc={inst.ohlc} width={220} height={75} />
        </View>
      </View>

      {/* Mini metrics strip — 1 line */}
      <View style={{
        flexDirection: "row", paddingVertical: 3, marginBottom: 5,
        borderTop: `0.4pt solid ${C.border}`, borderBottom: `0.4pt solid ${C.border}`,
        gap: 18,
      }}>
        <Text style={{ fontSize: 7, color: C.textMuted }}>
          Day  <Text style={[styles.mono, { color: C.text, fontWeight: 600 }]}>
            {fmtPrice(inst.dayLow, inst.symbol)}–{fmtPrice(inst.dayHigh, inst.symbol)}
          </Text>
        </Text>
        <Text style={{ fontSize: 7, color: C.textMuted }}>
          52W  <Text style={[styles.mono, { color: C.text, fontWeight: 600 }]}>
            {fmtPrice(inst.yearLow, inst.symbol)}–{fmtPrice(inst.yearHigh, inst.symbol)}
          </Text>
        </Text>
        <Text style={{ fontSize: 7, color: C.textMuted }}>
          Prev  <Text style={[styles.mono, { color: C.text, fontWeight: 600 }]}>
            {fmtPrice(inst.previousClose, inst.symbol)}
          </Text>
        </Text>
        <Text style={{ fontSize: 7, color: C.textMuted }}>
          Evt impact  <Text style={[styles.mono, { color: C.text, fontWeight: 600 }]}>
            {inst.eventImpactProbability ?? "—"}%
          </Text>
        </Text>
      </View>

      {/* Analysis text */}
      <Text style={{ fontSize: 8, color: C.text, lineHeight: 1.45 }}>{inst.analysis}</Text>
    </View>
  );
};

// Page that holds up to 2 instrument cards.
const InstrumentSpread: React.FC<{ insts: any[]; pageIndex: number; totalPages: number }> = ({ insts, pageIndex, totalPages }) => (
  <Page size="A4" style={styles.page}>
    <Chrome />
    {pageIndex === 0 ? (
      <Text style={[styles.sectionHead, { marginBottom: 12 }]}>
        INSTRUMENT ANALYSIS  ·  PAGE {pageIndex + 1} OF {totalPages}
      </Text>
    ) : (
      <Text style={[styles.sectionHead, { marginBottom: 12 }]}>
        INSTRUMENT ANALYSIS (CONT.)  ·  PAGE {pageIndex + 1} OF {totalPages}
      </Text>
    )}
    {insts.map((inst, i) => (
      <InstrumentCard inst={inst} isFirst={i === 0} key={inst.symbol} />
    ))}
  </Page>
);

const UpcomingEvents: React.FC = () => (
  <View style={styles.section} id="events">
    <Text style={styles.sectionHead}>UPCOMING EVENTS</Text>
    <View style={[styles.tRow, { borderBottom: `0.75pt solid ${C.borderStrong}`, paddingBottom: 4 }]}>
      <Text style={[styles.tHead, { flex: 1 }]}>Date</Text>
      <Text style={[styles.tHead, { flex: 1 }]}>Time</Text>
      <Text style={[styles.tHead, { flex: 4 }]}>Event</Text>
      <Text style={[styles.tHead, { flex: 1, textAlign: "right" }]}>Impact</Text>
    </View>
    {(report.upcomingEvents || []).map((e: any, i: number) => (
      <View style={styles.tRow} key={i}>
        <Text style={[styles.mono, { flex: 1 }]}>{e.date}</Text>
        <Text style={[styles.mono, { flex: 1, color: C.textMuted }]}>{e.time}</Text>
        <Text style={[styles.tCell, { flex: 4 }]}>{e.event}</Text>
        <Text style={[styles.tCell, {
          flex: 1, textAlign: "right",
          color: impactColor(e.impact), fontWeight: 700, fontSize: 8.5,
        }]}>{e.impact}</Text>
      </View>
    ))}
  </View>
);

// ---------------------------------------------------------------------------
//  Contents page — links to each section. Page numbers are deterministic
//  because we control the Page count exactly:
//    1: Cover
//    2: Contents
//    3: Live Data + Scorecard
//    4: Top Conviction Trades
//    5..(4+M): Instrument spreads (2 per page, M = ceil(N/2))
//    (5+M): Upcoming Events + Risk Scenarios
//  For N=7 instruments: M=4, total = 9 pages.
// ---------------------------------------------------------------------------

const INSTRUMENTS_PER_PAGE = 2;

function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

const instrumentSpreads = chunk((report.instruments || []), INSTRUMENTS_PER_PAGE);
const TAIL_PAGE = 5 + instrumentSpreads.length;

type TocItem = { id: string; label: string; page: number; indent?: boolean };

function buildToc(): TocItem[] {
  const items: TocItem[] = [
    { id: "data",      label: "Live Market Data",       page: 3 },
    { id: "scorecard", label: "Scorecard vs Previous",  page: 3 },
    { id: "trades",    label: "Top Conviction Trades",  page: 4 },
  ];
  instrumentSpreads.forEach((spread, spreadIdx) => {
    const page = 5 + spreadIdx;
    spread.forEach((inst: any, instIdx: number) => {
      const isFirstInstrumentOverall = spreadIdx === 0 && instIdx === 0;
      items.push({
        id: `inst-${inst.symbol}`,
        label: isFirstInstrumentOverall
          ? `Instrument Analysis  ·  ${inst.symbol}`
          : `${inst.symbol}` + (inst.name ? `  ·  ${inst.name}` : ""),
        page,
        indent: !isFirstInstrumentOverall,
      });
    });
  });
  items.push({ id: "events",    label: "Upcoming Events",   page: TAIL_PAGE });
  items.push({ id: "scenarios", label: "Risk Scenarios",    page: TAIL_PAGE });
  return items;
}

const TocRow: React.FC<{ item: TocItem }> = ({ item }) => (
  <Link
    src={`#${item.id}`}
    style={{
      textDecoration: "none", color: C.text,
      flexDirection: "row", alignItems: "baseline",
      paddingVertical: 6,
      borderBottom: `0.4pt dotted ${C.border}`,
      paddingLeft: item.indent ? 24 : 0,
    }}
  >
    <Text style={{ flex: 1, fontSize: 11, fontWeight: item.indent ? 400 : 600 }}>{item.label}</Text>
    <Text style={[styles.mono, { fontSize: 11, color: C.textMuted, fontWeight: 700 }]}>{item.page}</Text>
  </Link>
);

const Contents: React.FC = () => {
  const toc = buildToc();
  return (
    <View>
      <Text style={[styles.coverEyebrow, { marginBottom: 6 }]}>CONTENTS</Text>
      <Text style={[styles.coverTitle, { fontSize: 22, marginBottom: 4 }]}>Report Contents</Text>
      <Text style={{ fontSize: 10, color: C.textMuted, marginBottom: 24 }}>
        Click any section to jump there. Page numbers shown on the right.
      </Text>
      {toc.map((item) => <TocRow key={item.id} item={item} />)}
    </View>
  );
};

// 2-column compact grid so all 5 scenarios fit on the same page as Events.
const RiskScenarios: React.FC = () => {
  const scenarios = report.riskScenarios || [];
  const left = scenarios.filter((_: any, i: number) => i % 2 === 0);
  const right = scenarios.filter((_: any, i: number) => i % 2 === 1);
  const renderCard = (r: any, i: number) => (
    <View style={[styles.card, { marginBottom: 6, padding: 9 }]} key={i} wrap={false}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", marginBottom: 3 }}>
        <Text style={{ fontSize: 9.5, fontWeight: 700, flex: 1, paddingRight: 6 }}>{r.name}</Text>
        <Text style={{
          fontSize: 7.5, fontWeight: 700,
          color: C.bear, backgroundColor: C.bearBg,
          paddingHorizontal: 5, paddingVertical: 1, borderRadius: 3,
        }}>{r.probability}</Text>
      </View>
      <Text style={{ fontSize: 8.5, color: C.textMuted, lineHeight: 1.45 }}>{r.impact}</Text>
    </View>
  );
  return (
    <View style={styles.section} id="scenarios">
      <Text style={styles.sectionHead}>RISK SCENARIOS</Text>
      <View style={{ flexDirection: "row", gap: 8 }}>
        <View style={{ flex: 1 }}>{left.map(renderCard)}</View>
        <View style={{ flex: 1 }}>{right.map(renderCard)}</View>
      </View>
    </View>
  );
};

// ---------------------------------------------------------------------------
//  Document
// ---------------------------------------------------------------------------

const MarketPulsePdf: React.FC<{ qrPng: Buffer; generatedAt: string; reportUrl: string }> = ({ qrPng, generatedAt, reportUrl }) => (
  <Document
    title={`Market Pulse — ${report.displayDate}`}
    author="Market Pulse"
    subject={`${report.type} Trading Report`}
    creator={`Market Pulse PDF Exporter ${PDF_VERSION}`}
    producer="@react-pdf/renderer"
    language="en-GB"
  >
    {/* 1. Cover */}
    <Page size="A4" style={styles.page}>
      <Chrome />
      <Cover qrPng={qrPng} generatedAt={generatedAt} reportUrl={reportUrl} />
    </Page>

    {/* 2. Contents */}
    <Page size="A4" style={styles.page}>
      <Chrome />
      <Contents />
    </Page>

    {/* 3. Live Data + Scorecard */}
    <Page size="A4" style={styles.page}>
      <Chrome />
      <LiveData />
      <Scorecard />
    </Page>

    {/* 4. Top Conviction Trades */}
    <Page size="A4" style={styles.page}>
      <Chrome />
      <TopTrades />
    </Page>

    {/* 5..(4+M). 2 instruments per page */}
    {instrumentSpreads.map((spread, idx) => (
      <InstrumentSpread
        insts={spread}
        pageIndex={idx}
        totalPages={instrumentSpreads.length}
        key={`spread-${idx}`}
      />
    ))}

    {/* Tail page. Events + Risks (compacted) */}
    <Page size="A4" style={styles.page}>
      <Chrome />
      <UpcomingEvents />
      <RiskScenarios />
    </Page>
  </Document>
);

// ---------------------------------------------------------------------------
//  Render
// ---------------------------------------------------------------------------

(async () => {
  console.log(`Rendering ${slug} → ${path.relative(REPO_ROOT, OUTPUT_PATH)}`);

  // Cover QR: deep-link to this specific report on the live dashboard.
  const reportUrl = `${DASHBOARD_URL}/#/${slug}`;
  const qrPng = await QRCode.toBuffer(reportUrl, {
    errorCorrectionLevel: "M",
    margin: 1,
    scale: 6,
    color: { dark: C.dark, light: "#ffffff" },
  });

  const generatedAt =
    new Date().toISOString().replace("T", " ").replace(/\..*Z$/, " UTC");

  await renderToFile(
    <MarketPulsePdf qrPng={qrPng} generatedAt={generatedAt} reportUrl={reportUrl} />,
    OUTPUT_PATH,
  );
  const bytes = fs.statSync(OUTPUT_PATH).size;
  console.log(`Done. ${(bytes / 1024).toFixed(1)} KB written.`);
})();
