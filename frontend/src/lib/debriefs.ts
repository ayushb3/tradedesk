/**
 * API client and TanStack Query hooks for the M6 debrief interview.
 *
 * Endpoints:
 *   POST /api/debriefs                       → DebriefStartResponse
 *   POST /api/debriefs/{id}/turn             → InterviewTurn
 *   POST /api/debriefs/{id}/finish           → DebriefArtifacts
 *   GET  /api/debriefs?trade_id=             → Debrief[]
 *
 * Follows the same patterns as frontend/src/lib/drills.ts (typed request
 * helper, ApiError, TanStack Query hooks for list/read, imperative calls
 * for state-machine transitions).
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import type {
  Debrief,
  DebriefArtifacts,
  DebriefCreate,
  DebriefFinish,
  DebriefReply,
  DebriefStartResponse,
  InterviewTurn,
} from "@/lib/debriefTypes";

const API_BASE = "/api";

async function debriefRequest<T>(path: string, init?: RequestInit): Promise<T> {
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
      `Debrief request to ${path} failed (${res.status})`,
      res.status,
      detail,
    );
  }

  return (await res.json()) as T;
}

// --------------------------------------------------------------------------- //
// TanStack Query hooks
// --------------------------------------------------------------------------- //

/**
 * Fetch all debriefs for a given trade.
 * Used by the trade cockpit to show the "Debrief" / "View debrief" button.
 * staleTime = 30 s — low overhead for a per-trade view that opens infrequently.
 */
export function useDebriefsForTrade(
  tradeId: number | null,
): UseQueryResult<Debrief[]> {
  return useQuery<Debrief[]>({
    queryKey: ["debriefs", "trade", tradeId],
    queryFn: () =>
      debriefRequest<Debrief[]>(`/debriefs?trade_id=${tradeId!}`),
    enabled: tradeId !== null,
    staleTime: 30_000,
  });
}

// --------------------------------------------------------------------------- //
// Imperative API calls (used inside state-machine transitions)
// --------------------------------------------------------------------------- //

/** POST /api/debriefs — create a new debrief and get the first agent turn. */
export async function startDebrief(
  payload: DebriefCreate,
): Promise<DebriefStartResponse> {
  return debriefRequest<DebriefStartResponse>("/debriefs", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
}

/** POST /api/debriefs/{id}/turn — submit a user reply, get the next agent turn. */
export async function submitTurn(
  debriefId: number,
  reply: DebriefReply,
): Promise<InterviewTurn> {
  return debriefRequest<InterviewTurn>(`/debriefs/${debriefId}/turn`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(reply),
  });
}

/** POST /api/debriefs/{id}/finish — persist artifacts; optionally emit a drill. */
export async function finishDebrief(
  debriefId: number,
  finish: DebriefFinish,
): Promise<DebriefArtifacts> {
  return debriefRequest<DebriefArtifacts>(`/debriefs/${debriefId}/finish`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(finish),
  });
}
