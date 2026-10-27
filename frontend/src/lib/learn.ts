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
