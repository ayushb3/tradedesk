/**
 * Pure logic for Replay's Seconds mode: the seconds clock and the forming candle.
 * No DOM, no network. The server already cuts second bars at the playhead; every
 * function here ALSO refuses anything after the playhead, as a second guard.
 *
 * Convention: the playhead is a whole second P meaning "second P has printed", so a
 * second bar stamped S is visible when S <= P (the same rule the fills use).
 */
import type { Candle } from "./api";
import { DISPLAY_TZ, DISPLAY_TZ_LABEL } from "./format";

export const MINUTE = 60;
export const SECONDS_SPEEDS = [1, 2, 4] as const;
export type SecondsSpeed = (typeof SECONDS_SPEEDS)[number];

export const floorMinuteSec = (sec: number): number => Math.floor(sec / MINUTE) * MINUTE;
const epoch = (iso: string): number => Math.floor(new Date(iso).getTime() / 1000);
const iso = (sec: number): string => new Date(sec * 1000).toISOString();

/** ms between one-second steps at a given speed (1x = real time). */
export const tickMs = (speed: number): number => 1000 / speed;

export function clampSec(sec: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, sec));
}

/** Move the playhead by `delta` seconds, inside [min, max]. */
export function stepSec(sec: number, delta: number, min: number, max: number): number {
  return clampSec(sec + delta, min, max);
}

/** Arrow keys: 1 s, or 1 min with Shift. */
export function arrowDelta(key: "ArrowLeft" | "ArrowRight", shift: boolean): number {
  const n = shift ? MINUTE : 1;
  return key === "ArrowLeft" ? -n : n;
}

/** Add bars not already held (by timestamp), keeping time order. */
export function mergeBars(held: Candle[], incoming: Candle[]): Candle[] {
  if (incoming.length === 0) return held;
  const seen = new Set(held.map((b) => b.ts));
  const add = incoming.filter((b) => !seen.has(b.ts));
  if (add.length === 0) return held;
  return [...held, ...add].sort((a, b) => epoch(a.ts) - epoch(b.ts));
}

/** Bars at or before the playhead second (client-side guard; the server cuts first). */
export function barsUpTo(bars: Candle[], playheadSec: number): Candle[] {
  return bars.filter((b) => epoch(b.ts) <= playheadSec);
}

/** Timestamp of the newest held bar, or null. Sent as `after` so a poll gets only new bars. */
export function newestTs(bars: Candle[]): string | null {
  return bars.length ? bars[bars.length - 1].ts : null;
}

/**
 * The minute candle being formed: open = first second's open, high/low = running
 * max/min, close = latest close, volume summed, using only seconds up to the playhead.
 * Null when no second of that minute has printed yet.
 */
export function formingCandle(secondBars: Candle[], minuteSec: number, playheadSec: number): Candle | null {
  let out: Candle | undefined;
  for (const b of secondBars) {
    const t = epoch(b.ts);
    if (t < minuteSec || t >= minuteSec + MINUTE || t > playheadSec) continue;
    const prev: Candle | undefined = out;
    out = prev
      ? {
          ...prev,
          high: Math.max(prev.high, b.high),
          low: Math.min(prev.low, b.low),
          close: b.close,
          volume: prev.volume + b.volume,
        }
      : { ...b, ts: iso(minuteSec), timeframe: "1m" };
  }
  return out ?? null;
}

/**
 * The 1-minute series as it stands at the playhead: finished minutes are the normal
 * minute bars; the current minute is the forming candle (or absent if nothing has
 * printed in it yet). Minutes after the playhead's are dropped.
 */
export function replaySeries(minuteBars: Candle[], secondBars: Candle[], playheadSec: number): Candle[] {
  const cur = floorMinuteSec(playheadSec);
  const done = minuteBars.filter((c) => epoch(c.ts) < cur);
  const forming = formingCandle(secondBars, cur, playheadSec);
  return forming ? [...done, forming] : done;
}

/** A causal per-minute map without the playhead minute or anything after it. */
export function finishedOnly<T>(values: Map<number, T>, playheadSec: number): Map<number, T> {
  const cur = floorMinuteSec(playheadSec);
  const out = new Map<number, T>();
  for (const [k, v] of values) if (k < cur) out.set(k, v);
  return out;
}

/** "06:47:12" in the display zone (seconds included); add the label separately. */
const hms = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
export const formatClockSeconds = (sec: number): string => hms.format(sec * 1000);
export const formatClockSecondsPt = (sec: number): string => `${formatClockSeconds(sec)} ${DISPLAY_TZ_LABEL}`;

/** True when a poll is due: the playhead is past what has been fetched. */
export function pollDue(fetchedUpTo: number | null, playheadSec: number): boolean {
  return fetchedUpTo === null || playheadSec > fetchedUpTo;
}

/** Index of the last candle whose minute is at or before the playhead's minute (0 if none). */
export function minuteIndexAt(candles: Candle[], playheadSec: number): number {
  const cur = floorMinuteSec(playheadSec);
  let idx = 0;
  for (let i = 0; i < candles.length; i++) {
    if (floorMinuteSec(epoch(candles[i].ts)) > cur) break;
    idx = i;
  }
  return idx;
}

/** The latest price as a candle stamped at the playhead second, for the sim's fills. */
export function liveCandle(series: Candle[], playheadSec: number): Candle | null {
  const last = series[series.length - 1];
  return last ? { ...last, ts: iso(playheadSec) } : null;
}
