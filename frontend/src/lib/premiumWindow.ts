/**
 * Default visible window for the option premium chart: the trade, not the whole
 * session. Entry minus 15 min through exit plus 30 min, clamped to the available
 * bars. An open trade runs to the last bar. All values are epoch seconds (UTC).
 */
export const PRE_ENTRY_S = 15 * 60;
export const POST_EXIT_S = 30 * 60;

export interface TimeWindow {
  from: number;
  to: number;
}

export function premiumWindow(
  entryS: number,
  exitS: number | null,
  firstBarS: number,
  lastBarS: number,
): TimeWindow {
  const from = Math.max(firstBarS, entryS - PRE_ENTRY_S);
  const rawTo = exitS == null ? lastBarS : exitS + POST_EXIT_S;
  const to = Math.min(lastBarS, rawTo);
  // Degenerate input (trade outside the bars): fall back to the whole series.
  if (!(from < to)) return { from: firstBarS, to: lastBarS };
  return { from, to };
}
