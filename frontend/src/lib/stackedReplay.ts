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

export const floorMinute = (sec: number): number => Math.floor(sec / MINUTE) * MINUTE;

/** Every minute from `startSec` to `endSec` inclusive, as UTC epoch seconds. */
export function buildTimeline(startSec: number, endSec: number, stepSec: number = MINUTE): UTCTimestamp[] {
  const out: UTCTimestamp[] = [];
  for (let t = floorMinute(startSec); t <= endSec; t += stepSec) out.push(t as UTCTimestamp);
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

/**
 * Spread axis labels so none overlap: sorted by y, each at least `gap` px below
 * the one above, pushed back up from the bottom edge if needed. Labels that sit
 * outside [0, height] are dropped. Returns the label y (centre) in px.
 */
export function spreadLabels<T extends { y: number }>(
  items: T[],
  gap: number,
  height: number,
): (T & { labelY: number })[] {
  const inside = items
    .filter((it) => it.y >= 0 && it.y <= height)
    .sort((a, b) => a.y - b.y)
    .map((it) => ({ ...it, labelY: it.y }));
  for (let i = 1; i < inside.length; i++) {
    inside[i].labelY = Math.max(inside[i].labelY, inside[i - 1].labelY + gap);
  }
  const maxY = height - gap / 2;
  for (let i = inside.length - 1; i >= 0; i--) {
    const limit = i === inside.length - 1 ? maxY : inside[i + 1].labelY - gap;
    inside[i].labelY = Math.min(inside[i].labelY, limit);
  }
  return inside;
}
