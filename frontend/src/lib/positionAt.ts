/**
 * Pure long-only options position math at a point in time (no React).
 * No lookahead: fills after `atSec` are never applied.
 */

export const CONTRACT_MULTIPLIER = 100;

export interface OpenPosition {
  /** Open contracts. */
  qty: number;
  /** Blended average entry premium per share; 0 while flat. */
  avg: number;
  /** Realized dollars so far. */
  realized: number;
}

interface FillLike {
  side: "buy" | "sell";
  qty: number;
  price: number;
  ts: string;
}

export function positionAt(fills: readonly FillLike[], atSec: number): OpenPosition {
  const dated = fills
    .map((f, i) => ({ f, i, sec: Math.floor(Date.parse(f.ts) / 1000) }))
    .filter((x) => Number.isFinite(x.sec) && x.sec <= atSec)
    .sort((a, b) => a.sec - b.sec || a.i - b.i);

  let qty = 0;
  let avg = 0;
  let realized = 0;
  for (const { f } of dated) {
    if (f.side === "buy") {
      const n = qty + f.qty;
      if (!(n > 0)) continue;
      avg = (avg * qty + f.price * f.qty) / n;
      qty = n;
    } else {
      const q = Math.min(f.qty, qty);
      if (!(q > 0)) continue;
      realized += (f.price - avg) * q * CONTRACT_MULTIPLIER;
      qty -= q;
      if (qty === 0) avg = 0;
    }
  }
  return { qty, avg, realized };
}

/** Open P&L in dollars at `mark`; null when flat or no mark. */
export function openPnl(pos: OpenPosition, mark: number | null): number | null {
  if (pos.qty === 0 || mark == null) return null;
  return (mark - pos.avg) * pos.qty * CONTRACT_MULTIPLIER;
}

/** "+$58.00" / "-$12.50" / "$0.00". */
export function formatPnl(v: number): string {
  const abs = Math.abs(v);
  if (Number(abs.toFixed(2)) === 0) return "$0.00";
  const body = abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v < 0 ? "-" : "+"}$${body}`;
}
