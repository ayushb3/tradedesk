/**
 * API client for the M6 training module endpoints.
 *
 * Covers:
 *   GET  /api/training/scenarios?active=true  → ScenarioPublic[]
 *   GET  /api/training/scenarios/{id}         → ScenarioPublic
 *   GET  /api/training/scenarios/{id}/session → DrillSession
 *   GET  /api/training/leaks                  → LeakReport
 *   GET  /api/training/progress               → ProgressReport
 *   POST /api/training/scenarios              → ScenarioPublic (authoring + synthetic)
 *   POST /api/training/attempts               → Attempt
 *   PATCH /api/training/attempts/{id}         → Attempt
 *   POST /api/training/attempts/{id}/finish   → AttemptFinishResponse
 *
 * Follows the same patterns as frontend/src/lib/api.ts (typed request helper,
 * ApiError for non-2xx) and frontend/src/lib/queries.ts (TanStack Query hooks).
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import type {
  Attempt,
  AttemptAction,
  AttemptFinishResponse,
  DrillSession,
  LeakReport,
  ProgressReport,
  ScenarioPublic,
} from "@/lib/trainingTypes";

const API_BASE = "/api";

async function trainingRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (cause) {
    throw new ApiError(
      "Could not reach the TradeDesk backend. Is it running on :9000?",
      0,
      cause instanceof Error ? cause.message : String(cause),
    );
  }

  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body?.detail;
    } catch {
      // not JSON; ignore
    }
    throw new ApiError(
      `Training request to ${path} failed (${res.status})`,
      res.status,
      detail,
    );
  }

  return (await res.json()) as T;
}

// --------------------------------------------------------------------------- //
// TanStack Query hooks
// --------------------------------------------------------------------------- //

/** Fetch active scenarios for the drill lobby. */
export function useActiveScenarios(): UseQueryResult<ScenarioPublic[]> {
  return useQuery<ScenarioPublic[]>({
    queryKey: ["training-scenarios", "active"],
    queryFn: () =>
      trainingRequest<ScenarioPublic[]>("/training/scenarios?active=true"),
  });
}

/** Fetch a single scenario by id (used by LeaksPage to resolve scenario titles). */
export function useScenario(
  scenarioId: number | null,
): UseQueryResult<ScenarioPublic> {
  return useQuery<ScenarioPublic>({
    queryKey: ["training-scenario", scenarioId],
    queryFn: () =>
      trainingRequest<ScenarioPublic>(`/training/scenarios/${scenarioId!}`),
    enabled: scenarioId !== null,
  });
}

/**
 * Fetch the replay payload for a given scenario.
 * `blind` defaults to the scenario's `blind_default` when omitted.
 */
export function useDrillSession(
  scenarioId: number | null,
  blind?: boolean,
): UseQueryResult<DrillSession> {
  return useQuery<DrillSession>({
    queryKey: ["drill-session", scenarioId, blind],
    queryFn: () => {
      const params = new URLSearchParams();
      if (blind !== undefined) params.set("blind", String(blind));
      const qs = params.toString();
      return trainingRequest<DrillSession>(
        `/training/scenarios/${scenarioId!}/session${qs ? `?${qs}` : ""}`,
      );
    },
    enabled: scenarioId !== null,
  });
}

/** Fetch detected leaks + prescribed drill playlists. */
export async function fetchLeaks(): Promise<LeakReport> {
  return trainingRequest<LeakReport>("/training/leaks");
}

/** TanStack Query hook for the leaks report. Refreshes every 60 s. */
export function useLeaks(): UseQueryResult<LeakReport> {
  return useQuery<LeakReport>({
    queryKey: ["training-leaks"],
    queryFn: fetchLeaks,
    staleTime: 60_000,
  });
}

// --------------------------------------------------------------------------- //
// Imperative API calls (used inside event handlers / state machine transitions)
// --------------------------------------------------------------------------- //

/** POST /api/training/attempts — open a new attempt for a scenario. */
export async function startAttempt(input: {
  scenario_id: number;
  blind?: boolean;
  mood_checkin?: string;
}): Promise<Attempt> {
  return trainingRequest<Attempt>("/training/attempts", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(input),
  });
}

/** PATCH /api/training/attempts/{id} — append bar-indexed action events. */
export async function appendActions(
  attemptId: number,
  actions: AttemptAction[],
): Promise<Attempt> {
  return trainingRequest<Attempt>(`/training/attempts/${attemptId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ actions }),
  });
}

/** POST /api/training/attempts/{id}/finish — the ONLY endpoint that returns the answer key. */
export async function finishAttempt(
  attemptId: number,
): Promise<AttemptFinishResponse> {
  return trainingRequest<AttemptFinishResponse>(
    `/training/attempts/${attemptId}/finish`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
}

// --------------------------------------------------------------------------- //
// Progress / gamification (task 6.4.2)
// --------------------------------------------------------------------------- //

/** Fetch the full progress report (mastery + streaks + due reviews + quests). */
export async function fetchProgress(): Promise<ProgressReport> {
  return trainingRequest<ProgressReport>("/training/progress");
}

/** TanStack Query hook for the progress report. Refreshes every 60 s. */
export function useProgress(): UseQueryResult<ProgressReport> {
  return useQuery<ProgressReport>({
    queryKey: ["training-progress"],
    queryFn: fetchProgress,
    staleTime: 60_000,
  });
}

/**
 * POST /api/training/scenarios — create a synthetic scenario for a due review.
 *
 * Used by the "Review now" button (Option B): the due-review card creates a fresh
 * synthetic scenario using the suggested_seed from the SM-2 scheduler, then
 * navigates straight into the drill player. This delivers "resurfaces with a
 * different seed" per PLAN §6 C4 acceptance criteria.
 *
 * The answer_key is intentionally minimal — the scorer will evaluate against the
 * scenario's checklist_template_id, not against a hard-coded correct action list.
 * `skip` is the safe default (do-nothing is a valid decision for any archetype).
 */
export async function createSyntheticScenario(input: {
  archetype: string;
  synth_seed: number;
  title: string;
}): Promise<ScenarioPublic> {
  const payload = {
    title: input.title,
    archetype: input.archetype,
    source_type: "synthetic",
    drill_type: "decision-point",
    synth_seed: input.synth_seed,
    start_bar: 0,
    blind_default: true,
    answer_key: {
      correct_actions: ["skip"],
      explanation: "Review drill — follow your rules at the decision bar.",
      management_notes: [],
    },
  };
  return trainingRequest<ScenarioPublic>("/training/scenarios", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  });
}
