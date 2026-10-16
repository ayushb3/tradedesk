/**
 * Training-module (M6) types and API client.
 *
 * Types mirror backend/app/models/training.py exactly — kept as string unions /
 * interfaces rather than TS enums to stay consistent with how api.ts handles
 * other domain types in this project.
 *
 * API surface per docs/PLAN-M6-TRAINING.md §4.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";

const API_BASE = "/api";

/** Thrown for any non-2xx response. Mirrors the pattern in api.ts. */
class TrainingApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "TrainingApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (cause) {
    throw new TrainingApiError(
      "Could not reach the TradeDesk backend.",
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
      // response wasn't JSON; ignore
    }
    throw new TrainingApiError(
      `Request to ${path} failed (${res.status})`,
      res.status,
      detail,
    );
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Enums as string-union types (matches api.ts conventions)
// ---------------------------------------------------------------------------

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

export const ATTEMPT_ACTION_LABELS: Record<AttemptActionType, string> = {
  enter_long: "Enter Long",
  enter_short: "Enter Short",
  exit: "Exit",
  skip: "Skip (no trade)",
  wait: "Wait (defer 1 bar)",
  set_stop: "Set Stop",
  move_stop: "Move Stop",
};

export const ALL_ATTEMPT_ACTION_TYPES: AttemptActionType[] = [
  "enter_long",
  "enter_short",
  "exit",
  "skip",
  "wait",
  "set_stop",
  "move_stop",
];

export const ARCHETYPE_LABELS: Record<Archetype, string> = {
  "gap-and-go": "Gap and Go",
  "failed-breakout": "Failed Breakout",
  chop: "Chop",
  "trend-day": "Trend Day",
  custom: "Custom",
};

export const DRILL_TYPE_LABELS: Record<DrillType, string> = {
  "decision-point": "Decision Point",
  "management-only": "Management Only",
  "do-nothing": "Do Nothing",
  "spot-setup": "Spot the Setup",
  "drawdown-script": "Drawdown Script",
};

export const SOURCE_TYPE_LABELS: Record<ScenarioSource, string> = {
  real: "Real Tape",
  synthetic: "Synthetic",
  debrief: "From Debrief",
};

// ---------------------------------------------------------------------------
// Domain shapes (mirror ScenarioAnnotation, AnswerKey, ScenarioCreate, ScenarioPublic)
// ---------------------------------------------------------------------------

export interface ScenarioAnnotation {
  bar: number;
  text: string;
  kind: "note" | "confluence" | "warning";
}

export interface AnswerKey {
  correct_actions: AttemptActionType[];
  explanation: string;
  management_notes: { bar: number; action: string; note: string }[];
}

/**
 * POST /api/training/scenarios request body.
 * Mirrors ScenarioCreate in backend/app/models/training.py.
 */
export interface ScenarioCreateInput {
  title: string;
  archetype: Archetype;
  source_type: ScenarioSource;
  drill_type: DrillType;
  symbol?: string | null;
  session_date?: string | null; // YYYY-MM-DD
  synth_seed?: number | null;
  synth_params?: Record<string, unknown> | null;
  start_bar: number;
  decision_bar?: number | null;
  end_bar?: number | null;
  annotations: ScenarioAnnotation[];
  answer_key: AnswerKey;
  checklist_template_id?: number | null;
  source_trade_id?: number | null;
  blind_default: boolean;
}

/**
 * GET /api/training/scenarios response shape (no answer_key by contract).
 * Mirrors ScenarioPublic in backend/app/models/training.py.
 */
export interface ScenarioPublic {
  id: number;
  title: string;
  archetype: Archetype;
  source_type: ScenarioSource;
  drill_type: DrillType;
  symbol: string | null;
  session_date: string | null;
  start_bar: number;
  decision_bar: number | null;
  end_bar: number | null;
  annotations: ScenarioAnnotation[];
  checklist_template_id: number | null;
  blind_default: boolean;
  active: boolean;
  created_at: string; // ISO 8601
}

export interface ScenarioListParams {
  archetype?: Archetype;
  drill_type?: DrillType;
  active?: boolean;
}

// ---------------------------------------------------------------------------
// Raw API functions (no React hooks)
// ---------------------------------------------------------------------------

function toQuery(
  params: Record<string, string | number | boolean | undefined>,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    qs.set(k, String(v));
  }
  return qs.toString();
}

export async function createScenario(
  payload: ScenarioCreateInput,
): Promise<ScenarioPublic> {
  return request<ScenarioPublic>("/training/scenarios", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchScenarios(
  params?: ScenarioListParams,
): Promise<ScenarioPublic[]> {
  const qs = params ? toQuery({ ...params }) : "";
  return request<ScenarioPublic[]>(`/training/scenarios${qs ? `?${qs}` : ""}`);
}

export async function fetchScenario(id: number): Promise<ScenarioPublic> {
  return request<ScenarioPublic>(`/training/scenarios/${id}`);
}

// ---------------------------------------------------------------------------
// TanStack Query hooks
// ---------------------------------------------------------------------------

const SCENARIO_KEYS = {
  all: ["scenarios"] as const,
  list: (params?: ScenarioListParams) =>
    ["scenarios", "list", params] as const,
  detail: (id: number) => ["scenarios", "detail", id] as const,
};

export function useScenarios(
  params?: ScenarioListParams,
): UseQueryResult<ScenarioPublic[]> {
  return useQuery<ScenarioPublic[]>({
    queryKey: SCENARIO_KEYS.list(params),
    queryFn: () => fetchScenarios(params),
  });
}

export function useScenario(id: number): UseQueryResult<ScenarioPublic> {
  return useQuery<ScenarioPublic>({
    queryKey: SCENARIO_KEYS.detail(id),
    queryFn: () => fetchScenario(id),
    enabled: id > 0,
  });
}

export function useCreateScenario() {
  const qc = useQueryClient();
  return useMutation<ScenarioPublic, TrainingApiError, ScenarioCreateInput>({
    mutationFn: createScenario,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCENARIO_KEYS.all });
    },
  });
}
