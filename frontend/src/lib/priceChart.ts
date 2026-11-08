/**
 * Pure logic for the shared PriceChart (stacked panes on one minute grid).
 * Replay and Trade review both render through it, so the rules that must not
 * drift live here: the playhead clamp, per-pane autoscale, the default window
 * and where fill labels go. No DOM.
 */
export { fitWindow, type FitWindow } from "./priceChartWindow";

export const MINUTE = 60;
/** Widest a bar may get (px). Keeps a handful of bars from filling the pane. */
export const MAX_BAR_SPACING = 50;
/** Fewest bars ever shown regardless of plot width. */
export const MIN_BARS_FLOOR = 8;
/** Autoscale padding: this fraction of the visible price range, each side. */
export const AUTOSCALE_PAD = 0.06;

export interface IndexRange {
  from: number;
  to: number;
}

export interface PriceDomain {
  minValue: number;
  maxValue: number;
}

// ---------------------------------------------------------------------------
// Time axis

/** Fewest bars the view may span for a plot this wide (so spacing stays <= MAX_BAR_SPACING). */
export function minVisibleBars(plotWidthPx: number): number {
  if (!(plotWidthPx > 0)) return MIN_BARS_FLOOR;
  return Math.max(MIN_BARS_FLOOR, Math.ceil(plotWidthPx / MAX_BAR_SPACING));
}

/** Timeline index of a minute (epoch seconds), clamped to [0, lastIdx]. */
export function indexOfSec(startSec: number, sec: number, lastIdx: number, stepSec: number = MINUTE): number {
  const i = Math.floor((sec - startSec) / stepSec);
  return Math.min(lastIdx, Math.max(0, i));
}

/**
 * The furthest right edge (timeline index) the view may reach.
 * Replay: the playhead bar, so the future cannot be panned or zoomed into. Until enough
 * bars have played to fill the pane at the widest bar spacing, the view is that many
 * bars wide with blank space right of the playhead (nothing is drawn there).
 * Review: the whole window.
 */
export function rightLimit(opts: {
  replay: boolean;
  playIdx: number;
  lastIdx: number;
  minBars: number;
}): number {
  if (!opts.replay) return opts.lastIdx;
  return Math.min(opts.lastIdx, Math.max(opts.playIdx, opts.minBars - 1));
}

/**
 * Keep a visible range inside [0, rightLimit] and at least `minBars` wide, preserving its
 * width where it can (a pan past the limit stops at the limit instead of squashing).
 */
export function clampRange(r: IndexRange, lim: { rightLimit: number; minBars: number }): IndexRange {
  const minSpan = Math.min(lim.minBars - 1, lim.rightLimit);
  const width = Math.max(minSpan, Math.min(lim.rightLimit, r.to - r.from));
  const to = Math.min(r.to, lim.rightLimit);
  const from = to - width;
  if (from < 0) return { from: 0, to: width };
  return { from, to };
}

/** The default view: the whole window (review) or lead start .. playhead (replay). */
export function defaultRange(lim: { rightLimit: number }): IndexRange {
  return { from: 0, to: lim.rightLimit };
}

/** Index range of [fromSec, toSec] on a timeline that starts at `startSec`. */
export function indexRange(startSec: number, fromSec: number, toSec: number, lastIdx: number): IndexRange {
  return {
    from: indexOfSec(startSec, fromSec, lastIdx),
    to: indexOfSec(startSec, toSec, lastIdx),
  };
}

// ---------------------------------------------------------------------------
// Autoscale

/**
 * y-range for one pane from the VISIBLE played bars: the bars' low/high (plus any extra
 * levels such as the stop), padded by a few percent of that range. No fixed or rounded
 * band. With `floorZero` (option premium) the low never goes below 0.
 */
export function paddedRange(
  bounds: { min: number; max: number } | null,
  extras: (number | null | undefined)[] = [],
  opts: { floorZero?: boolean; padFrac?: number } = {},
): PriceDomain | null {
  let lo = bounds ? bounds.min : Infinity;
  let hi = bounds ? bounds.max : -Infinity;
  for (const v of extras) {
    if (v == null || !Number.isFinite(v)) continue;
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  const mid = (lo + hi) / 2;
  const pad = Math.max((hi - lo) * (opts.padFrac ?? AUTOSCALE_PAD), Math.abs(mid) * 0.00002, 0.005);
  let minValue = lo - pad;
  const maxValue = hi + pad;
  if (opts.floorZero) minValue = Math.max(0, minValue);
  return { minValue, maxValue: Math.max(maxValue, minValue + 0.01) };
}

// ---------------------------------------------------------------------------
// Fill labels

/** "B 06:34 PT" / "S 06:36 PT": side and time only, never a price. */
export function fillChipText(side: "buy" | "sell", clock: string): string {
  return `${side === "buy" ? "B" : "S"} ${clock}`;
}

/** Broker order type for display: "LMT" / "MKT" / "STOP"; "" when the broker didn't say. */
export function orderTypeLabel(t: string | null | undefined): string {
  if (!t) return "";
  const k = t.toLowerCase();
  return k === "limit" ? "LMT" : k === "market" ? "MKT" : k === "stop" ? "STOP" : k.toUpperCase();
}

/**
 * Dollar value of a fill: whole dollars when it is whole ("$250"), cents otherwise ("$1,000.50").
 * "—" when the server did not send one (an older backend during a deploy).
 */
export function fillValueText(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: Number.isInteger(v) ? 0 : 2, maximumFractionDigits: 2 })}`;
}

/**
 * Readout line for one fill: "B 2 @ 1.25 07:30:05 PT LMT". `price: false` drops "@ price"
 * (the underlying pane must not show an option price on its axis).
 */
export function fillDetailText(
  f: { side: "buy" | "sell"; qty: number; price: number; order_type?: string | null },
  clock: string,
  opts: { price?: boolean } = {},
): string {
  const head = opts.price === false ? `${f.side === "buy" ? "B" : "S"} ${f.qty}` : `${f.side === "buy" ? "B" : "S"} ${f.qty} @ ${f.price.toFixed(2)}`;
  const type = orderTypeLabel(f.order_type);
  return type ? `${head} ${clock} ${type}` : `${head} ${clock}`;
}

/** Fills in time order (ties keep FIFO `seq` order), for the one-row-per-fill list. */
export function fillsInTimeOrder<T extends { ts: string; seq: number }>(fills: T[]): T[] {
  return [...fills].sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts) || a.seq - b.seq);
}

const CHAR_PX = 7.4; // 12px monospace
const CHIP_PAD_PX = 12;
/** Approximate rendered width of a chip, px. */
export const chipWidth = (text: string): number => Math.ceil(text.length * CHAR_PX + CHIP_PAD_PX);

export interface PlacedChip {
  left: number;
  lane: number;
}

/**
 * Place time chips in a strip of `width` px: each starts just right of its marker (x), flips
 * to the left of it near the right edge, is clamped inside the strip, and moves to a lower
 * lane when it would overlap another chip. Output order matches input order.
 */
export function placeChips(items: { x: number; w: number }[], width: number, gap = 4): PlacedChip[] {
  const out: PlacedChip[] = new Array(items.length);
  const lanes: { l: number; r: number }[][] = [];
  const order = items.map((_, i) => i).sort((a, b) => items[a].x - items[b].x);
  for (const i of order) {
    const { x, w } = items[i];
    let left = x + 3;
    if (left + w > width) left = x - 3 - w;
    left = Math.max(0, Math.min(left, Math.max(0, width - w)));
    let lane = 0;
    while (lanes[lane]?.some((o) => left < o.r + gap && left + w + gap > o.l)) lane++;
    (lanes[lane] ??= []).push({ l: left, r: left + w });
    out[i] = { left, lane };
  }
  return out;
}

/** What the chart legend reads out for one bar: OHLC plus the change from the previous bar's close. */
export interface LegendBar {
  open: number;
  high: number;
  low: number;
  close: number;
  /** close - previous close; null for the first bar in the window. */
  change: number | null;
  changePct: number | null;
}

/**
 * The legend's bar at `sec` (a bar open, epoch seconds): never one after the playhead.
 * Bars match on their open time, rounded to the minute like the rest of the chart.
 */
export function legendBar(
  candles: { ts: string; open: number; high: number; low: number; close: number }[],
  sec: number,
  playheadSec: number,
): LegendBar | null {
  if (sec > playheadSec) return null;
  const key = (ts: string) => Math.floor(Date.parse(ts) / 60_000) * 60;
  const i = candles.findIndex((c) => key(c.ts) === sec);
  if (i < 0) return null;
  const c = candles[i];
  const prev = i > 0 ? candles[i - 1].close : null;
  const change = prev == null ? null : c.close - prev;
  const changePct = prev ? (change as number) / prev * 100 : null;
  return { open: c.open, high: c.high, low: c.low, close: c.close, change, changePct };
}

/** "+0.12 (+1.05%)", "−0.40 (−2.10%)"; "" when there is no previous bar. */
export function formatChange(b: Pick<LegendBar, "change" | "changePct">): string {
  if (b.change == null) return "";
  const sign = b.change > 0 ? "+" : b.change < 0 ? "−" : "";
  const pct = b.changePct == null ? "" : ` (${sign}${Math.abs(b.changePct).toFixed(2)}%)`;
  return `${sign}${Math.abs(b.change).toFixed(2)}${pct}`;
}

/** "1m", "5m", "15s" for a bar size in seconds. */
export function timeframeLabel(barSec: number): string {
  if (barSec < 60) return `${barSec}s`;
  if (barSec % 3600 === 0) return `${barSec / 3600}h`;
  return `${Math.round(barSec / 60)}m`;
}
