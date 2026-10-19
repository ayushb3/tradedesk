/**
 * Pure helpers for the Trade review verdict band and story strip.
 *
 * R is never computed here: the hero reads `trade.r_multiple` and the story strip reads
 * the excursions endpoint, which measures on the same ledger risk (`resolve_r`), so the
 * numbers agree. Nothing here carries a dollar figure for the after-exit path.
 */
import type { ReviewDay, RuleResult, TradeExcursions } from "./api";
import { formatR } from "./format";

export type Tone = "kept" | "broken" | "unknown";

export interface RuleChip {
  code: RuleResult["code"];
  label: string;
  tone: Tone;
  /** Hover text: the rule's own detail from the API. */
  detail: string;
}

const KEPT_LABELS: Record<RuleResult["code"], string> = {
  before_cutoff: "Before cutoff",
  daily_stop: "Daily stop OK",
  max_loss_written: "Max loss written",
  call_source: "Call source ?",
  size_within_plan: "Size within plan",
};

const BROKEN_LABELS: Partial<Record<RuleResult["code"], string>> = {
  before_cutoff: "After cutoff",
  daily_stop: "Daily stop broken",
  max_loss_written: "No max loss written",
  call_source: "Own idea",
  size_within_plan: "Over planned size",
};

/** Unknown (kept === null): neutral wording, never the kept label. */
const UNKNOWN_LABELS: Record<RuleResult["code"], string> = {
  before_cutoff: "Cutoff ?",
  daily_stop: "Daily stop ?",
  max_loss_written: "Max loss ?",
  call_source: "Call source ?",
  size_within_plan: "No plan size",
};

const ORDER: RuleResult["code"][] = [
  "call_source",
  "max_loss_written",
  "before_cutoff",
  "daily_stop",
  "size_within_plan",
];

const toneOf = (kept: boolean | null): Tone => (kept === true ? "kept" : kept === false ? "broken" : "unknown");

export function ruleChips(results: RuleResult[] | undefined | null): RuleChip[] {
  const byCode = new Map((results ?? []).map((r) => [r.code, r]));
  return ORDER.filter((c) => byCode.has(c)).map((code) => {
    const r = byCode.get(code)!;
    let label = KEPT_LABELS[code];
    if (code === "call_source" && r.kept === true) label = "Mentor call";
    else if (r.kept === false) label = BROKEN_LABELS[code] ?? label;
    else if (r.kept === null) label = UNKNOWN_LABELS[code];
    return { code, label, tone: toneOf(r.kept), detail: r.detail };
  });
}

// ---------------------------------------------------------------------------
// Day tally

export interface DayTally {
  /** Net of every position closed at or before this trade's close, this one included. */
  net: number;
  trades: number;
  /** The daily stop (negative), from the day's stop result. */
  limit: number | null;
  /** At or past the stop after this trade. */
  atStop: boolean;
  /** When the day's stop was hit (whole day, as the API reports it), or null. */
  hitAtPt: string | null;
}

const finite = (n: number | null | undefined): n is number => typeof n === "number" && Number.isFinite(n);
const lastFill = (p: ReviewDay["positions"][number]) =>
  p.fills.reduce((mx, f) => Math.max(mx, Date.parse(f.ts)), -Infinity);

/** The day's net through this trade's close versus the daily stop. Null while the trade is open. */
export function dayTally(day: ReviewDay | null | undefined, tradeId: number): DayTally | null {
  if (!day) return null;
  const own = day.positions.find((p) => p.trade_id === tradeId);
  if (!own || own.status === "open") return null;
  const cutoff = lastFill(own);
  if (!Number.isFinite(cutoff)) return null;
  let sum = 0;
  let trades = 0;
  for (const p of day.positions) {
    if (p.status === "open" || !finite(p.pnl)) continue;
    if (lastFill(p) <= cutoff) {
      sum += p.pnl;
      trades += 1;
    }
  }
  const net = Math.round(sum * 100) / 100;
  const limit = finite(day.stop_result?.limit) ? day.stop_result!.limit : null;
  return {
    net,
    trades,
    limit,
    atStop: limit !== null && net <= limit + 1e-9,
    hitAtPt: day.stop_result?.hit ? (day.stop_result.hit_at_pt ?? null) : null,
  };
}

// ---------------------------------------------------------------------------
// Story strip

export interface StoryItem {
  key: "best" | "stop" | "worst" | "after";
  label: string;
  value: string;
  sub: string | null;
  /** Shown muted ("estimated from thin bars") because the quality check trimmed wicks. */
  estimated: boolean;
}

const hhmm = (pt: string | null | undefined) => (pt ? pt.replace(/(\d{1,2}:\d{2}):\d{2}/, "$1") : null);

export function storyStrip(x: TradeExcursions | undefined | null): StoryItem[] | null {
  if (!x || !x.available || !x.hold) return null;
  const h = x.hold;
  const est = x.quality === "suspect_wicks" || x.quality === "short_hold";
  const items: StoryItem[] = [];

  items.push({
    key: "best",
    label: "Best point",
    value: h.mfe_ts_pt ? formatR(h.mfe_r) : "Never over cost",
    sub: h.mfe_ts_pt ? `at ${hhmm(h.mfe_ts_pt)}` : null,
    estimated: est && !!h.mfe_ts_pt,
  });

  if (h.crossed_stop) {
    const m = h.crossed_stop_minutes;
    items.push({
      key: "stop",
      label: "Stop hit",
      value: hhmm(h.crossed_stop_at_pt) ?? "—",
      sub: m == null ? null : m === 1 ? "1 min after entry" : `${m} min after entry`,
      estimated: est,
    });
  } else {
    items.push({ key: "stop", label: "Stop", value: "Stop not touched", sub: null, estimated: est });
  }

  items.push({
    key: "worst",
    label: "Worst point",
    value: h.mae_ts_pt ? formatR(h.mae_r) : "Never under cost",
    sub: h.mae_ts_pt ? `at ${hhmm(h.mae_ts_pt)}` : null,
    estimated: est && !!h.mae_ts_pt,
  });

  const a = x.after_exit;
  if (x.status === "closed" && a) {
    if (a.bars === 0) {
      items.push({ key: "after", label: "After exit", value: "No bars after the exit", sub: null, estimated: false });
    } else if (a.returned_at_pt) {
      items.push({
        key: "after",
        label: "After exit",
        value: `Back to entry by ${hhmm(a.returned_at_pt)}`,
        sub: null,
        estimated: false,
      });
    } else {
      items.push({
        key: "after",
        label: "After exit",
        value: `Never back to entry in ${a.window_min} min`,
        sub: a.complete ? null : "partial window",
        estimated: false,
      });
    }
  }
  return items;
}
