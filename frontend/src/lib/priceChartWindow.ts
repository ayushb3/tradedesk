/**
 * Trade-window arithmetic shared by reviewChart.ts and priceChart.ts
 * (kept in its own file so the two do not import each other).
 */
const MINUTE = 60;
const floorMinute = (sec: number): number => Math.floor(sec / MINUTE) * MINUTE;

export interface FitWindow {
  startSec: number;
  endSec: number;
  /** Set when the bars end before the wanted window does: the last bar's minute. */
  noDataAfterSec: number | null;
}

/**
 * Entry - lead .. exit + tail, fitted to the bars that exist. If the bars end early the
 * window ends at the last bar (no dead right side) and `noDataAfterSec` says where.
 * An open trade (no exit) runs to the last bar.
 */
export function fitWindow(opts: {
  entrySec: number;
  exitSec: number | null;
  firstBarSec: number;
  lastBarSec: number;
  leadMin?: number;
  tailMin?: number;
}): FitWindow {
  const lead = (opts.leadMin ?? 15) * MINUTE;
  const tail = (opts.tailMin ?? 30) * MINUTE;
  const first = floorMinute(opts.firstBarSec);
  const start = Math.max(first, floorMinute(opts.entrySec) - lead);
  const wantedEnd = opts.exitSec === null ? opts.lastBarSec : floorMinute(opts.exitSec) + tail;
  const end = Math.min(opts.lastBarSec, wantedEnd);
  if (!(start < end)) return { startSec: first, endSec: opts.lastBarSec, noDataAfterSec: null };
  const endedEarly = opts.exitSec !== null && opts.lastBarSec < wantedEnd;
  return { startSec: start, endSec: end, noDataAfterSec: endedEarly ? floorMinute(opts.lastBarSec) : null };
}
