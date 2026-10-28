/**
 * Pure logic for the Learn drills: the answer step machine, client-side no-leak
 * guard, PT formatting and a tiny markdown parser. No React, no network.
 * Checked by learn.check.ts (npm run check:sim).
 */
import type { Candle, DrillPublic, LearnNotConfigured, LearnStepName } from "./api";
import { DISPLAY_TZ, DISPLAY_TZ_LABEL } from "./format";

// ---- not-configured responses --------------------------------------------------

/** Every /learn endpoint answers `{configured:false}` when the learn folder is unset. */
export function isNotConfigured(x: unknown): x is LearnNotConfigured {
  return (
    typeof x === "object" && x !== null && !Array.isArray(x) && (x as { configured?: unknown }).configured === false
  );
}

// ---- the answer draft + step machine --------------------------------------------

export type EnterChoice = "enter" | "wait";
export type Side = "long" | "short";

export interface Draft {
  level: number | null;
  enter: EnterChoice | null;
  side: Side | null;
  stop: number | null;
  target: number | null;
}

export const EMPTY_DRAFT: Draft = { level: null, enter: null, side: null, stop: null, target: null };

/** What the chart should do with the next click. */
export type ChartPick = "level" | "stop" | "target" | null;

/**
 * The steps to answer, in the drill's order. Level and Enter/Wait always apply; stop and target
 * only once the answer is "enter" (a "wait" skips them). Before the choice is made, they are held back.
 */
export function requiredSteps(questionSteps: readonly LearnStepName[], enter: EnterChoice | null): LearnStepName[] {
  return questionSteps.filter((s) => (s === "stop" || s === "target" ? enter === "enter" : true));
}

/** Is this step finished? The enter step also needs a side when the choice is "enter". */
export function stepDone(step: LearnStepName, d: Draft): boolean {
  switch (step) {
    case "level":
      return d.level !== null;
    case "enter":
      return d.enter === "wait" || (d.enter === "enter" && d.side !== null);
    case "stop":
      return d.stop !== null;
    case "target":
      return d.target !== null;
  }
}

/** The first unfinished required step, or null when the answer is complete. */
export function currentStep(questionSteps: readonly LearnStepName[], d: Draft): LearnStepName | null {
  for (const s of requiredSteps(questionSteps, d.enter)) if (!stepDone(s, d)) return s;
  return null;
}

export function canSubmit(questionSteps: readonly LearnStepName[], d: Draft): boolean {
  return currentStep(questionSteps, d) === null && requiredSteps(questionSteps, d.enter).length > 0;
}

/** Which price a chart click sets right now. */
export function chartPickFor(active: LearnStepName | null): ChartPick {
  return active === "level" || active === "stop" || active === "target" ? active : null;
}

/** Choosing "wait" clears the entry-only fields so they can never be sent. */
export function setEnter(d: Draft, enter: EnterChoice): Draft {
  return enter === "wait" ? { ...d, enter, side: null, stop: null, target: null } : { ...d, enter };
}

/** Body for POST /answer. */
export function toAnswerRequest(d: Draft) {
  const enter: EnterChoice = d.enter ?? "wait";
  return {
    level: d.level,
    enter,
    side: enter === "enter" ? d.side : null,
    stop: enter === "enter" ? d.stop : null,
    target: enter === "enter" ? d.target : null,
  };
}

/** Parse a typed price; positive finite numbers only. */
export function parsePrice(text: string): number | null {
  const t = text.trim().replace(/,/g, "");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export const roundPrice = (n: number): number => Math.round(n * 100) / 100;

// ---- bars: the no-leak guard and playback ---------------------------------------

/**
 * Never show a bar at or after `pause_at` before the answer, even if the server sent one.
 * After the answer the full set is shown.
 */
export function guardBars(candles: Candle[], pauseAt: string, answered: boolean): Candle[] {
  if (answered) return candles;
  const cut = Date.parse(pauseAt);
  return candles.filter((c) => Date.parse(c.ts) < cut);
}

/** Playback starts this many minutes before the pause; the view shows this many minutes. */
export const LEAD_MINUTES = 30;
export const VIEW_MINUTES = 60;
/** Playback speeds, bars per second. */
export const PLAY_SPEEDS = [1, 2, 5, 10] as const;

/** Bars covering `minutes` at a timeframe like "1m" or "5m" (unknown: 1-minute bars). */
export function barsFor(minutes: number, timeframe: string): number {
  const m = /^(\d+)m$/.exec(timeframe.trim());
  const per = m ? Math.max(1, Number(m[1])) : 1;
  return Math.max(1, Math.ceil(minutes / per));
}

// ---- timeframes: client-side bars from the 1m tape, KCU defaults, hourly levels ----------

/** Selectable bar sizes, minutes. */
export const TIMEFRAMES = [1, 2, 5, 10, 15, 60] as const;
export type TfChoice = "auto" | (typeof TIMEFRAMES)[number];

export const tfLabel = (m: number): string => (m >= 60 ? `${m / 60}h` : `${m}m`);

/**
 * KCU's chart progression, by minutes since the open: the first 30 minutes on the 2m,
 * the next hour on the 5m, then the 10m.
 */
export function kcuTimeframe(minutesSinceOpen: number): 2 | 5 | 10 {
  if (minutesSinceOpen < 30) return 2;
  if (minutesSinceOpen < 90) return 5;
  return 10;
}

/** Start of the `stepSec` bucket holding `sec`, aligned to the session open (`anchorSec`). */
export function bucketStart(sec: number, stepSec: number, anchorSec: number): number {
  return anchorSec + Math.floor((sec - anchorSec) / stepSec) * stepSec;
}

/**
 * Bars of `stepMin` minutes built from 1m bars, buckets aligned to the open (`anchorSec`, so the
 * hourly bar runs 6:30-7:30 PT like TradingView's session-aligned 1h). Pass only revealed bars:
 * the last bucket is then the bar still forming at the playhead, never one that peeks ahead.
 */
export function aggregateBars(candles: readonly Candle[], stepMin: number, anchorSec: number): Candle[] {
  if (stepMin <= 1) return candles.slice();
  const step = stepMin * 60;
  const out: Candle[] = [];
  let cur: Candle | null = null;
  let curStart = NaN;
  for (const c of candles) {
    const b = bucketStart(Date.parse(c.ts) / 1000, step, anchorSec);
    if (cur && b === curStart) {
      cur.high = Math.max(cur.high, c.high);
      cur.low = Math.min(cur.low, c.low);
      cur.close = c.close;
      cur.volume += c.volume;
      continue;
    }
    cur = { ...c, ts: new Date(b * 1000).toISOString(), timeframe: tfLabel(stepMin) };
    curStart = b;
    out.push(cur);
  }
  return out;
}

/**
 * High and low of every hourly bar (open-aligned) that has closed by `playheadSec`; the hour
 * still forming is left out so a level never moves under you.
 */
export function hourlyLevels(
  candles: readonly Candle[],
  anchorSec: number,
  playheadSec: number,
): { label: string; price: number }[] {
  const out: { label: string; price: number }[] = [];
  for (const h of aggregateBars(candles, 60, anchorSec)) {
    const start = Date.parse(h.ts) / 1000;
    if (start + 3600 > playheadSec + 60) continue; // not closed yet
    const at = ptTime(h.ts).replace(/ PT$/, "");
    out.push({ label: `1h H ${at}`, price: h.high }, { label: `1h L ${at}`, price: h.low });
  }
  return out;
}

/** Opening range length, minutes (the course's 15-minute ORB). */
export const ORB_MINUTES = 15;

/** The opening range high and low, once its last bar has closed by `playheadSec`; else nothing. */
export function openingRange(
  candles: readonly Candle[],
  anchorSec: number,
  playheadSec: number,
  minutes: number = ORB_MINUTES,
): { label: string; price: number }[] {
  const end = anchorSec + minutes * 60;
  if (playheadSec + 60 < end) return [];
  const inRange = candles.filter((c) => {
    const t = Date.parse(c.ts) / 1000;
    return t >= anchorSec && t < end;
  });
  if (!inRange.length) return [];
  return [
    { label: `ORB${minutes} H`, price: Math.max(...inRange.map((c) => c.high)) },
    { label: `ORB${minutes} L`, price: Math.min(...inRange.map((c) => c.low)) },
  ];
}

/**
 * Session VWAP from the open, causal: keyed by bar open (of `stepMin` bars), each value is the
 * VWAP as of the last 1m bar passed in that bucket. Typical price (H+L+C)/3 weighted by volume.
 */
export function sessionVwap(candles: readonly Candle[], stepMin: number, anchorSec: number): Map<number, number> {
  const out = new Map<number, number>();
  let pv = 0;
  let vol = 0;
  for (const c of candles) {
    const t = Date.parse(c.ts) / 1000;
    if (t < anchorSec) continue;
    const v = c.volume > 0 ? c.volume : 0;
    pv += ((c.high + c.low + c.close) / 3) * v;
    vol += v;
    if (vol > 0) out.set(bucketStart(t, stepMin * 60, anchorSec), pv / vol);
  }
  return out;
}

/** First playhead index for a fresh drill: `lead` bars before the last pre-pause bar. */
export function startIndex(prePauseBars: number, lead: number): number {
  return Math.max(0, prePauseBars - 1 - lead);
}

const MISSING: Record<LearnStepName, string> = {
  level: "set the level",
  enter: "pick Enter or Wait",
  stop: "set the stop",
  target: "set the target",
};

/** Why Submit is disabled, e.g. "To submit: pick Long or Short." */
export function missingText(questionSteps: readonly LearnStepName[], d: Draft): string {
  const todo = requiredSteps(questionSteps, d.enter)
    .filter((s) => !stepDone(s, d))
    .map((s) => (s === "enter" && d.enter === "enter" ? "pick Long or Short" : MISSING[s]));
  return todo.length ? `To submit: ${todo.join(", ")}.` : "";
}

/** The next playhead index after one step, clamped to the last bar. */
export function stepIndex(idx: number, total: number): number {
  return Math.max(0, Math.min(total - 1, idx + 1));
}

// ---- PT formatting ----------------------------------------------------------------

const ptClock = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "06:42 PT" from an ISO UTC instant. */
export function ptTime(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? `${ptClock.format(ms)} ${DISPLAY_TZ_LABEL}` : "";
}

/** "06:30 to 07:12 PT" for the replay window of a drill. */
export function ptWindow(fromIso: string, toIso: string): string {
  const a = ptTime(fromIso).replace(` ${DISPLAY_TZ_LABEL}`, "");
  return `${a} to ${ptTime(toIso)}`;
}

/** "Oct 14" from a YYYY-MM-DD calendar date (no timezone shift: it is a session date). */
export function shortDate(ymd: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd);
  if (!m) return ymd;
  return `${MONTHS[Number(m[2]) - 1] ?? m[2]} ${Number(m[3])}`;
}

/** "Session Oct 14 PT · pauses at 07:12 PT" for a drill card. */
export function drillWhen(d: Pick<DrillPublic, "date" | "pause_at">): string {
  return `Session ${shortDate(d.date)} PT · pauses at ${ptTime(d.pause_at)}`;
}

/** "kebab-case-topic" to "Kebab case topic" when no title is available. */
export function topicName(slug: string): string {
  const t = slug.replace(/[-_]+/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : slug;
}

export function scoreText(score: number | null | undefined): string {
  return score == null ? "n/a" : `${Math.round(score)}`;
}

/** The next undone drill after `currentId` (wrapping to earlier ones), or null. */
export function nextUndone(drills: readonly DrillPublic[], currentId: string): DrillPublic | null {
  const i = drills.findIndex((d) => d.id === currentId);
  const ordered = i < 0 ? [...drills] : [...drills.slice(i + 1), ...drills.slice(0, i)];
  return ordered.find((d) => d.status !== "done" && d.id !== currentId) ?? null;
}

export function fmtPrice(v: number | string | null | undefined): string {
  if (v == null) return "none";
  return typeof v === "number" ? v.toFixed(2) : String(v);
}

// ---- tiny markdown (headings, bullets, paragraphs; bold/code inline) -------------

export type MdBlock =
  | { kind: "h"; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul"; items: string[] };

export function parseMarkdown(md: string): MdBlock[] {
  const out: MdBlock[] = [];
  let para: string[] = [];
  let list: string[] | null = null;
  const flushPara = () => {
    if (para.length) out.push({ kind: "p", text: para.join(" ") });
    para = [];
  };
  const flushList = () => {
    if (list) out.push({ kind: "ul", items: list });
    list = null;
  };
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trim();
    const h = /^#{1,6}\s+(.*)$/.exec(line);
    const li = /^[-*]\s+(.*)$/.exec(line) ?? /^\d+[.)]\s+(.*)$/.exec(line);
    if (!line) {
      flushPara();
      flushList();
    } else if (h) {
      flushPara();
      flushList();
      out.push({ kind: "h", text: h[1] });
    } else if (li) {
      flushPara();
      (list ??= []).push(li[1]);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return out;
}

export type MdSpan = { text: string; bold?: boolean; code?: boolean };

/** Split inline `code` and **bold**; everything else is plain text (never HTML). */
export function parseInline(text: string): MdSpan[] {
  const out: MdSpan[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ text: text.slice(last, m.index) });
    const tok = m[0];
    out.push(tok.startsWith("`") ? { text: tok.slice(1, -1), code: true } : { text: tok.slice(2, -2), bold: true });
    last = m.index + tok.length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}

// Candle is re-exported so callers of guardBars need one import.
export type { Candle };
