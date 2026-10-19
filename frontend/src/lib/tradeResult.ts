/**
 * Pure result math for the Replay result card and the "Your real trade" card.
 * Used for BOTH the sim round trips and the real trade, so the two verdicts are
 * computed by the same rule.
 *
 * STOP VERDICT (exact definition):
 *  - Bars considered: 1-minute premium bars strictly AFTER the minute of the
 *    first buy, up to and including the minute of the last sell (the position
 *    was open for the whole of each such bar).
 *  - A bar is "past stop" when its LOW is at or below the stop in force at that
 *    bar. The stop in force is the latest stop set in an EARLIER minute (the
 *    first buy's stop, then each dragged stop from the bar after it was
 *    released), so `stopMoves` count.
 *  - Respected when no bar is past stop. Otherwise "held N min past stop" where
 *    N = number of 1-minute bars from the first past-stop bar through the exit
 *    bar, inclusive (the minutes from that bar's open to the end of the exit bar).
 */

const MINUTE = 60;
export const MULT = 100;
export const ASSUMED_FRAC = 0.3;

const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);
const minuteOf = (iso: string) => Math.floor(sec(iso) / MINUTE) * MINUTE;

export interface BarLow {
  ts: string;
  low: number;
}
export interface StopAt {
  ts: string;
  stop: number;
}

export interface StopVerdict {
  respected: boolean;
  /** Minutes held past the stop; 0 when respected. */
  minutesPast: number;
  /** ISO ts of the first past-stop bar, or null when respected. */
  firstBreachTs: string | null;
}

export function stopVerdict(args: {
  bars: BarLow[];
  /** Time of the first buy / entry fill. */
  openTs: string;
  /** Time of the last sell / exit fill (position flat). */
  flatTs: string;
  /** Stop history, oldest first (a single entry for a fixed stop). */
  stops: StopAt[];
}): StopVerdict {
  const lo = minuteOf(args.openTs);
  const hi = minuteOf(args.flatTs);
  const stops = [...args.stops].sort((a, b) => sec(a.ts) - sec(b.ts));
  const bars = [...args.bars].sort((a, b) => sec(a.ts) - sec(b.ts));

  let firstBreach: number | null = null;
  for (const bar of bars) {
    const m = minuteOf(bar.ts);
    if (m <= lo || m > hi) continue;
    let inForce: number | null = null;
    for (const s of stops) {
      if (minuteOf(s.ts) < m) inForce = s.stop;
      else break;
    }
    if (inForce !== null && bar.low <= inForce) {
      firstBreach = m;
      break;
    }
  }
  if (firstBreach === null) return { respected: true, minutesPast: 0, firstBreachTs: null };
  return {
    respected: false,
    minutesPast: (hi - firstBreach) / MINUTE + 1,
    firstBreachTs: new Date(firstBreach * 1000).toISOString(),
  };
}

export interface RealFill {
  role: "entry" | "exit";
  side: "buy" | "sell";
  qty: number;
  price: number;
  ts: string;
}

export interface RealTradeSummary {
  avgIn: number;
  qtyIn: number;
  openTs: string;
  /** The written stop, or the assumed -30% stop when none was written. */
  stop: number;
  stopAssumed: boolean;
  /** Present only when the caller asked for it AND exit fills exist. */
  exit: {
    avgOut: number;
    flatTs: string;
    pnl: number;
    /** pnl / 1R where 1R = (avgIn - stop) x 100 x qty. null if the stop is unusable. */
    r: number | null;
  } | null;
}

const wavg = (xs: RealFill[]) =>
  xs.reduce((s, f) => s + f.price * f.qty, 0) / xs.reduce((s, f) => s + f.qty, 0);

/**
 * Summarize the real trade from the fills revealed so far. Long premium only
 * (entries are buys); returns null with no entry yet or for a short entry.
 * The exit part is computed only when `includeExit` is true AND exit fills
 * exist, so the caller controls the reveal.
 */
export function summarizeRealTrade(
  fills: RealFill[],
  writtenStop: number | null | undefined,
  includeExit: boolean,
  /** The API's flag: no max loss was written, so `writtenStop` is the default stop. */
  apiAssumed = false,
): RealTradeSummary | null {
  const ins = fills.filter((f) => f.role === "entry");
  if (ins.length === 0 || ins.some((f) => f.side !== "buy")) return null;
  const avgIn = wavg(ins);
  const qtyIn = ins.reduce((s, f) => s + f.qty, 0);
  const openTs = ins.reduce((a, f) => (sec(f.ts) < sec(a) ? f.ts : a), ins[0].ts);
  const usable =
    writtenStop != null && Number.isFinite(writtenStop) && writtenStop > 0 && writtenStop < avgIn;
  const stop = usable
    ? (writtenStop as number)
    : Math.round(avgIn * (1 - ASSUMED_FRAC) * 10000) / 10000;

  const outs = fills.filter((f) => f.role === "exit");
  let exit: RealTradeSummary["exit"] = null;
  if (includeExit && outs.length > 0) {
    const avgOut = wavg(outs);
    const qtyOut = outs.reduce((s, f) => s + f.qty, 0);
    const flatTs = outs.reduce((a, f) => (sec(f.ts) > sec(a) ? f.ts : a), outs[0].ts);
    const risk = (avgIn - stop) * MULT * qtyIn;
    const pnl = (avgOut - avgIn) * MULT * qtyOut;
    exit = { avgOut, flatTs, pnl, r: risk > 0 ? pnl / risk : null };
  }
  return { avgIn, qtyIn, openTs, stop, stopAssumed: !usable || apiAssumed, exit };
}
