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
export function indexOfSec(startSec: number, sec: number, lastIdx: number): number {
  const i = Math.floor((sec - startSec) / MINUTE);
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
