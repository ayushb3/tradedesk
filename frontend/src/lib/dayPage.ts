/**
 * Pure derivations for the Days day page: verdict band, trade bands over the SPY
 * session strip, and time-ordered trade rows. No React, no fetching.
 */
import type { DeskDayDetail, DeskPosition, RuleResult } from "./api";

export type Tone = "kept" | "broken" | "unknown";

export interface VerdictChip {
  key: string;
  label: string;
  tone: Tone;
}

export interface Verdict {
  net: number;
  /** Sum of R over trades that have one; null when none do. */
  sumR: number | null;
  rCount: number;
  assumedCount: number;
  chips: VerdictChip[];
}

function usd(n: number): string {
  const v = `$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return n < 0 ? `-${v}` : v;
}

const RULE_LABEL: Record<RuleResult["code"], string> = {
  before_cutoff: "After cutoff",
  daily_stop: "Past the daily stop",
  max_loss_written: "No max loss written",
  call_source: "Own idea",
  size_within_plan: "Over plan size",
};

export function ruleLabel(code: RuleResult["code"]): string {
  return RULE_LABEL[code] ?? code;
}

/** "07:12:30 PT" -> "07:12 PT". */
export function clockPt(pt: string | null | undefined): string {
  if (!pt) return "—";
  const m = /^(\d{1,2}:\d{2})(?::\d{2})?\s*(PT)?/.exec(pt.trim());
  return m ? `${m[1]} PT` : pt;
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export function ruleOf(p: DeskPosition, code: RuleResult["code"]): RuleResult | undefined {
  return p.rule_results.find((r) => r.code === code);
}

export function dayVerdict(day: DeskDayDetail): Verdict {
  const withR = day.positions.filter((p) => p.r != null && Number.isFinite(p.r));
  const sumR = withR.length ? withR.reduce((a, p) => a + (p.r as number), 0) : null;
  const assumedCount = withR.filter((p) => p.r_basis === "assumed").length;

  const chips: VerdictChip[] = [];
  const stop = day.stop_result;
  if (stop) {
    if (!stop.hit) {
      chips.push({ key: "stop", label: "Daily stop not hit", tone: "kept" });
    } else {
      const pastTol = !!stop.past_tolerance;
      const broken = stop.trades_after > 0 || pastTol;
      chips.push({
        key: "stop",
        label: `Daily stop hit ${clockPt(stop.hit_at_pt)}`,
        tone: broken ? "broken" : "kept",
      });
      if (stop.trades_after > 0) {
        chips.push({
          key: "after-stop",
          label: `${plural(stop.trades_after, "trade", "trades")} after stop`,
          tone: "broken",
        });
      }
      const tol = stop.tolerance_pct ?? 0;
      if (pastTol) {
        chips.push({
          key: "blown",
          label: `Past the ${usd(stop.limit)} stop by more than ${tol}%`,
          tone: "broken",
        });
      } else if (stop.blown_past) {
        chips.push({ key: "blown", label: `Past the stop, within ${tol}%`, tone: "unknown" });
      }
    }
  }

  const late = day.positions.filter((p) => ruleOf(p, "before_cutoff")?.kept === false).length;
  if (late > 0) {
    chips.push({ key: "cutoff", label: `${late} after cutoff`, tone: "broken" });
  }

  chips.push(
    day.stats.net >= day.daily_target
      ? { key: "target", label: "Target reached", tone: "kept" }
      : { key: "target", label: "Target not reached", tone: "unknown" },
  );

  return { net: day.stats.net, sumR, rCount: withR.length, assumedCount, chips };
}

export function fillSpan(p: DeskPosition): { startMs: number; endMs: number } | null {
  const ts = p.fills.map((f) => Date.parse(f.ts)).filter(Number.isFinite);
  if (!ts.length) return null;
  return { startMs: Math.min(...ts), endMs: Math.max(...ts) };
}

/** Regular session in PT, the default span of the day strip. */
export const SESSION_OPEN_PT = "06:30";
export const SESSION_CLOSE_PT = "13:00";

/** UTC ms of a PT wall-clock time ("HH:MM") on a calendar date. */
export function ptWallToMs(date: string, hhmm: string): number {
  const [y, mo, d] = date.split("-").map(Number);
  const [h, mi] = hhmm.split(":").map(Number);
  const want = Date.UTC(y, mo - 1, d, h, mi);
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Los_Angeles",
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  // PT is UTC-7 or UTC-8: pick the offset whose wall clock reads `want`.
  for (const off of [7, 8]) {
    const ms = want + off * 3_600_000;
    const p = Object.fromEntries(fmt.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
    if (Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute) === want) return ms;
  }
  return want + 8 * 3_600_000;
}

/**
 * The span of the session strip: the regular session, widened only to include
 * trades that fall outside it (with a small pad so an edge band stays visible).
 */
export function stripWindow(
  date: string,
  positions: DeskPosition[],
  padMs = 5 * 60_000,
): { startMs: number; endMs: number } {
  let startMs = ptWallToMs(date, SESSION_OPEN_PT);
  let endMs = ptWallToMs(date, SESSION_CLOSE_PT);
  for (const p of positions) {
    const s = fillSpan(p);
    if (!s) continue;
    if (s.startMs < startMs) startMs = s.startMs - padMs;
    if (s.endMs > endMs) endMs = s.endMs + padMs;
  }
  return { startMs, endMs };
}

export interface TradeBand {
  tradeId: number;
  leftPct: number;
  widthPct: number;
  tone: "gain" | "loss" | "flat";
}

/**
 * Each trade's hold as a band on the session strip. The strip spans
 * [startMs, endMs]; bands outside it are clamped, and none is thinner than
 * `minPct` so a short hold stays clickable.
 */
export function tradeBands(
  positions: DeskPosition[],
  startMs: number,
  endMs: number,
  minPct = 0.6,
): TradeBand[] {
  const span = endMs - startMs;
  if (!(span > 0)) return [];
  const out: TradeBand[] = [];
  for (const p of positions) {
    const s = fillSpan(p);
    if (!s) continue;
    const a = Math.min(100, Math.max(0, ((s.startMs - startMs) / span) * 100));
    const b = Math.min(100, Math.max(0, ((s.endMs - startMs) / span) * 100));
    const widthPct = Math.max(minPct, b - a);
    out.push({
      tradeId: p.trade_id,
      leftPct: Math.min(a, 100 - widthPct),
      widthPct,
      tone: p.pnl == null || p.pnl === 0 ? "flat" : p.pnl > 0 ? "gain" : "loss",
    });
  }
  return out;
}

export interface TradeRow {
  tradeId: number;
  timePt: string;
  contract: string;
  r: number | null;
  assumed: boolean;
  pnl: number | null;
  open: boolean;
  broken: RuleResult[];
  keptCount: number;
}

export function contractLabel(p: DeskPosition): string {
  return p.label || `${p.ticker} ${p.strike}${p.type}`.trim();
}

/** Trade rows in time order (first fill), with broken rules split from kept ones. */
export function tradeRows(positions: DeskPosition[]): TradeRow[] {
  return positions
    .map((p) => ({ p, at: fillSpan(p)?.startMs ?? Number.POSITIVE_INFINITY }))
    .sort((x, y) => x.at - y.at || x.p.trade_id - y.p.trade_id)
    .map(({ p }) => ({
      tradeId: p.trade_id,
      timePt: clockPt(p.fills[0]?.ts_pt),
      contract: contractLabel(p),
      r: p.r != null && Number.isFinite(p.r) ? p.r : null,
      assumed: p.r_basis === "assumed",
      pnl: p.pnl,
      open: p.status !== "closed" && p.pnl == null,
      broken: p.rule_results.filter((r) => r.kept === false),
      keptCount: p.rule_results.filter((r) => r.kept === true).length,
    }));
}

/** SVG polyline points for a close-price line scaled into a w x h box. */
export function linePoints(
  closes: { ms: number; close: number }[],
  w: number,
  h: number,
  startMs: number,
  endMs: number,
): string {
  if (closes.length < 2 || !(endMs > startMs)) return "";
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of closes) {
    lo = Math.min(lo, c.close);
    hi = Math.max(hi, c.close);
  }
  const range = hi - lo || 1;
  return closes
    .map((c) => {
      const x = ((c.ms - startMs) / (endMs - startMs)) * w;
      const y = h - ((c.close - lo) / range) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}
