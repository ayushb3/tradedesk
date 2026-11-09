/**
 * Pure pieces of the Legend look: volume bars under the candles and the compact position pill
 * text. Kept free of React and lightweight-charts so `npm run check:sim` can run them.
 */

type Row = { time: number; open?: number; close?: number };

export interface VolumeBar {
  time: number;
  value: number;
  color: string;
}

/**
 * Volume bars for the drawn candle rows: one bar per drawn candle (whitespace rows get none),
 * colored by the candle's direction. `volumeAt` reads a row's volume (0 when unknown); bars
 * after `greyAfterSec` (review: after the exit) are grey like their candles.
 */
export function volumeBars(
  rows: readonly Row[],
  volumeAt: (time: number) => number | undefined,
  look: { up: string; down: string; grey: string; greyAfterSec?: number | null },
): VolumeBar[] {
  const out: VolumeBar[] = [];
  for (const r of rows) {
    if (r.open === undefined || r.close === undefined) continue;
    const v = volumeAt(r.time);
    const grey = look.greyAfterSec != null && r.time > look.greyAfterSec;
    out.push({
      time: r.time,
      value: v != null && Number.isFinite(v) && v > 0 ? v : 0,
      color: grey ? look.grey : r.close >= r.open ? look.up : look.down,
    });
  }
  return out;
}

/** "1.2M", "34.5K", "870": the legend's V value. */
export function formatVolume(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(1)}K`;
  return `${Math.round(v)}`;
}

/** A position's % change at `mark`: long gains when the mark is above the average, short when below. */
export function positionPct(avg: number, mark: number | null | undefined, qty: number): number | null {
  if (mark == null || !Number.isFinite(mark) || !(avg > 0) || qty === 0) return null;
  return ((mark - avg) / avg) * 100 * Math.sign(qty);
}

/** "▲17.08%", "▼3.20%", "0.00%", "—". */
export function pillPctText(pct: number | null): string {
  if (pct === null) return "—";
  const r = Math.round(pct * 100) / 100;
  if (r === 0) return "0.00%";
  return `${r > 0 ? "▲" : "▼"}${Math.abs(r).toFixed(2)}%`;
}

/** "+10", "-2": the pill's size. */
export function pillQtyText(qty: number): string {
  return qty > 0 ? `+${qty}` : `${qty}`;
}

/** "11/27 Call" from an expiry (YYYY-MM-DD) and side: the option's tag on the underlying chart. */
export function optionTag(expiry: string, side: "call" | "put"): string {
  return `${expiry.slice(5, 7)}/${expiry.slice(8, 10)} ${side === "call" ? "Call" : "Put"}`;
}

/** The latest close at or before `sec` (candles in time order), or null. */
export function closeAtOrBefore(
  candles: readonly { ts: string; close: number }[],
  sec: number,
): number | null {
  let out: number | null = null;
  for (const c of candles) {
    if (Math.floor(Date.parse(c.ts) / 1000) > sec) break;
    out = c.close;
  }
  return out;
}

/** "11/27 Call" from a contract label like "SPY 600C 11/27"; null when it isn't one. */
export function optionTagFromLabel(label: string | null | undefined): string | null {
  const m = /\s\d+(?:\.\d+)?([CP])\s+(\d{2}\/\d{2})\b/.exec(label ?? "");
  return m ? `${m[2]} ${m[1] === "C" ? "Call" : "Put"}` : null;
}
