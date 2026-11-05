/**
 * Where things live under Learn (one home for drills, leaks, progress and lessons), plus the
 * redirects that keep the old /training/* URLs working. Pure: no React, no network.
 * Checked by learnRoutes.check.ts (npm run check:sim).
 */
import type { DrillBecause } from "./api";

export const LEARN_HOME = "/learn";
export const LEARN_LEAKS = "/learn/leaks";
export const LEARN_PROGRESS = "/learn/progress";
export const LEARN_SCENARIOS = "/learn/scenarios";
export const LEARN_SCENARIO_NEW = "/learn/scenarios/new";

/** The scenario drill player (Critique / Debrief / leak playlists). `search` keeps ?playlist, ?mood. */
export function practicePath(scenarioId: number | string, search = ""): string {
  const qs = search && !search.startsWith("?") ? `?${search}` : search;
  return `/learn/practice/${scenarioId}${qs}`;
}

/** A Learn topic view: its drills and lessons. */
export function topicPath(slug: string): string {
  return `/learn/topics/${encodeURIComponent(slug)}`;
}

/** The in-app lesson reader. */
export function lessonPath(readerId: number): string {
  return `/knowledge/lessons/${readerId}`;
}

export function tradePath(tradeId: number): string {
  return `/trades/${tradeId}`;
}

/** New home for an old /training/* URL (query string kept). Unknown paths land on Learn. */
export function trainingRedirect(pathname: string, search = ""): string {
  const p = pathname.replace(/\/+$/, "");
  const drill = /^\/training\/drills\/([^/]+)$/.exec(p);
  if (drill) return practicePath(drill[1], search);
  const fixed: Record<string, string> = {
    "/training/scenarios": LEARN_SCENARIOS,
    "/training/scenarios/new": LEARN_SCENARIO_NEW,
    "/training/drills": LEARN_HOME,
    "/training/leaks": LEARN_LEAKS,
    "/training/progress": LEARN_PROGRESS,
  };
  const to = fixed[p] ?? LEARN_HOME;
  return to === LEARN_HOME ? to : `${to}${search}`;
}

const RULE_TEXT: Record<string, string> = {
  exited_early: "you exited early",
  countertrend: "you traded against the trend",
  before_cutoff: "you traded after your cutoff",
};

/** What the drill practises, in words, for a `because.rule`. */
const RULE_FOCUS: Record<string, string> = {
  exited_early: "This drill pauses at the first target: hold a runner (Enter) or take it all (Wait)?",
  countertrend: "A setup that fights the trend: is it a trade?",
  before_cutoff: "A setup after your trading window: is it a trade?",
};

export function becauseRuleText(rule: string): string {
  return RULE_TEXT[rule] ?? rule.replace(/_/g, " ");
}

export function becauseFocus(b: DrillBecause | null | undefined): string | null {
  return b ? (RULE_FOCUS[b.rule] ?? null) : null;
}

/** Scenario drills made from your own trades (Critique / Debrief), newest first. */
export function tradeScenarios<
  T extends { id: number; source_type: string; source_trade_id?: number | null; created_at: string },
>(list: readonly T[]): T[] {
  return list
    .filter((s) => s.source_type === "debrief" || s.source_trade_id != null)
    .slice()
    .sort((a, b) => (a.created_at === b.created_at ? b.id - a.id : a.created_at < b.created_at ? 1 : -1));
}
