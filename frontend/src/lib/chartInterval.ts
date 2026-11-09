/**
 * The chart's Interval switch: minute bars rolled up into 2m … 1h bars on the client, the way a
 * trading platform's interval menu reads. Pure; PriceChart applies it to its props.
 *
 * No lookahead: only minute bars at or before the playhead are rolled up, so the bar holding
 * the playhead is the forming bar (open, high, low and close so far). Indicator lines are the
 * minute values at each bar's last minute so far (they stay causal; they are not recomputed on
 * the larger bars).
 */
import type { Candle } from "./api";

/** Epoch seconds of an ISO time (kept local so the pure checks bundle without aliases). */
const secOf = (iso: string): number => Math.floor(Date.parse(iso) / 1000);

/**
 * Interval choices in minutes. The seconds ones are drawn from second bars (built from trade
 * prints) by charts that load them (`seconds` prop); 1D is shown but not built.
 */
export const INTERVALS = [
  { label: "5s", min: 5 / 60, available: true, seconds: true },
  { label: "15s", min: 15 / 60, available: true, seconds: true },
  { label: "30s", min: 30 / 60, available: true, seconds: true },
  { label: "1m", min: 1, available: true },
  { label: "2m", min: 2, available: true },
  { label: "5m", min: 5, available: true },
  { label: "10m", min: 10, available: true },
  { label: "15m", min: 15, available: true },
  { label: "1h", min: 60, available: true },
  { label: "1D", min: 1440, available: false, why: "Only the trade day is loaded." },
] as const;

/** Range choices; only the loaded day is available. */
export const RANGES = ["1D", "1W", "1M", "3M", "YTD", "1Y", "All"] as const;

/** A time's bar open for `tfSec` bars (clock-aligned: 5m bars open on :00, :05, …). */
export const bucketOf = (sec: number, tfSec: number): number => Math.floor(sec / tfSec) * tfSec;

/** Minute candles up to the playhead, rolled up into `tfSec` bars (ts = the bar open). */
export function rollUp(candles: readonly Candle[], tfSec: number, playheadSec: number): Candle[] {
  const out: Candle[] = [];
  let cur: Candle | null = null;
  let curB = NaN;
  for (const c of candles) {
    const t = secOf(c.ts);
    if (t > playheadSec) break; // never a bar after the playhead
    const b = bucketOf(t, tfSec);
    if (cur && b === curB) {
      cur.high = Math.max(cur.high, c.high);
      cur.low = Math.min(cur.low, c.low);
      cur.close = c.close;
      cur.volume = (cur.volume ?? 0) + (c.volume ?? 0);
    } else {
      if (cur) out.push(cur);
      curB = b;
      cur = { ...c, ts: new Date(b * 1000).toISOString(), timeframe: `${tfSec / 60}m` };
    }
  }
  if (cur) out.push(cur);
  return out;
}

/** A minute-keyed line as `tfSec` bars: each bar takes the value at its last minute up to the playhead. */
export function rollUpLine(values: Map<number, number>, tfSec: number, playheadSec: number): Map<number, number> {
  const keys = [...values.keys()].filter((k) => k <= playheadSec).sort((a, b) => a - b);
  const out = new Map<number, number>();
  for (const k of keys) out.set(bucketOf(k, tfSec), values.get(k) as number);
  return out;
}
