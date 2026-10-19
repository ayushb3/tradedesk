/**
 * Pure derivations for the Days home: the rule scoreboard, calendar marks,
 * trades grouped by day with empty columns dropped, and rule breaks for the
 * Patterns section. No React, no fetching. P&L is carried beside rule state,
 * never folded into it.
 */
import type { CalendarData, DeskDaySummary, RuleResult, Trade } from "./api";

export type RuleCode = RuleResult["code"];

export const RULE_CODES: RuleCode[] = [
  "before_cutoff",
  "daily_stop",
  "max_loss_written",
  "call_source",
  "size_within_plan",
];

/** The rule as a thing to keep (the scoreboard row name), not the broken-state label. */
const RULE_NAME: Record<RuleCode, string> = {
  before_cutoff: "Done before the cutoff",
  daily_stop: "Daily stop respected",
  max_loss_written: "Max loss written before entry",
  call_source: "Mentor calls only",
  size_within_plan: "Size within plan",
};

export function ruleName(code: RuleCode): string {
  return RULE_NAME[code] ?? code;
}

export function inMonth(date: string, month: string): boolean {
  return date.startsWith(`${month}-`);
}

export interface ScoreRow {
  code: RuleCode;
  name: string;
  /** Days where the rule was kept. */
  kept: number;
  /** Days where it was broken: the headline. */
  broken: number;
  /** Days the data to judge it was never recorded. */
  unknown: number;
  /** Days traded in the range. */
  traded: number;
  /** Days with a recorded result (kept + broken). 0 means "not recorded". */
  known: number;
}

export interface Scoreboard {
  rows: ScoreRow[];
  tradedDays: number;
  /** Sum of broken days over every rule. */
  brokenTotal: number;
  /** Net P&L over the range, shown beside the rule state (never a score). */
  net: number;
}

function resultOf(d: DeskDaySummary, code: RuleCode): boolean | null {
  const r = d.rule_results.find((x) => x.code === code);
  return r ? r.kept : null;
}

/** One row per rule over the given days; only days with trades count as traded. */
export function ruleScoreboard(days: DeskDaySummary[]): Scoreboard {
  const traded = days.filter((d) => d.positions > 0);
  const rows: ScoreRow[] = RULE_CODES.map((code) => {
    let kept = 0;
    let broken = 0;
    let unknown = 0;
    for (const d of traded) {
      const k = resultOf(d, code);
      if (k === true) kept++;
      else if (k === false) broken++;
      else unknown++;
    }
    return { code, name: ruleName(code), kept, broken, unknown, traded: traded.length, known: kept + broken };
  });
  return {
    rows,
    tradedDays: traded.length,
    brokenTotal: rows.reduce((a, r) => a + r.broken, 0),
    net: traded.reduce((a, d) => a + d.net, 0),
  };
}

/**
 * Calendar data booked by session day, the same as /api/days (a position held
 * across days counts on its session_date), so month totals match the scoreboard.
 */
export function calendarFromDays(days: DeskDaySummary[]): CalendarData {
  const out: CalendarData = {};
  for (const d of days) {
    if (d.positions > 0) out[d.date] = { pnl: d.net, trades: d.positions };
  }
  return out;
}

export interface DayMark {
  /** Rule codes broken on the day, in rule order. */
  broken: RuleCode[];
  /** The day broke the daily stop (traded past it, or blew past it). */
  stopBreach: boolean;
}

/** Calendar marks keyed by date. Days with nothing broken are omitted. */
export function calendarMarks(days: DeskDaySummary[]): Record<string, DayMark> {
  const out: Record<string, DayMark> = {};
  for (const d of days) {
    if (d.positions <= 0) continue;
    const broken = RULE_CODES.filter((c) => resultOf(d, c) === false);
    const s = d.stop_result;
    const stopBreach = resultOf(d, "daily_stop") === false || (!!s && (s.trades_after > 0 || s.blown_past));
    if (broken.length || stopBreach) out[d.date] = { broken, stopBreach };
  }
  return out;
}

export interface DayGroup {
  date: string;
  trades: Trade[];
  net: number;
  brokenCount: number;
}

const brokenOf = (t: Trade) => (t.rule_results ?? []).filter((r) => r.kept === false);

/** Trades grouped by session day (newest day first), each day in time order. */
export function groupByDay(trades: Trade[]): DayGroup[] {
  const by = new Map<string, Trade[]>();
  for (const t of trades) {
    const list = by.get(t.session_date);
    if (list) list.push(t);
    else by.set(t.session_date, [t]);
  }
  return [...by.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0))
    .map(([date, list]) => {
      const sorted = [...list].sort((x, y) => Date.parse(x.open_ts) - Date.parse(y.open_ts) || x.id - y.id);
      return {
        date,
        trades: sorted,
        net: sorted.reduce((a, t) => a + (t.net_pnl ?? 0), 0),
        brokenCount: sorted.reduce((a, t) => a + brokenOf(t).length, 0),
      };
    });
}

export type OptionalColumn = "exit" | "r" | "grade" | "tags" | "rules";

/** Optional columns that hold at least one value across the shown trades. */
export function visibleColumns(trades: Trade[]): Set<OptionalColumn> {
  const out = new Set<OptionalColumn>();
  for (const t of trades) {
    if (t.avg_exit != null) out.add("exit");
    if (t.r_multiple != null && Number.isFinite(t.r_multiple)) out.add("r");
    if (t.enrichment?.grade) out.add("grade");
    if ((t.tags ?? []).length > 0) out.add("tags");
    if (brokenOf(t).length > 0) out.add("rules");
  }
  return out;
}

export interface RuleBreak {
  code: RuleCode;
  name: string;
  /** Distinct days it was broken on. */
  days: number;
  /** Broken trades, newest first. */
  trades: { id: number; date: string; label: string }[];
}

/** Broken rules across the shown trades, with the trades to open. Rules never broken are omitted. */
export function ruleBreaks(trades: Trade[]): RuleBreak[] {
  const out: RuleBreak[] = [];
  for (const code of RULE_CODES) {
    const hits = trades
      .filter((t) => brokenOf(t).some((r) => r.code === code))
      .sort((a, b) => (a.session_date < b.session_date ? 1 : a.session_date > b.session_date ? -1 : b.id - a.id));
    if (!hits.length) continue;
    out.push({
      code,
      name: ruleName(code),
      days: new Set(hits.map((t) => t.session_date)).size,
      trades: hits.map((t) => ({ id: t.id, date: t.session_date, label: t.contract_label ?? t.symbol })),
    });
  }
  return out.sort((a, b) => b.trades.length - a.trades.length);
}

/** "Sep 29 – Oct 16" style range of the trades' session days, or null when empty. */
export function tradeRange(trades: Trade[]): { from: string; to: string } | null {
  if (!trades.length) return null;
  const ds = trades.map((t) => t.session_date).sort();
  return { from: ds[0], to: ds[ds.length - 1] };
}

/** Latest session date that has at least one trade, or null when there are none. */
export function lastTradingDay(days: DeskDaySummary[]): string | null {
  let last: string | null = null;
  for (const d of days) {
    if (d.positions > 0 && (last === null || d.date > last)) last = d.date;
  }
  return last;
}
