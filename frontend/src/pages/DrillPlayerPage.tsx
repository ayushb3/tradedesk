/**
 * DrillPlayerPage — /training/drills/:scenarioId
 *
 * State machine:
 *   loading  → ready (session loaded, no attempt yet)
 *   ready    → playing (attempt created, tape auto-advances)
 *   playing  → decision (tape reaches decision_bar, paused)
 *   decision → resolving (user picks Long/Short/Skip)
 *            → decision (user picks Wait — advance one bar, re-pause)
 *   resolving → finished (tape plays out, /finish called)
 *   finished → lobby / next-in-playlist / "take again"
 *
 * Mood threading (Option A — URL query param):
 *   The lobby injects ?mood=calm|rushed|frustrated before navigating here.
 *   The player reads it from useSearchParams and passes it to the initial
 *   POST /attempts.  No context, no local storage — the URL is the source of
 *   truth, so refresh and deep-link both work correctly.
 *
 * Playlist mechanic (URL-driven):
 *   ?playlist=2,3,4&mood=calm
 *   The current scenario is the :scenarioId path param.  `playlist` holds the
 *   *remaining* ids (not yet played).  On finish the player either:
 *     - navigates to /training/drills/<next>?playlist=<rest>&mood=<mood>  (more drills left)
 *     - navigates to /training/leaks with a success toast                 (playlist complete)
 *   All state rides in the URL; bailing mid-playlist is harmless.
 *
 * Reuses ReplayChart for tape rendering. The chart receives a `DrillSession`
 * adapted to the `ReplaySession` shape that ReplayChart expects.
 */

import { useCallback, useEffect, useReducer, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { AlertCircle, ArrowLeft, List, ServerCrash } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ReplayChart } from "@/components/replay/ReplayChart";
import { ActionButtons, type DrillChoice } from "@/components/drills/ActionButtons";
import { AnswerKeyCard } from "@/components/drills/AnswerKeyCard";
import { ScoreBadge } from "@/components/drills/ScoreBadge";
import { ApiError } from "@/lib/api";
import {
  appendActions,
  finishAttempt,
  startAttempt,
  useDrillSession,
} from "@/lib/drills";
import type {
  AttemptAction,
  AttemptFinishResponse,
  DrillSession,
  MoodCheckin,
} from "@/lib/trainingTypes";

// --------------------------------------------------------------------------- //
// Playlist URL helpers
// --------------------------------------------------------------------------- //

/** Parse the `playlist` query param into a number array. Empty string → []. */
function parsePlaylist(raw: string | null): number[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => parseInt(s.trim(), 10))
    .filter((n) => !isNaN(n) && n > 0);
}

/** Serialise remaining IDs back to a comma string (or omit the param if empty). */
function serializePlaylist(ids: number[]): string {
  return ids.join(",");
}

// --------------------------------------------------------------------------- //
// Adapters: DrillSession → ReplaySession (the shape ReplayChart expects)
// --------------------------------------------------------------------------- //

function toReplaySessionShape(session: DrillSession) {
  return {
    symbol: session.symbol ?? "BLIND",
    session_date: session.session_date ?? "",
    candles: session.candles,
    indicators: session.indicators as Record<string, (number | null)[]>,
    levels: session.levels as Record<string, (number | null)[]>,
    meta: {
      or_minutes: (session.meta.or_minutes as number | undefined) ?? 30,
      tz: (session.meta.tz as string | undefined) ?? "America/New_York",
    },
  };
}

// --------------------------------------------------------------------------- //
// State machine
// --------------------------------------------------------------------------- //

type Phase =
  | "loading"
  | "ready"
  | "playing"
  | "decision"
  | "resolving"
  | "finished";

interface DrillState {
  phase: Phase;
  currentBar: number;
  attemptId: number | null;
  userActions: AttemptAction[];
  finishResult: AttemptFinishResponse | null;
  error: string | null;
}

type DrillAction =
  | { type: "SESSION_LOADED" }
  | { type: "ATTEMPT_STARTED"; attemptId: number }
  | { type: "TICK" }
  | { type: "REACH_DECISION_BAR" }
  | { type: "USER_CHOSE"; action: AttemptAction; finalDecision: boolean }
  | { type: "WAIT_TICK" }
  | { type: "FINISH_READY"; result: AttemptFinishResponse }
  | { type: "ERROR"; message: string }
  | { type: "RESET" };

function reducer(state: DrillState, action: DrillAction): DrillState {
  switch (action.type) {
    case "SESSION_LOADED":
      return { ...state, phase: "ready" };
    case "ATTEMPT_STARTED":
      return { ...state, phase: "playing", attemptId: action.attemptId, currentBar: 0 };
    case "TICK":
      return { ...state, currentBar: state.currentBar + 1 };
    case "REACH_DECISION_BAR":
      return { ...state, phase: "decision" };
    case "USER_CHOSE":
      return {
        ...state,
        phase: action.finalDecision ? "resolving" : state.phase,
        userActions: [...state.userActions, action.action],
      };
    case "WAIT_TICK":
      return { ...state, currentBar: state.currentBar + 1 };
    case "FINISH_READY":
      return { ...state, phase: "finished", finishResult: action.result };
    case "ERROR":
      return { ...state, error: action.message };
    case "RESET":
      return {
        phase: "loading",
        currentBar: 0,
        attemptId: null,
        userActions: [],
        finishResult: null,
        error: null,
      };
    default:
      return state;
  }
}

const initialState: DrillState = {
  phase: "loading",
  currentBar: 0,
  attemptId: null,
  userActions: [],
  finishResult: null,
  error: null,
};

// --------------------------------------------------------------------------- //
// Playback hook: auto-advances bars at a given interval
// --------------------------------------------------------------------------- //

const PLAY_INTERVAL_MS = 500;
const RESOLVE_INTERVAL_MS = 200;

function useBarTimer(active: boolean, intervalMs: number, onTick: () => void) {
  const onTickRef = useRef(onTick);
  onTickRef.current = onTick;

  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => onTickRef.current(), intervalMs);
    return () => window.clearInterval(id);
  }, [active, intervalMs]);
}

// --------------------------------------------------------------------------- //
// Main page
// --------------------------------------------------------------------------- //

export function DrillPlayerPage() {
  const { scenarioId: scenarioIdStr } = useParams<{ scenarioId: string }>();
  const scenarioId = scenarioIdStr ? parseInt(scenarioIdStr, 10) : null;
  const navigate = useNavigate();
  const { toast } = useToast();

  // --- URL params: mood + playlist -------------------------------------------
  const [searchParams] = useSearchParams();
  const moodParam = searchParams.get("mood") as MoodCheckin | null;
  const playlistParam = searchParams.get("playlist");
  const remainingIds = parsePlaylist(playlistParam);

  // Total playlist size = already-played (we don't track) + current + remaining.
  // We store the *original* total as a data attr on the link: not needed here.
  // For the "X of N" indicator we count: remaining + 1 (current).
  // The leaks page builds the URL as ?playlist=id2,id3 with the current id as
  // the :scenarioId param.  So totalInPlaylist = remainingIds.length + 1 when
  // first starting.  We don't track played count in URL (keeps it simple), so
  // we show "Playlist · N remaining" instead of "2 of 3".
  const isInPlaylist = playlistParam !== null;

  // ---------------------------------------------------------------------------

  const [state, dispatch] = useReducer(reducer, initialState);
  const { phase, currentBar, attemptId, userActions, finishResult, error } = state;

  const {
    data: session,
    isLoading: sessionLoading,
    isError: sessionError,
    error: sessionErrorObj,
  } = useDrillSession(scenarioId);

  useEffect(() => {
    if (session && phase === "loading") {
      dispatch({ type: "SESSION_LOADED" });
    }
  }, [session, phase]);

  const decisionBar = session?.decision_bar ?? null;
  const totalBars = session?.candles.length ?? 0;
  const endBar = totalBars > 0 ? totalBars - 1 : 0;

  const startBar = (session?.meta?.start_bar as number | undefined) ?? 0;
  const toGlobalBar = (localBar: number) => startBar + localBar;

  // --- playing phase: auto-advance bars until decision_bar -------------------
  const handlePlayTick = useCallback(() => {
    if (!session) return;
    const nextBar = currentBar + 1;
    if (decisionBar !== null && nextBar >= decisionBar) {
      dispatch({ type: "TICK" });
      dispatch({ type: "REACH_DECISION_BAR" });
    } else if (nextBar >= endBar) {
      dispatch({ type: "TICK" });
      dispatch({ type: "REACH_DECISION_BAR" });
    } else {
      dispatch({ type: "TICK" });
    }
  }, [currentBar, decisionBar, endBar, session]);

  useBarTimer(phase === "playing", PLAY_INTERVAL_MS, handlePlayTick);

  // --- resolving phase: play out remaining bars then finish ------------------
  const isFinishing = useRef(false);

  const handleResolveTick = useCallback(() => {
    if (currentBar < endBar) {
      dispatch({ type: "TICK" });
    } else if (!isFinishing.current && attemptId !== null) {
      isFinishing.current = true;
      void finishAttempt(attemptId)
        .then((result) => dispatch({ type: "FINISH_READY", result }))
        .catch((err) => {
          isFinishing.current = false;
          dispatch({
            type: "ERROR",
            message:
              err instanceof ApiError
                ? (err.detail ?? err.message)
                : String(err),
          });
        });
    }
  }, [currentBar, endBar, attemptId]);

  useBarTimer(phase === "resolving", RESOLVE_INTERVAL_MS, handleResolveTick);

  useEffect(() => {
    if (phase !== "resolving") isFinishing.current = false;
  }, [phase]);

  // --- confirm start: include mood from URL param ----------------------------
  const handleConfirmStart = useCallback(async () => {
    if (!session || !scenarioId) return;
    try {
      const attempt = await startAttempt({
        scenario_id: scenarioId,
        blind: session.blind,
        // Thread mood from the URL query param into the attempt record.
        // mood_checkin is null when the user skipped the check-in (no ?mood=).
        ...(moodParam ? { mood_checkin: moodParam } : {}),
      });
      dispatch({ type: "ATTEMPT_STARTED", attemptId: attempt.id });
    } catch (err) {
      dispatch({
        type: "ERROR",
        message:
          err instanceof ApiError ? (err.detail ?? err.message) : String(err),
      });
    }
  }, [session, scenarioId, moodParam]);

  // --- user decision ---------------------------------------------------------
  const handleChoice = useCallback(
    async (choice: DrillChoice) => {
      if (!attemptId || !session) return;

      const now = new Date().toISOString();
      const action: AttemptAction = {
        bar: toGlobalBar(currentBar),
        action:
          choice === "enter_long"
            ? "enter_long"
            : choice === "enter_short"
              ? "enter_short"
              : choice === "skip"
                ? "skip"
                : "wait",
        price: null,
        qty: null,
        ts: now,
      };

      if (choice === "wait") {
        try {
          await appendActions(attemptId, [action]);
        } catch {
          // Non-fatal
        }
        dispatch({ type: "USER_CHOSE", action, finalDecision: false });

        if (currentBar >= endBar) {
          const skipAction: AttemptAction = {
            bar: toGlobalBar(currentBar),
            action: "skip",
            price: null,
            qty: null,
            ts: new Date().toISOString(),
          };
          try {
            await appendActions(attemptId, [skipAction]);
          } catch {
            // best-effort
          }
          dispatch({ type: "USER_CHOSE", action: skipAction, finalDecision: true });
        } else {
          dispatch({ type: "WAIT_TICK" });
        }
      } else {
        try {
          await appendActions(attemptId, [action]);
        } catch (err) {
          dispatch({
            type: "ERROR",
            message:
              err instanceof ApiError ? (err.detail ?? err.message) : String(err),
          });
          return;
        }
        dispatch({ type: "USER_CHOSE", action, finalDecision: true });
      }
    },
    [attemptId, currentBar, endBar, session],
  );

  // --- post-finish navigation: playlist-aware --------------------------------
  const handleTakeAnother = useCallback(() => {
    if (isInPlaylist && remainingIds.length > 0) {
      // Advance to the next drill in the playlist.
      const [nextId, ...rest] = remainingIds;
      const params = new URLSearchParams();
      if (rest.length > 0) params.set("playlist", serializePlaylist(rest));
      if (moodParam) params.set("mood", moodParam);
      const qs = params.toString();
      navigate(`/training/drills/${nextId}${qs ? `?${qs}` : ""}`);
    } else if (isInPlaylist && remainingIds.length === 0) {
      // Playlist complete — go to leaks page with a toast.
      navigate("/training/leaks");
      // Toast fires after navigation; slight delay avoids race with page mount.
      window.setTimeout(() => {
        toast({
          title: "Playlist complete",
          description: "All prescribed drills done. Check for new leaks.",
          variant: "success",
        });
      }, 150);
    } else {
      // Normal single-drill flow — lobby.
      navigate("/training/drills");
    }
  }, [isInPlaylist, remainingIds, moodParam, navigate, toast]);

  const handleReset = useCallback(() => {
    dispatch({ type: "RESET" });
  }, []);

  // --- render ----------------------------------------------------------------
  if (!scenarioId) {
    return <PlayerError message="No scenario ID in URL." />;
  }

  if (sessionLoading || phase === "loading") {
    return <PlayerSkeleton />;
  }

  if (sessionError || !session) {
    return <PlayerError error={sessionErrorObj} />;
  }

  if (error) {
    return <PlayerError message={error} onBack={() => dispatch({ type: "RESET" })} />;
  }

  const replaySession = toReplaySessionShape(session);

  if (phase === "ready") {
    return (
      <ReadyScreen
        session={session}
        onStart={handleConfirmStart}
        onBack={() => navigate("/training/drills")}
        playlistRemaining={isInPlaylist ? remainingIds.length : null}
      />
    );
  }

  if (phase === "finished" && finishResult) {
    return (
      <FinishedScreen
        session={session}
        result={finishResult}
        userActions={userActions}
        onTakeAnother={handleTakeAnother}
        onTakeAgain={handleReset}
        playlistRemaining={isInPlaylist ? remainingIds.length : null}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PlayerHeader
        session={session}
        phase={phase}
        currentBar={currentBar}
        totalBars={totalBars}
        decisionBar={decisionBar}
        playlistRemaining={isInPlaylist ? remainingIds.length : null}
      />

      <div className="min-h-0 flex-1">
        <ReplayChart session={replaySession} currentIndex={currentBar} trades={[]} />
      </div>

      {phase === "decision" && <DecisionBar onChoice={handleChoice} />}
      {phase === "playing" && (
        <StatusBar>
          <span className="text-sm text-muted-foreground">
            Playing tape...{" "}
            {decisionBar !== null && (
              <span className="tabular">
                decision bar in {Math.max(0, decisionBar - currentBar)} bars
              </span>
            )}
          </span>
        </StatusBar>
      )}
      {phase === "resolving" && (
        <StatusBar>
          <span className="text-sm text-muted-foreground">Playing out resolution...</span>
        </StatusBar>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Sub-components
// --------------------------------------------------------------------------- //

function PlayerHeader({
  session,
  phase,
  currentBar,
  totalBars,
  decisionBar,
  playlistRemaining,
}: {
  session: DrillSession;
  phase: Phase;
  currentBar: number;
  totalBars: number;
  decisionBar: number | null;
  playlistRemaining: number | null;
}) {
  const label = session.blind
    ? "Blind drill"
    : [session.symbol, session.session_date].filter(Boolean).join(" — ");

  const phaseLabel: Record<Phase, string> = {
    loading: "Loading",
    ready: "Ready",
    playing: "Playing",
    decision: "Decision",
    resolving: "Resolving",
    finished: "Finished",
  };

  return (
    <div className="flex shrink-0 items-center justify-between border-b border-border bg-card px-4 py-2">
      <div className="flex items-center gap-3">
        <span className="font-semibold text-sm">{label}</span>
        <Badge variant="outline" className="text-[11px]">
          {phaseLabel[phase]}
        </Badge>
        {session.drill_type && (
          <Badge variant="muted" className="text-[11px] hidden sm:inline-flex">
            {session.drill_type}
          </Badge>
        )}
        {playlistRemaining !== null && (
          <Badge variant="muted" className="text-[11px] hidden sm:inline-flex gap-1">
            <List className="h-3 w-3" />
            {playlistRemaining > 0
              ? `Playlist · ${playlistRemaining} remaining`
              : "Playlist · last drill"}
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
        <span>
          bar {currentBar + 1} / {totalBars}
        </span>
        {decisionBar !== null && phase === "playing" && (
          <span className="hidden sm:inline">decision @ bar {decisionBar + 1}</span>
        )}
      </div>
    </div>
  );
}

function DecisionBar({ onChoice }: { onChoice: (c: DrillChoice) => void }) {
  return (
    <div className="shrink-0 border-t border-border bg-card px-4 py-4">
      <p className="mb-3 text-center text-sm font-medium">
        Decision point — what do you do?
      </p>
      <ActionButtons onChoice={onChoice} />
    </div>
  );
}

function StatusBar({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex shrink-0 items-center justify-center border-t border-border bg-card px-4 py-3">
      {children}
    </div>
  );
}

function ReadyScreen({
  session,
  onStart,
  onBack,
  playlistRemaining,
}: {
  session: DrillSession;
  onStart: () => void;
  onBack: () => void;
  playlistRemaining: number | null;
}) {
  const label = session.blind
    ? "Blind drill — ticker and date hidden"
    : [session.symbol, session.session_date].filter(Boolean).join(" — ");

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Ready to start?</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground w-24 shrink-0">Scenario</span>
              <span className="font-medium">{label}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground w-24 shrink-0">Type</span>
              <Badge variant="outline">{session.drill_type}</Badge>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground w-24 shrink-0">Bars</span>
              <span className="tabular">{session.candles.length}</span>
            </div>
            {session.decision_bar !== null && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground w-24 shrink-0">Decision</span>
                <span className="tabular">bar {session.decision_bar + 1}</span>
              </div>
            )}
            {playlistRemaining !== null && (
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground w-24 shrink-0">Playlist</span>
                <span className="text-xs text-muted-foreground">
                  {playlistRemaining > 0
                    ? `${playlistRemaining} more drill${playlistRemaining !== 1 ? "s" : ""} after this`
                    : "Last in sequence"}
                </span>
              </div>
            )}
            {session.blind && (
              <p className="pt-1 text-xs text-muted-foreground">
                Ticker and date are hidden. Annotations are still shown.
              </p>
            )}
          </div>
          <div className="flex gap-2 pt-2">
            <Button variant="outline" onClick={onBack} className="flex-1">
              <ArrowLeft className="h-4 w-4" />
              Back
            </Button>
            <Button onClick={onStart} className="flex-1">
              Start
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function FinishedScreen({
  session,
  result,
  userActions,
  onTakeAnother,
  onTakeAgain,
  playlistRemaining,
}: {
  session: DrillSession;
  result: AttemptFinishResponse;
  userActions: AttemptAction[];
  onTakeAnother: () => void;
  onTakeAgain: () => void;
  playlistRemaining: number | null;
}) {
  // Label for the "continue" CTA depends on playlist state.
  const continueLabel =
    playlistRemaining === null
      ? "Take another drill"
      : playlistRemaining > 0
        ? `Next drill (${playlistRemaining} left)`
        : "Finish playlist";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-2xl space-y-6">
        <div className="flex flex-col items-center gap-4 text-center">
          <ScoreBadge score={result.process_score} />
          <p className="max-w-xs text-xs text-muted-foreground">
            Process score measures rule adherence and decision quality — not P&L.
            A disciplined decision on a bad setup scores higher than a lucky break
            on a broken rule.
          </p>
        </div>

        {result.outcome_pnl !== null && (
          <div className="rounded-md border border-border bg-muted/30 px-4 py-3 text-sm">
            <span className="text-muted-foreground">Outcome P&L: </span>
            <span
              className={result.outcome_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}
            >
              {result.outcome_pnl >= 0 ? "+" : ""}
              {result.outcome_pnl.toFixed(2)}
            </span>
            <span className="ml-2 text-[11px] text-muted-foreground">
              (recorded · not scored)
            </span>
          </div>
        )}

        <AnswerKeyCard answerKey={result.answer_key} userActions={userActions} />

        {result.checklist.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle>Checklist</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1.5">
                {result.checklist.map((item, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5">
                      {(item as { checked?: boolean }).checked ? (
                        <span className="text-emerald-400">✓</span>
                      ) : (
                        <span className="text-rose-400">✗</span>
                      )}
                    </span>
                    <span>
                      {typeof (item as { item?: string }).item === "string"
                        ? (item as { item: string }).item
                        : JSON.stringify(item)}
                    </span>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        )}

        {!session.blind && (session.symbol || session.session_date) && (
          <p className="text-center text-xs text-muted-foreground">
            {[session.symbol, session.session_date].filter(Boolean).join(" · ")}
          </p>
        )}

        <div className="flex justify-center gap-3 pb-4">
          <Button variant="outline" onClick={onTakeAnother}>
            {continueLabel}
          </Button>
          <Button onClick={onTakeAgain}>Take this drill again</Button>
        </div>
      </div>
    </div>
  );
}

function PlayerSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
      <Skeleton className="h-6 w-48" />
      <Skeleton className="min-h-0 flex-1" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

function PlayerError({
  error,
  message,
  onBack,
}: {
  error?: unknown;
  message?: string;
  onBack?: () => void;
}) {
  const status = error instanceof ApiError ? error.status : undefined;
  const isBackendDown = status === 0;
  const notImplemented = status === 501 || status === 404;

  const detail =
    message ??
    (error instanceof ApiError
      ? (error.detail ?? error.message)
      : error instanceof Error
        ? error.message
        : "Something went wrong.");

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4 p-6">
      <Alert
        variant={notImplemented ? "default" : "destructive"}
        className="max-w-lg"
      >
        {isBackendDown ? (
          <ServerCrash className="h-4 w-4" />
        ) : (
          <AlertCircle className="h-4 w-4" />
        )}
        <AlertTitle>
          {isBackendDown
            ? "Backend not running"
            : notImplemented
              ? "Training module not ready"
              : "Error"}
        </AlertTitle>
        <AlertDescription>{detail}</AlertDescription>
      </Alert>
      {onBack && (
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
          Go back
        </Button>
      )}
    </div>
  );
}
