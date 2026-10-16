/**
 * Adapters between the backend contract and lightweight-charts data.
 *
 * lightweight-charts wants `time` as a UTC epoch in SECONDS (UTCTimestamp)
 * for intraday data. Backend `ts` is an ISO 8601 UTC string (bar open time),
 * so we parse to ms and divide by 1000. Timestamps stay in true UTC; the ET
 * display is purely a formatting concern (see `makeEtFormatters` / `formatEt`).
 */
import type {
  CandlestickData,
  LineData,
  UTCTimestamp,
  WhitespaceData,
} from "lightweight-charts";
import type { AlignedSeries, Candle } from "@/lib/api";
import { DISPLAY_TZ } from "@/lib/format";

/** ISO 8601 UTC string -> UTCTimestamp (epoch seconds). */
export function isoToUtcSeconds(iso: string): UTCTimestamp {
  return Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;
}

/** Map backend candles to lightweight-charts candlestick data. */
export function toCandlestickData(
  candles: Candle[],
): CandlestickData<UTCTimestamp>[] {
  return candles.map((c) => ({
    time: isoToUtcSeconds(c.ts),
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));
}

/**
 * Progressive-reveal candle data for the FULL session width.
 *
 * Bars `0..currentIndex` are real candlesticks; every later bar is emitted as
 * a whitespace point (`{ time }` only). Whitespace reserves the logical slot
 * without drawing a bar, so `setVisibleLogicalRange(0 .. length-1)` can frame
 * the entire session from index 0 — otherwise lightweight-charts clamps the
 * range to the 1–2 bars that physically exist early on, and the fixed-window
 * framing collapses (candles crushed against the left edge).
 */
export function revealedCandlestickData(
  candles: Candle[],
  currentIndex: number,
): (CandlestickData<UTCTimestamp> | WhitespaceData<UTCTimestamp>)[] {
  const end = Math.min(currentIndex, candles.length - 1);
  return candles.map((c, i) => {
    const time = isoToUtcSeconds(c.ts);
    if (i > end) return { time };
    return { time, open: c.open, high: c.high, low: c.low, close: c.close };
  });
}

/**
 * Build line data from a causal server series, revealing ONLY bars
 * `0..currentIndex`. This is the no-lookahead gate: the loop never reads an
 * index beyond `currentIndex`, so the future can't leak into the overlay.
 * Nulls (warm-up gaps) are dropped — lightweight-charts renders the gap.
 */
export function revealedLineData(
  candles: Candle[],
  series: AlignedSeries | undefined,
  currentIndex: number,
): LineData<UTCTimestamp>[] {
  if (!series || candles.length === 0 || currentIndex < 0) return [];
  const out: LineData<UTCTimestamp>[] = [];
  const end = Math.min(currentIndex, candles.length - 1, series.length - 1);
  for (let i = 0; i <= end; i++) {
    const v = series[i];
    if (v == null || Number.isNaN(v)) continue;
    out.push({ time: isoToUtcSeconds(candles[i].ts), value: v });
  }
  return out;
}

/** The causal value of an aligned series at exactly `currentIndex`, or null. */
export function valueAt(
  series: AlignedSeries | undefined,
  currentIndex: number,
): number | null {
  if (!series || currentIndex < 0 || currentIndex >= series.length) return null;
  const v = series[currentIndex];
  return v == null || Number.isNaN(v) ? null : v;
}

/**
 * Time-axis formatters that render UTC bar timestamps in the display zone
 * (`DISPLAY_TZ`, Pacific). lightweight-charts passes intraday `time` as epoch
 * SECONDS, so multiply by 1000 for the Intl formatter.
 */
export function makeDisplayFormatters(tz: string = DISPLAY_TZ) {
  const hm = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const full = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const toMs = (time: unknown): number =>
    typeof time === "number" ? time * 1000 : Date.parse(String(time));
  return {
    // axis tick labels (HH:MM, display zone)
    tickMarkFormatter: (time: unknown) => hm.format(toMs(time)),
    // crosshair label (Mon DD, HH:MM, display zone)
    timeFormatter: (time: unknown) => full.format(toMs(time)),
  };
}

/**
 * Older names, kept so existing callers compile. They used to take the
 * session's exchange zone; the argument is now ignored and the result is in
 * the display zone, like everything else.
 */
export const makeEtFormatters = (_sessionTz?: string) => makeDisplayFormatters();
export const formatEt = (iso: string, _sessionTz?: string) => formatDisplayTime(iso);

/** Format an ISO UTC timestamp as a display-zone clock time (HH:MM:SS). */
export function formatDisplayTime(iso: string, tz: string = DISPLAY_TZ): string {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}
