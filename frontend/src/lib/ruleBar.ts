/**
 * Rule bar state for the replayed trade: the day as it stood BEFORE the entry.
 *
 * The server filters the day (`GET /api/replay/trade/{id}/day-so-far`): it
 * returns only aggregates of positions closed strictly before this entry, with
 * no later trade and no end-of-day flag, so nothing from the future reaches the
 * browser. This module only maps that response to display state. Anything
 * missing stays null and renders as "—".
 */
import type { ReplayDaySoFar } from "./api";

export interface RuleBarState {
  /** Net P&L of earlier, fully closed trades; null when unavailable. */
  dayNet: number | null;
  /** How many earlier trades make up dayNet. */
  priorTrades: number;
  stop: number | null;
  target: number | null;
  /** Cutoff as "HH:MM", and whether this entry was before or after it. */
  cutoff: string | null;
  entry: "before" | "after" | null;
  /** Call source for this trade: its mentor/own field, else its plan's caller. */
  caller: string | null;
}

const finite = (n: number | undefined | null): n is number =>
  typeof n === "number" && Number.isFinite(n);

export function ruleBarState(day: ReplayDaySoFar | null | undefined): RuleBarState {
  return {
    dayNet: finite(day?.net_so_far) ? day!.net_so_far : null,
    priorTrades: day?.prior_trades ?? 0,
    stop: finite(day?.daily_stop) ? day!.daily_stop : null,
    target: finite(day?.daily_target) ? day!.daily_target : null,
    cutoff: day?.cutoff ?? null,
    entry: day?.entry ?? null,
    caller: day?.call_source || null,
  };
}

/**
 * Meter geometry for a diverging bar from the stop (left) to the target (right),
 * zero in between. Returns percentages of the bar width, or null when the stop or
 * target is unusable.
 */
export function meterGeometry(
  net: number | null,
  stop: number | null,
  target: number | null,
): { zeroPct: number; fillLeftPct: number; fillWidthPct: number } | null {
  if (net === null || stop === null || target === null || !(stop < 0) || !(target > 0)) return null;
  const span = target - stop;
  const zeroPct = (-stop / span) * 100;
  const clamped = Math.min(target, Math.max(stop, net));
  const endPct = ((clamped - stop) / span) * 100;
  return {
    zeroPct,
    fillLeftPct: Math.min(zeroPct, endPct),
    fillWidthPct: Math.abs(endPct - zeroPct),
  };
}
