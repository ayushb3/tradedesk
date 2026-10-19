/**
 * Rule bar state for the replayed trade: the day as it stood BEFORE the entry.
 *
 * The day API returns the whole day, including trades after this one. Only
 * positions that are fully closed with their last fill strictly before the
 * entry count, so nothing from the future reaches the screen. Day-level
 * end-of-day flags (stop hit, target hit) are never read. Anything missing
 * stays null and renders as "—".
 */
import type { DeskDay, DeskRules } from "./api";
import { DISPLAY_TZ } from "./format";

export interface RuleBarState {
  /** Net P&L of earlier, fully closed trades; null when the day is unavailable. */
  dayNet: number | null;
  /** How many earlier trades make up dayNet. */
  priorTrades: number;
  stop: number | null;
  target: number | null;
  /** Cutoff as "HH:MM", and whether this entry was before or after it. */
  cutoff: string | null;
  entry: "before" | "after" | null;
  /** Call source for this trade: its plan's caller, else the day's callers. */
  caller: string | null;
}

const ptClock = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function ptMinutes(ms: number): number {
  const parts = ptClock.formatToParts(new Date(ms));
  const h = Number(parts.find((p) => p.type === "hour")?.value);
  const m = Number(parts.find((p) => p.type === "minute")?.value);
  return h * 60 + m;
}

function hhmmMinutes(v: string | undefined): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(v ?? "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

const finite = (n: number | undefined | null): n is number =>
  typeof n === "number" && Number.isFinite(n);

export function ruleBarState(input: {
  rules?: DeskRules | null;
  day?: DeskDay | null;
  tradeId: number;
  /** The replayed trade's entry time (ISO). */
  entryTs: string | null;
}): RuleBarState {
  const { rules, day, tradeId, entryTs } = input;
  const entryMs = entryTs ? Date.parse(entryTs) : NaN;
  const haveEntry = Number.isFinite(entryMs);

  let dayNet: number | null = null;
  let priorTrades = 0;
  if (day && haveEntry) {
    let sum = 0;
    for (const p of day.positions) {
      if (p.trade_id === tradeId || p.status === "open" || !finite(p.pnl)) continue;
      const last = p.fills.reduce((mx, f) => Math.max(mx, Date.parse(f.ts)), -Infinity);
      if (!(last < entryMs)) continue; // closed before this entry only
      sum += p.pnl;
      priorTrades += 1;
    }
    dayNet = Math.round(sum * 100) / 100;
  }

  const cutoff = rules?.window?.end ?? null;
  const cutoffMin = hhmmMinutes(cutoff ?? undefined);
  const entry =
    haveEntry && cutoffMin !== null ? (ptMinutes(entryMs) > cutoffMin ? "after" : "before") : null;

  const own = day?.positions.find((p) => p.trade_id === tradeId);
  const callers = day?.fields?.callers?.filter(Boolean) ?? [];
  const caller = own?.plan?.caller || (callers.length ? callers.join(", ") : null);

  return {
    dayNet,
    priorTrades,
    stop: finite(rules?.daily_stop) ? rules!.daily_stop : null,
    target: finite(rules?.daily_target) ? rules!.daily_target : null,
    cutoff,
    entry,
    caller,
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
