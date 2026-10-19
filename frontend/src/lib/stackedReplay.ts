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
/** The y-domain is padded this much (fraction of the range) on both sides. */
export const DOMAIN_PAD = 0.06;

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

/**
 * Fixed price range over the whole trade window (plus any extra levels, e.g.
 * the stop), padded a little. Computed once per window so nothing rescales
 * during playback.
 */
export function fixedDomain(
  candles: Candle[],
  extra: (number | null | undefined)[] = [],
): { minValue: number; maxValue: number } | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of candles) {
    lo = Math.min(lo, c.low);
    hi = Math.max(hi, c.high);
  }
  for (const v of extra) {
    if (v == null) continue;
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  const span = hi - lo || Math.abs(hi) * 0.01 || 1;
  return { minValue: lo - span * DOMAIN_PAD, maxValue: hi + span * DOMAIN_PAD };
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
