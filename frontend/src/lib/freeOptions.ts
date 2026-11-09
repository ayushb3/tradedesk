/**
 * Pure helpers for free-replay 0DTE option contracts (practice only; nothing
 * calls a broker). No React.
 */
import type { Candle } from "@/lib/api";
import { isoToUtcSeconds } from "@/lib/chart";

export type { Candle };

export type OptionRoot = "SPY" | "QQQ" | "SPXW";
export const OPTION_ROOTS: readonly OptionRoot[] = ["SPY", "QQQ", "SPXW"];

/** Caller normalises; this upper-cases for the compare. */
export function isOptionRoot(sym: string): sym is OptionRoot {
  return (OPTION_ROOTS as readonly string[]).includes(sym.toUpperCase());
}

export function strikeStep(root: OptionRoot): number {
  return root === "SPXW" ? 5 : 1;
}

/** The SPXW underlying chart is the SPY proxy, so the SPX level is approximated as SPY x 10. */
export function underlyingScale(root: OptionRoot): number {
  return root === "SPXW" ? 10 : 1;
}

export function atmStrike(root: OptionRoot, underlyingPrice: number): number {
  const step = strikeStep(root);
  return Math.round((underlyingPrice * underlyingScale(root)) / step) * step;
}

/**
 * Approximate public fee-schedule defaults (Robinhood-style per-contract fees):
 * ~$0.04 for equity options, ~$0.97 for index options incl. index/exchange fees.
 */
export function pricing(root: OptionRoot): { halfSpread: number; feePerContract: number } {
  return root === "SPXW"
    ? { halfSpread: 0.05, feePerContract: 0.97 }
    : { halfSpread: 0.01, feePerContract: 0.04 };
}

/** e.g. "SPY 600C 03/13" (MM/DD). */
export function contractLabel(
  root: OptionRoot,
  type: "call" | "put",
  strike: number,
  date: string,
): string {
  const [, mm, dd] = date.split("-");
  return `${root} ${strike}${type === "call" ? "C" : "P"} ${mm}/${dd}`;
}

/**
 * The last bar whose start is <= sec (bars ascending). stale = that bar's start
 * is not `sec`, i.e. the bar for the playhead minute is missing.
 */
export function contractBarAt(
  bars: Candle[],
  sec: number,
): { bar: Candle; stale: boolean } | null {
  let lo = 0;
  let hi = bars.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (isoToUtcSeconds(bars[mid].ts) <= sec) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (found < 0) return null;
  const bar = bars[found];
  return { bar, stale: isoToUtcSeconds(bar.ts) !== sec };
}

/** Close at -50%, no matter what (the desk's option rule). */
export const RULE_CLOSE_FRAC = 0.5;

/** stop is negative (e.g. -250): hit when dayPnl <= stop; target when dayPnl >= target. Nulls ignored. */
export function dayLimitHit(
  dayPnl: number,
  stop: number | null,
  target: number | null,
): "stop" | "target" | null {
  if (stop !== null && dayPnl <= stop) return "stop";
  if (target !== null && dayPnl >= target) return "target";
  return null;
}

const toMin = (hhmm: string) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

/** "HH:MM" PT windows; inclusive start, exclusive end. */
export function inTradingWindow(
  ptMinutes: number,
  windows: { start: string; end: string }[],
): boolean {
  return windows.some((w) => ptMinutes >= toMin(w.start) && ptMinutes < toMin(w.end));
}

const ptFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Los_Angeles",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** Minutes since midnight in America/Los_Angeles for an ISO timestamp. */
export function ptMinutesOf(isoTs: string): number {
  const parts = ptFmt.formatToParts(new Date(isoTs));
  const h = Number(parts.find((x) => x.type === "hour")?.value ?? 0) % 24;
  const m = Number(parts.find((x) => x.type === "minute")?.value ?? 0);
  return h * 60 + m;
}
