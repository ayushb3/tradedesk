/**
 * Practice and group-of-trades review (pure, checked in check:sim).
 *
 * - Practice: free replay on a day's date at the money (/replay?mode=symbol&date=...).
 * - Session chips: the day's trades grouped into sessions by their times (a long pause
 *   splits them), named by the rules' windows (GET /api/rules) or the market phase. No
 *   window times live in code.
 * - Group review: a day replay limited to the chosen trades, from 5 min before the first
 *   entry to 10 min after the last exit, with one combined P&L.
 */
import type { DeskPosition, DeskRules } from "@/lib/api";

export const GROUP_LEAD_SEC = 5 * 60;
export const GROUP_TAIL_SEC = 10 * 60;

/** Free replay trades 0DTE options on these; SPX trades practise on SPXW. */
const PRACTICE_ROOTS = new Set(["SPY", "QQQ", "SPXW"]);
const PRACTICE_PROXY: Record<string, string> = { SPX: "SPXW" };

/** The underlying a day is practised on: its most-traded option root, else SPY. */
export function practiceSymbol(positions: readonly Pick<DeskPosition, "ticker">[]): string {
  const n = new Map<string, number>();
  for (const p of positions) {
    const t = PRACTICE_PROXY[p.ticker.toUpperCase()] ?? p.ticker.toUpperCase();
    if (PRACTICE_ROOTS.has(t)) n.set(t, (n.get(t) ?? 0) + 1);
  }
  let best = "SPY";
  let most = 0;
  for (const [t, c] of n) {
    if (c > most) {
      best = t;
      most = c;
    }
  }
  return best;
}

/** A real calendar date (YYYY-MM-DD), not in the future, on a weekday: a practice day. */
export function isPracticeDate(v: string | null | undefined, today = new Date()): v is string {
  if (!v || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const d = new Date(`${v}T12:00:00Z`);
  if (isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== v) return false;
  const dow = d.getUTCDay();
  return dow !== 0 && dow !== 6 && d.getTime() <= today.getTime() + 12 * 3600 * 1000;
}

/** Free replay on `date` (at the money is the player's default strike). */
export function practiceHref(date: string, symbol = "SPY", from?: string | null): string {
  const qs = new URLSearchParams({ mode: "symbol", date, symbol });
  if (from) qs.set("from", from);
  return `/replay?${qs.toString()}`;
}

const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** First fill (entry) and last fill (exit) of a position, epoch seconds. */
export function holdOf(p: Pick<DeskPosition, "fills">): { entry: number; exit: number } | null {
  if (p.fills.length === 0) return null;
  const ts = p.fills.map((f) => sec(f.ts)).sort((a, b) => a - b);
  return { entry: ts[0], exit: ts[ts.length - 1] };
}

/** "HH:MM" to minutes after midnight, or null. */
function hm(v: string | undefined): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(v?.trim() ?? "");
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

function minuteOfDay(epochSec: number, tz: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(epochSec * 1000));
  const h = Number(parts.find((p) => p.type === "hour")?.value);
  const m = Number(parts.find((p) => p.type === "minute")?.value);
  return h * 60 + m;
}

export interface SessionGroup {
  key: string;
  label: string;
  /** The trades' span, first entry to last exit, "HH:MM–HH:MM" in the rules' zone. */
  range: string;
  tradeIds: number[];
}

const fmtHm = (m: number) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;

/** A pause longer than this between trades starts a new session. */
export const SESSION_GAP_SEC = 20 * 60;

/** Market phase of a moment, by New York time: the label when no rules window names it. */
function phaseOf(epochSec: number): string {
  const m = minuteOfDay(epochSec, "America/New_York");
  if (m < 11 * 60) return "Morning";
  if (m >= 15 * 60 + 30) return "MOC";
  return "Midday";
}

/**
 * The day's sessions, built from the trade times: trades in time order, split wherever no
 * trade is open for SESSION_GAP_SEC. A session is named by the rules window its first entry
 * falls in (the main window is "Morning"; extra windows use their label), else by the market
 * phase (Morning, Midday, MOC = the last 30 minutes). Repeated names get a number.
 */
export function sessionGroups(
  positions: readonly Pick<DeskPosition, "trade_id" | "fills">[],
  rules: Pick<DeskRules, "tz" | "window" | "extra_windows"> | null | undefined,
  fallbackTz = "America/Los_Angeles",
): SessionGroup[] {
  const tz = rules?.tz || fallbackTz;
  const wins: { label: string; start: number; end: number }[] = [];
  const ms = hm(rules?.window?.start);
  const me = hm(rules?.window?.end);
  if (ms != null && me != null) wins.push({ label: "Morning", start: ms, end: me });
  for (const w of rules?.extra_windows ?? []) {
    const ws = hm(w.start);
    const we = hm(w.end);
    if (ws != null && we != null) wins.push({ label: w.label?.trim() || `${fmtHm(ws)}–${fmtHm(we)}`, start: ws, end: we });
  }

  const held = positions
    .map((p) => ({ id: p.trade_id, h: holdOf(p) }))
    .filter((x): x is { id: number; h: { entry: number; exit: number } } => x.h !== null)
    .sort((a, b) => a.h.entry - b.h.entry);
  const clusters: { ids: number[]; first: number; last: number }[] = [];
  for (const x of held) {
    const c = clusters[clusters.length - 1];
    if (c && x.h.entry - c.last <= SESSION_GAP_SEC) {
      c.ids.push(x.id);
      c.last = Math.max(c.last, x.h.exit);
    } else clusters.push({ ids: [x.id], first: x.h.entry, last: x.h.exit });
  }

  const seen = new Map<string, number>();
  return clusters.map((c) => {
    const m = minuteOfDay(c.first, tz);
    const base = wins.find((w) => m >= w.start && m < w.end)?.label ?? phaseOf(c.first);
    const n = (seen.get(base) ?? 0) + 1;
    seen.set(base, n);
    return {
      key: `${c.first}`,
      label: n > 1 ? `${base} ${n}` : base,
      range: `${fmtHm(m)}–${fmtHm(minuteOfDay(c.last, tz))}`,
      tradeIds: c.ids,
    };
  });
}

/** /replay?day=...&trades=1,2,3: the day replay limited to these trades. */
export function groupReplayHref(date: string, tradeIds: readonly number[], symbol?: string | null): string {
  const qs = new URLSearchParams({ day: date, trades: [...tradeIds].sort((a, b) => a - b).join(",") });
  if (symbol) qs.set("symbol", symbol);
  qs.set("from", `/days/${date}`);
  return `/replay?${qs.toString()}`;
}

/** Parse ?trades=1,2,3 (positive integers only), or null. */
export function parseTradeIds(v: string | null): number[] | null {
  if (!v) return null;
  const ids = v
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
  return ids.length ? [...new Set(ids)] : null;
}

/** The group's play window: 5 min before the first entry to 10 min after the last exit (whole minutes). */
export function groupWindow(
  positions: readonly Pick<DeskPosition, "trade_id" | "fills">[],
  ids: readonly number[],
): { startSec: number; endSec: number } | null {
  const holds = positions
    .filter((p) => ids.includes(p.trade_id))
    .map(holdOf)
    .filter((h): h is { entry: number; exit: number } => h !== null);
  if (holds.length === 0) return null;
  const first = Math.min(...holds.map((h) => h.entry));
  const last = Math.max(...holds.map((h) => h.exit));
  return {
    startSec: Math.floor((first - GROUP_LEAD_SEC) / 60) * 60,
    endSec: Math.floor((last + GROUP_TAIL_SEC) / 60) * 60,
  };
}

/** Entry times of the group's trades, in order (for Next / Prev). */
export function groupEntries(
  positions: readonly Pick<DeskPosition, "trade_id" | "fills">[],
  ids: readonly number[],
): number[] {
  return positions
    .filter((p) => ids.includes(p.trade_id))
    .map((p) => holdOf(p)?.entry)
    .filter((s): s is number => s != null)
    .sort((a, b) => a - b);
}

/**
 * The entry after (dir 1) or before (dir -1) the playhead `pos`, or null. `land` is where a
 * jump to an entry puts the playhead (a minute player lands on the bar after a mid-minute
 * entry, where its fill shows), so the entry just landed on is neither next nor previous.
 */
export function adjacentEntry(
  entries: readonly number[],
  pos: number,
  dir: 1 | -1,
  land: (entrySec: number) => number = (e) => e,
): number | null {
  if (dir > 0) return entries.find((e) => land(e) > pos) ?? null;
  for (let i = entries.length - 1; i >= 0; i--) if (land(entries[i]) < pos) return entries[i];
  return null;
}

/** A minute player's landing for a moment: the first bar open at or after it. */
export const ceilMinute = (sec: number) => Math.ceil(sec / 60) * 60;

/**
 * The group's P&L as of the playhead: only trades whose last fill has been played count, so
 * a result is never shown before it happened.
 */
export function groupPnl(
  positions: readonly Pick<DeskPosition, "trade_id" | "fills" | "pnl">[],
  ids: readonly number[],
  playSec: number,
): { pnl: number; closed: number; total: number } {
  let pnl = 0;
  let closed = 0;
  let total = 0;
  for (const p of positions) {
    if (!ids.includes(p.trade_id)) continue;
    total++;
    const h = holdOf(p);
    if (h && h.exit <= playSec && p.pnl != null) {
      pnl += p.pnl;
      closed++;
    }
  }
  return { pnl: Math.round(pnl * 100) / 100, closed, total };
}
