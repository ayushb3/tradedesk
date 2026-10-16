/**
 * Debrief-module (M6 C2) TypeScript types — mirrors backend models/training.py
 * for the debrief section (DebriefStep, InterviewTurn, Debrief, DebriefArtifacts).
 *
 * DebriefVerdict is defined in trainingTypes.ts and re-exported here so
 * consumers can import everything debrief-related from one place.
 */

import type { DebriefVerdict } from "@/lib/trainingTypes";

// Re-export so consumers can: import type { DebriefVerdict } from "@/lib/debriefTypes"
export type { DebriefVerdict };

// --------------------------------------------------------------------------- //
// Enums
// --------------------------------------------------------------------------- //

export type DebriefStep =
  | "setup"
  | "plan"
  | "feelings"
  | "verdict"
  | "done";

// --------------------------------------------------------------------------- //
// Interview protocol
// --------------------------------------------------------------------------- //

/**
 * One agent turn — identical shape from every LLM backend (form / claude-cli / api).
 * When `options` is non-null the frontend renders stepper chips instead of a
 * free-text textarea (but still allows textarea as a supplement).
 */
export interface InterviewTurn {
  step: DebriefStep;
  question: string | null;   // null when step === "done"
  options: string[] | null;  // form backend: suggested choices; null for LLM backends
  context_note: string | null; // drift flag, e.g. "Stated stop 12.40, exited 12.31"
  done: boolean;
}

// --------------------------------------------------------------------------- //
// Debrief entity
// --------------------------------------------------------------------------- //

export interface TranscriptEntry {
  role: "agent" | "user";
  step: DebriefStep;
  text: string;
  fields?: Record<string, unknown>;
}

export interface Debrief {
  id: number;
  trade_id: number;
  llm_backend: string; // "none" | "claude-cli" | "api"
  step: DebriefStep;
  transcript: TranscriptEntry[];
  plan: Record<string, unknown> | null;
  feelings: Array<{ feeling: string }>;
  verdict: DebriefVerdict | null;
  scenario_id: number | null;
  created_at: string; // ISO 8601
}

// --------------------------------------------------------------------------- //
// API payloads
// --------------------------------------------------------------------------- //

/** POST /api/debriefs — starts a new debrief session. */
export interface DebriefCreate {
  trade_id: number;
}

/** POST /api/debriefs response envelope. */
export interface DebriefStartResponse {
  debrief: Debrief;
  turn: InterviewTurn;
}

/** POST /api/debriefs/{id}/turn — the user's answer. */
export interface DebriefReply {
  text: string;
  /** Structured fields from stepper (option selection, etc). */
  fields?: Record<string, unknown>;
}

/** POST /api/debriefs/{id}/finish. */
export interface DebriefFinish {
  emit_scenario: boolean;
}

/** Response from /finish. */
export interface DebriefArtifacts {
  enrichment_saved: boolean;
  verdict: DebriefVerdict | null;
  scenario_id: number | null; // emitted drill, if emit_scenario was true
}
