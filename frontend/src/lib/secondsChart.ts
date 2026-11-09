/**
 * Second-bar chart helpers (pure): chunking of 1s fetches, roll-up of 1s bars to 5s/15s/30s
 * with no look-ahead past the playhead, and second-level (index, offset) arithmetic.
 */
import type { Candle } from "./api";

export const CHUNK_SEC = 1800;
export const SECONDS_RES = ["5s", "15s", "30s"] as const;
export type SecondsRes = (typeof SECONDS_RES)[number];

export function resSecOf(label: SecondsRes | string): number {
  const n = parseInt(label, 10);
  return Number.isFinite(n) && n > 0 ? n : 1;
}

/** Floor to a 30-minute boundary (epoch seconds). */
export function chunkStart(sec: number): number {
  return Math.floor(sec / CHUNK_SEC) * CHUNK_SEC;
}

export interface Chunk {
  start: number;
  end: number;
}

/** The chunk holding playSec plus the previous one, ascending. */
export function chunksFor(playSec: number): Chunk[] {
  const s = chunkStart(playSec);
  return [
    { start: s - CHUNK_SEC, end: s },
    { start: s, end: s + CHUNK_SEC },
  ];
}

/**
 * Roll 1s bars at or before cutSec into resSec bars. The bar holding cutSec is the forming
 * bar (seconds <= cutSec only). Input may be unsorted or duplicated; duplicates drop.
 */
export function rollUpSeconds(oneSecBars: Candle[], resSec: number, cutSec: number): Candle[] {
  const rows = oneSecBars
    .map((b) => ({ b, t: Math.floor(Date.parse(b.ts) / 1000) }))
    .filter((r) => Number.isFinite(r.t) && r.t <= cutSec)
    .sort((a, b) => a.t - b.t);
  const out: Candle[] = [];
  let lastT = NaN;
  let cur: Candle | null = null;
  let curBucket = NaN;
  for (const { b, t } of rows) {
    if (t === lastT) continue;
    lastT = t;
    const bucket = Math.floor(t / resSec) * resSec;
    if (cur && bucket === curBucket) {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
      cur.volume += b.volume;
    } else {
      curBucket = bucket;
      cur = {
        symbol: b.symbol,
        ts: new Date(bucket * 1000).toISOString(),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
        timeframe: `${resSec}s`,
        source_label: b.source_label,
      };
      out.push(cur);
    }
  }
  return out;
}

export interface SecPos {
  index: number;
  offset: number;
}

/** (index, offset 0..59) shifted by n seconds with carry, clamped to [0:0, total-1:59]. */
export function shiftSec(pos: SecPos, n: number, total: number): SecPos {
  if (total <= 0) return { index: 0, offset: 0 };
  const flat = pos.index * 60 + pos.offset + n;
  const max = (total - 1) * 60 + 59;
  const c = Math.max(0, Math.min(flat, max));
  return { index: Math.floor(c / 60), offset: c % 60 };
}
