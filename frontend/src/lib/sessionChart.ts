/**
 * A replay session (free replay, Learn practice, the scenario author) as the one chart's
 * input: PriceChart's underlying pane at a bar index, plus the shared time grid. Overlay,
 * level and caller-level styles come from lib/chartColors; visibility is opt-out.
 *
 * The server computes indicators and levels causally, and PriceChart in replay mode never
 * draws or scales to a bar after the playhead, so nothing past `idx` reaches the screen.
 */
import type { PaneSpec } from "@/components/chart/PriceChart";
import type { Candle, IndicatorKey, LevelKey, ReplaySession, SimTrade } from "@/lib/api";
import { isoToUtcSeconds, valueAt } from "@/lib/chart";
import { callerStyle, LEVEL_COLORS, lineLook, type LineStyleName } from "@/lib/chartColors";

export interface OverlaySpec {
  key: string; // a key in session.indicators, e.g. "ema9"
  label: string;
  color: string;
  width: number;
  style: LineStyleName;
}

/**
 * The overlay specs for the EMA/SMA keys present in the payload (keys are dynamic: ema9,
 * ema21, …), sorted by period, with VWAP last.
 */
export function overlaySpecs(indicators: Record<string, unknown>): OverlaySpec[] {
  const byPeriod = (re: RegExp) =>
    Object.keys(indicators)
      .filter((k) => re.test(k))
      .sort((a, b) => Number(a.slice(3)) - Number(b.slice(3)));
  const spec = (k: string, i = 0): OverlaySpec => {
    const look = lineLook(k, i);
    return { key: k, label: k.toUpperCase(), color: look.color, width: look.width, style: look.style };
  };
  const specs: OverlaySpec[] = byPeriod(/^ema\d+$/).map((k, i) => spec(k, i));
  for (const k of byPeriod(/^sma\d+$/)) specs.push(spec(k));
  if ("vwap" in indicators) specs.push(spec("vwap"));
  return specs;
}

/** Horizontal levels, drawn at their causal value at the playhead bar. */
export const LEVEL_META: { key: LevelKey; label: string; color: string }[] = [
  { key: "pdh", label: "PDH", color: LEVEL_COLORS.pdh },
  { key: "pdl", label: "PDL", color: LEVEL_COLORS.pdl },
  { key: "pmh", label: "PMH", color: LEVEL_COLORS.pmh },
  { key: "pml", label: "PML", color: LEVEL_COLORS.pml },
  { key: "or_high", label: "ORH", color: LEVEL_COLORS.or_high },
  { key: "or_low", label: "ORL", color: LEVEL_COLORS.or_low },
  { key: "day_high", label: "HOD", color: LEVEL_COLORS.day_high },
  { key: "day_low", label: "LOD", color: LEVEL_COLORS.day_low },
];

/** Visibility key for the caller-levels group (lives in hiddenLevels). */
export const CALLER_LEVELS_KEY = "caller";

/** Keys the user toggled OFF; anything absent is shown. */
export interface ChartVisibility {
  hiddenOverlays: Set<string>;
  hiddenLevels: Set<string>;
}

const SHOW_ALL: ChartVisibility = { hiddenOverlays: new Set(), hiddenLevels: new Set() };

/** Bar size in seconds: the smallest gap between consecutive bars (60 when unknown). */
export function barSecOf(candles: Candle[]): number {
  let best = Infinity;
  for (let i = 1; i < candles.length; i++) {
    const d = isoToUtcSeconds(candles[i].ts) - isoToUtcSeconds(candles[i - 1].ts);
    if (d > 0 && d < best) best = d;
  }
  return Number.isFinite(best) ? Math.round(best) : 60;
}

/** Floor a time onto the bar grid that starts at `startSec`. */
function onGrid(sec: number, startSec: number, barSec: number): number {
  return startSec + Math.floor((sec - startSec) / barSec) * barSec;
}

/** One series as a bar-open-keyed map (nulls skipped). */
function seriesMap(candles: Candle[], series: (number | null)[] | undefined, startSec: number, barSec: number) {
  const out = new Map<number, number>();
  if (!series) return out;
  candles.forEach((c, i) => {
    const v = series[i];
    if (v != null && !Number.isNaN(v)) out.set(onGrid(isoToUtcSeconds(c.ts), startSec, barSec), v);
  });
  return out;
}

export interface SessionChart {
  pane: PaneSpec;
  startSec: number;
  endSec: number;
  /** Bar open at `idx`. */
  playheadSec: number;
  barSec: number;
}

/**
 * The session at bar `idx` as PriceChart props. `trades` (free replay's practice trades)
 * become markers; their legs after the playhead are left out.
 */
export function sessionChart(
  session: ReplaySession,
  idx: number,
  opts: { visibility?: ChartVisibility; trades?: SimTrade[]; label?: string } = {},
): SessionChart | null {
  const { candles, indicators, levels } = session;
  if (candles.length === 0) return null;
  const vis = opts.visibility ?? SHOW_ALL;
  const barSec = barSecOf(candles);
  const startSec = isoToUtcSeconds(candles[0].ts);
  const end = Math.max(0, Math.min(idx, candles.length - 1));
  const endSec = onGrid(isoToUtcSeconds(candles[candles.length - 1].ts), startSec, barSec);
  const playheadSec = onGrid(isoToUtcSeconds(candles[end].ts), startSec, barSec);
  const series = (k: string) => (indicators as Record<string, (number | null)[] | undefined>)[k as IndicatorKey];

  const specs = overlaySpecs(indicators).filter((o) => !vis.hiddenOverlays.has(o.key));
  const lines = specs
    .filter((o) => o.key !== "vwap")
    .map((o) => ({ label: o.label, values: seriesMap(candles, series(o.key), startSec, barSec), color: o.color, style: o.style, width: o.width }));
  const vwap = specs.some((o) => o.key === "vwap") ? seriesMap(candles, series("vwap"), startSec, barSec) : undefined;

  const paneLevels: NonNullable<PaneSpec["levels"]> = [];
  for (const lv of LEVEL_META) {
    if (vis.hiddenLevels.has(lv.key)) continue;
    const price = valueAt(levels[lv.key], end);
    if (price != null) paneLevels.push({ label: lv.label, price, color: lv.color, style: "dashed" });
  }
  if (!vis.hiddenLevels.has(CALLER_LEVELS_KEY)) {
    for (const cl of session.caller_levels ?? []) {
      const st = callerStyle(cl.kind);
      paneLevels.push({ label: st.label, price: cl.price, color: st.color, style: st.style });
    }
  }

  const markers: NonNullable<PaneSpec["markers"]> = [];
  (opts.trades ?? []).forEach((t, ti) => {
    const long = t.direction === "long";
    for (const leg of t.entries)
      markers.push({ sec: onGrid(isoToUtcSeconds(leg.ts), startSec, barSec), side: long ? "buy" : "sell", text: `#${ti + 1} ${long ? "L" : "S"}` });
    for (const leg of t.exits)
      markers.push({ sec: onGrid(isoToUtcSeconds(leg.ts), startSec, barSec), side: "exit", text: "exit" });
  });

  return {
    pane: {
      label: opts.label ?? session.symbol,
      candles,
      vwap,
      emas: lines.length > 0 ? lines : undefined,
      levels: paneLevels,
      levelsOnAxis: true,
      markers: markers.length > 0 ? markers : undefined,
    },
    startSec,
    endSec,
    playheadSec,
    barSec,
  };
}
