/**
 * Pure helpers for the stacked Replay panes (contract premium over SPY).
 *
 * The two panes have different bar sets, so both are laid out on ONE shared
 * minute grid from the lead start to the window end. A pane's bar for a minute
 * is its real candle, or a whitespace point when it has none (or the playhead
 * has not reached it yet), so the same minute lines up vertically.
 */
import type { CandlestickData, UTCTimestamp, WhitespaceData } from "lightweight-charts";

import type { Candle, ReplayFill } from "@/lib/api";
import { isoToUtcSeconds } from "@/lib/chart";

export const MINUTE = 60;
/** Initial x-range: lead start + a fixed 90 min (depends only on the entry). */
export const INITIAL_WINDOW_MIN = 90;
/** y-domain padding: this fraction of the revealed range, never less than the floor. */
export const DOMAIN_PAD = 0.25;
const DOMAIN_FLOOR_FRAC = 0.05; // of the mid price
const DOMAIN_FLOOR_ABS = 0.05;

export const floorMinute = (sec: number): number => Math.floor(sec / MINUTE) * MINUTE;

/** Every minute from `startSec` to `endSec` inclusive, as UTC epoch seconds. */
export function buildTimeline(startSec: number, endSec: number): UTCTimestamp[] {
  const out: UTCTimestamp[] = [];
  for (let t = floorMinute(startSec); t <= endSec; t += MINUTE) out.push(t as UTCTimestamp);
  return out;
}

/** Candles whose minute lies in [startSec, endSec]. */
export function candlesInWindow(candles: Candle[], startSec: number, endSec: number): Candle[] {
  const lo = floorMinute(startSec);
  return candles.filter((c) => {
    const t = isoToUtcSeconds(c.ts);
    return t >= lo && t <= endSec;
  });
}

/**
 * Candlestick data on the shared grid, revealed up to `playheadSec`: bars after
 * the playhead and minutes with no bar are whitespace.
 */
export function alignedCandles(
  timeline: UTCTimestamp[],
  candles: Candle[],
  playheadSec: number,
): (CandlestickData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] {
  const byMinute = new Map<number, Candle>();
  for (const c of candles) byMinute.set(floorMinute(isoToUtcSeconds(c.ts)), c);
  return timeline.map((time) => {
    const c = time <= playheadSec ? byMinute.get(time) : undefined;
    return c
      ? { time, open: c.open, high: c.high, low: c.low, close: c.close }
      : { time };
  });
}

/** The candle for a minute, only if the playhead has reached it. */
export function candleAt(
  candles: Candle[],
  minuteSec: number,
  playheadSec: number,
): Candle | null {
  if (minuteSec > playheadSec) return null;
  return candles.find((c) => floorMinute(isoToUtcSeconds(c.ts)) === minuteSec) ?? null;
}

export interface Domain {
  minValue: number;
  maxValue: number;
}

/**
 * y-domain from the bars revealed so far (lead start .. playhead), padded
 * generously, that only ever EXPANDS: it is left untouched until a revealed bar
 * (or an extra level such as the stop) leaves it, and then grows with fresh
 * padding. It never shrinks, so there is no jitter during playback, and it
 * never reflects bars the playhead has not reached.
 */
export function growDomain(
  prev: Domain | null,
  candles: Candle[],
  playheadSec: number,
  extra: (number | null | undefined)[] = [],
): Domain | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of candles) {
    if (isoToUtcSeconds(c.ts) > playheadSec) break; // candles are chronological
    lo = Math.min(lo, c.low);
    hi = Math.max(hi, c.high);
  }
  for (const v of extra) {
    if (v == null) continue;
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return prev;
  if (prev && lo >= prev.minValue && hi <= prev.maxValue) return prev;
  const mid = (lo + hi) / 2;
  const floor = Math.max(Math.abs(mid) * DOMAIN_FLOOR_FRAC, DOMAIN_FLOOR_ABS);
  const pad = Math.max((hi - lo) * DOMAIN_PAD, floor);
  const next = { minValue: lo - pad, maxValue: hi + pad };
  if (!prev) return next;
  return {
    minValue: Math.min(prev.minValue, next.minValue),
    maxValue: Math.max(prev.maxValue, next.maxValue),
  };
}

/**
 * The revealed fill time of a role at or before the playhead, if any: the
 * first entry, or the latest exit revealed so far (a scale-out is several).
 */
export function revealedFillTime(
  fills: ReplayFill[],
  role: "entry" | "exit",
  playheadSec: number,
): number | null {
  const times = fills
    .filter((f) => f.role === role)
    .map((f) => floorMinute(isoToUtcSeconds(f.ts)))
    .filter((t) => t <= playheadSec);
  if (!times.length) return null;
  return role === "entry" ? Math.min(...times) : Math.max(...times);
}

/**
 * "SPY, proxy for SPX" when the backend says the bars are a stand-in (the
 * source label looks like "SPY (proxy)"); otherwise the plain symbol.
 */
export function spyPaneLabel(symbol: string, sourceLabel?: string | null): string {
  if (sourceLabel && /proxy/i.test(sourceLabel)) {
    const ticker = /^\s*([A-Za-z.]+)/.exec(sourceLabel)?.[1]?.toUpperCase() ?? "SPY";
    return `${ticker}, proxy for SPX`;
  }
  return symbol;
}
