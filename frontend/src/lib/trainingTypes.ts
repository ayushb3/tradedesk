/**
 * Training-module (M6) TypeScript types — mirrors backend models/training.py.
 *
 * These are shared between the drill player (task 6.1.2) and the scenario
 * authoring UI (task 6.1.3). If 6.1.3 needs to extend these, extend this file
 * additively — do NOT rename or remove existing exports.
 */

import type { Candle } from "@/lib/api";

// --------------------------------------------------------------------------- //
// Enums (string unions matching backend Enum values)
// --------------------------------------------------------------------------- //

export type ScenarioSource = "real" | "synthetic" | "debrief";

export type Archetype =
  | "gap-and-go"
  | "failed-breakout"
  | "chop"
  | "trend-day"
  | "custom";

export type DrillType =
  | "decision-point"
  | "management-only"
  | "do-nothing"
  | "spot-setup"
  | "drawdown-script";

export type AttemptActionType =
  | "enter_long"
  | "enter_short"
  | "exit"
  | "skip"
  | "wait"
  | "set_stop"
  | "move_stop";

export type MoodCheckin = "calm" | "rushed" | "frustrated";

export type DebriefVerdict =
  | "setup-failed"
  | "failed-the-setup"
  | "good-loss"
  | "bad-win"
  | "clean";

// --------------------------------------------------------------------------- //
// Scenarios
// --------------------------------------------------------------------------- //

export interface ScenarioAnnotation {
  bar: number;
  text: string;
  kind: string; // "note" | "confluence" | "warning"
}

/** AnswerKey — only ever returned from /finish, never in scenario responses. */
export interface AnswerKey {
  correct_actions: AttemptActionType[];
  explanation: string;
  management_notes: Record<string, unknown>[];
}

export interface ScenarioPublic {
  id: number;
  title: string;
  archetype: Archetype;
  source_type: ScenarioSource;
  drill_type: DrillType;
  symbol: string | null;       // null when blind
  session_date: string | null; // null when blind (YYYY-MM-DD)
  start_bar: number;
  decision_bar: number | null;
  end_bar: number | null;
  annotations: ScenarioAnnotation[];
  checklist_template_id: number | null;
  blind_default: boolean;
  active: boolean;
  created_at: string;
}

/** GET /api/training/scenarios/{id}/session — the drill replay payload. */
export interface DrillSession {
  scenario_id: number;
  drill_type: DrillType;
  blind: boolean;
  symbol: string | null;
  session_date: string | null;
  decision_bar: number | null;
  candles: Candle[];
  indicators: Record<string, (number | null)[]>;
  levels: Record<string, (number | null)[]>;
  annotations: ScenarioAnnotation[];
  meta: Record<string, unknown>;
}

// --------------------------------------------------------------------------- //
// Attempts
// --------------------------------------------------------------------------- //

export interface AttemptAction {
  bar: number;
  action: AttemptActionType;
  price: number | null;
  qty: number | null;
  ts: string; // ISO 8601 UTC
}

export interface AttemptCreate {
  scenario_id: number;
  mood_checkin?: MoodCheckin;
  blind?: boolean;
}

export interface Attempt {
  id: number;
  scenario_id: number;
  started_at: string;
  finished_at: string | null;
  mood_checkin: MoodCheckin | null;
  actions: AttemptAction[];
}

export interface AttemptFinishResponse {
  attempt: Attempt;
  process_score: number; // 0..1
  outcome_pnl: number | null;
  checklist: Record<string, unknown>[];
  answer_key: AnswerKey;
  verdict: string | null;
}

// --------------------------------------------------------------------------- //
// Leaks (task 6.3.4) — mirrors backend/app/analytics/leaks.py
// --------------------------------------------------------------------------- //

export interface LeakEvidence {
  trade_ids?: number[];
  attempt_ids?: number[];
  debrief_ids?: number[];
}

export interface Leak {
  name: string;
  severity: 1 | 2 | 3;
  description: string;
  evidence: LeakEvidence;
  prescribed_scenario_ids: number[];
}

export interface LeakReport {
  leaks: Leak[];
  generated_at: string;
}

// --------------------------------------------------------------------------- //
// Progress / gamification (task 6.4.2) — mirrors backend/app/analytics/mastery.py
// --------------------------------------------------------------------------- //

export interface ArchetypeMastery {
  archetype: string;
  level: number | null;       // 0-3, or null if unattempted
  level_label: string;        // learning | developing | proficient | mastered | unattempted
  rolling_mean_score: number | null;
  attempts_count: number;
}

export interface DueReview {
  archetype: string;
  last_score: number;
  failed_at: string;          // ISO-8601
  due_at: string;             // ISO-8601
  review_count: number;
  suggested_seed: number;     // deterministic fresh tape seed
}

export interface Quest {
  key: string;                // grade_all | tag_all | debrief_n
  label: string;
  target: number;
  progress: number;
  done: boolean;
}

export interface ProgressReport {
  mastery: ArchetypeMastery[];
  process_streak: number;           // current consecutive discipline streak
  longest_process_streak: number;
  due_reviews: DueReview[];
  quests: Quest[];
  generated_at: string;             // ISO-8601
}
