/**
 * DebriefPanel — the trade-debrief interview, rendered inside the cockpit's
 * right column (see TradeReviewPage). Extracted from the former standalone
 * /debriefs/new/:tradeId page so the annotated chart stays visible while the
 * user answers the interview (epic #64 / #63, closes #62).
 *
 * Walks the user through the 4-step debrief interview:
 *   setup → plan → feelings → verdict
 *
 * State machine (useReducer):
 *   starting   → POST /api/debriefs → get first turn → active
 *   active     → chat bubbles (no options) or stepper chips (options present)
 *              → on send/next: POST /api/debriefs/{id}/turn
 *              → when turn.done: → previewing
 *   previewing → show what will be saved (verdict, feelings extracted from transcript)
 *              → Save & finish → POST /api/debriefs/{id}/finish {emit_scenario:false}
 *              → Save & make drill → POST /finish {emit_scenario:true}
 *              → both → saved
 *   saved      → summary card; navigate to drill if scenario was emitted, or
 *                trigger the in-cockpit critique (no route change).
 *   error      → error display
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, MessageCircle, Send, ServerCrash, Sparkles } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { useAgentStatus } from "@/lib/queries";
import { finishDebrief, startDebrief, submitTurn } from "@/lib/debriefs";
import type {
  Debrief,
  DebriefArtifacts,
  InterviewTurn,
  TranscriptEntry,
} from "@/lib/debriefTypes";
import type { DebriefVerdict } from "@/lib/trainingTypes";
import { cn } from "@/lib/utils";

// --------------------------------------------------------------------------- //
// State machine
// --------------------------------------------------------------------------- //

type Phase = "starting" | "active" | "previewing" | "saved" | "error";

interface DebriefState {
  phase: Phase;
  debrief: Debrief | null;
  /** Current agent turn (the question being answered). */
  currentTurn: InterviewTurn | null;
  /** Full conversation history for chat rendering. */
  transcript: TranscriptEntry[];
  artifacts: DebriefArtifacts | null;
  errorMessage: string | null;
}

type DebriefAction =
  | { type: "START_SUCCESS"; debrief: Debrief; turn: InterviewTurn }
  | { type: "TURN_RECEIVED"; userText: string; agentTurn: InterviewTurn }
  | { type: "PREVIEW" }
  | { type: "SAVED"; artifacts: DebriefArtifacts }
  | { type: "ERROR"; message: string };

function reducer(state: DebriefState, action: DebriefAction): DebriefState {
  switch (action.type) {
    case "START_SUCCESS":
      return {
        ...state,
        phase: "active",
        debrief: action.debrief,
        currentTurn: action.turn,
        transcript: [
          {
            role: "agent",
            step: action.turn.step,
            text: action.turn.question ?? "",
          },
        ],
      };

    case "TURN_RECEIVED": {
      const newTranscript: TranscriptEntry[] = [
        ...state.transcript,
        // user reply (append to transcript view)
        {
          role: "user",
          step: state.currentTurn?.step ?? "setup",
          text: action.userText,
        },
      ];
      if (action.agentTurn.question) {
        newTranscript.push({
          role: "agent",
          step: action.agentTurn.step,
          text: action.agentTurn.question,
        });
      }
      if (action.agentTurn.done) {
        return {
          ...state,
          phase: "previewing",
          currentTurn: action.agentTurn,
          transcript: newTranscript,
        };
      }
      return {
        ...state,
        phase: "active",
        currentTurn: action.agentTurn,
        transcript: newTranscript,
      };
    }

    case "PREVIEW":
      return { ...state, phase: "previewing" };

    case "SAVED":
      return { ...state, phase: "saved", artifacts: action.artifacts };

    case "ERROR":
      return { ...state, phase: "error", errorMessage: action.message };

    default:
      return state;
  }
}

const initialState: DebriefState = {
  phase: "starting",
  debrief: null,
  currentTurn: null,
  transcript: [],
  artifacts: null,
  errorMessage: null,
};

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

const STEP_LABELS: Record<string, string> = {
  setup: "Setup",
  plan: "Plan",
  feelings: "Feelings",
  verdict: "Verdict",
  done: "Done",
};

/** Extract the last user-reply for a given step from the transcript. */
function extractFromTranscript(
  transcript: TranscriptEntry[],
  step: string,
): string | null {
  let lastAgentStep: string | null = null;
  for (const entry of transcript) {
    if (entry.role === "agent") {
      lastAgentStep = entry.step;
    } else if (entry.role === "user" && lastAgentStep === step) {
      return entry.text;
    }
  }
  return null;
}

/** Human-readable label for a verdict value. */
function verdictLabel(v: DebriefVerdict | null): string {
  if (!v) return "—";
  const map: Record<string, string> = {
    "setup-failed": "Setup failed (good trade, setup didn't work)",
    "failed-the-setup": "Failed the setup (valid setup, execution broke it)",
    "good-loss": "Good loss (followed plan, took planned risk)",
    "bad-win": "Bad win (broke rules but got lucky)",
    clean: "Clean (followed plan and won)",
  };
  return map[v] ?? v;
}

// --------------------------------------------------------------------------- //
// Panel
// --------------------------------------------------------------------------- //

export interface DebriefPanelProps {
  /** Trade being debriefed. */
  tradeId: number;
  /**
   * Called from the saved screen's "Get critique" affordance. The critique
   * renders in the cockpit's LEFT column, so this triggers/focuses it in place
   * rather than navigating away. Omit to hide the affordance.
   */
  onGetCritique?: () => void;
}

export function DebriefPanel({ tradeId, onGetCritique }: DebriefPanelProps) {
  const navigate = useNavigate();

  const [state, dispatch] = useReducer(reducer, initialState);
  const { phase, debrief, currentTurn, transcript, artifacts, errorMessage } =
    state;

  // Boot: POST /api/debriefs as soon as the panel mounts.
  const startedRef = useRef(false);
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    startDebrief({ trade_id: tradeId })
      .then(({ debrief: d, turn }) =>
        dispatch({ type: "START_SUCCESS", debrief: d, turn }),
      )
      .catch((err) =>
        dispatch({
          type: "ERROR",
          message:
            err instanceof ApiError
              ? (err.detail ?? err.message)
              : String(err),
        }),
      );
  }, [tradeId]);

  // ---------- render phases ---------- //
  if (phase === "starting") {
    return <DebriefSkeleton />;
  }

  if (phase === "error") {
    return <DebriefError message={errorMessage ?? "Something went wrong."} />;
  }

  if (phase === "saved" && artifacts) {
    return (
      <SavedScreen
        tradeId={tradeId}
        artifacts={artifacts}
        onTakeDrill={() =>
          navigate(`/training/drills/${artifacts.scenario_id}`)
        }
        onGetCritique={onGetCritique}
      />
    );
  }

  if (phase === "previewing" && debrief) {
    return (
      <PreviewScreen
        debrief={debrief}
        transcript={transcript}
        onSave={(emitScenario) => {
          finishDebrief(debrief.id, { emit_scenario: emitScenario })
            .then((art) => dispatch({ type: "SAVED", artifacts: art }))
            .catch((err) =>
              dispatch({
                type: "ERROR",
                message:
                  err instanceof ApiError
                    ? (err.detail ?? err.message)
                    : String(err),
              }),
            );
        }}
      />
    );
  }

  if (phase === "active" && debrief && currentTurn) {
    return (
      <ActiveInterview
        debrief={debrief}
        currentTurn={currentTurn}
        transcript={transcript}
        dispatch={dispatch}
      />
    );
  }

  return <DebriefSkeleton />;
}

// --------------------------------------------------------------------------- //
// Active interview
// --------------------------------------------------------------------------- //

interface ActiveInterviewProps {
  debrief: Debrief;
  currentTurn: InterviewTurn;
  transcript: TranscriptEntry[];
  dispatch: React.Dispatch<DebriefAction>;
}

function ActiveInterview({
  debrief,
  currentTurn,
  transcript,
  dispatch,
}: ActiveInterviewProps) {
  const [text, setText] = useState("");
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Scroll to bottom when transcript grows.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript.length]);

  // Reset local form state when the step advances.
  useEffect(() => {
    setText("");
    setSelectedOptions([]);
  }, [currentTurn.step]);

  const isStepperMode = currentTurn.options !== null;

  const handleSend = useCallback(async () => {
    const reply = text.trim();
    // In stepper mode accept option selection OR free text (either is enough).
    const effectiveText =
      selectedOptions.length > 0
        ? selectedOptions.join(", ") + (reply ? ` — ${reply}` : "")
        : reply;

    if (!effectiveText) return;
    if (sending) return;

    setSending(true);
    try {
      const fields: Record<string, unknown> =
        selectedOptions.length > 0 ? { selected: selectedOptions } : {};
      const turn = await submitTurn(debrief.id, {
        text: effectiveText,
        fields: Object.keys(fields).length > 0 ? fields : undefined,
      });
      dispatch({
        type: "TURN_RECEIVED",
        userText: effectiveText,
        agentTurn: turn,
      });
    } catch (err) {
      dispatch({
        type: "ERROR",
        message:
          err instanceof ApiError ? (err.detail ?? err.message) : String(err),
      });
    } finally {
      setSending(false);
    }
  }, [debrief.id, dispatch, selectedOptions, sending, text]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter (without shift) sends in chat mode only.
    if (!isStepperMode && e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  const toggleOption = (opt: string) => {
    setSelectedOptions((prev) =>
      prev.includes(opt) ? prev.filter((o) => o !== opt) : [...prev, opt],
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Step badge row */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border pb-2">
        <MessageCircle className="h-4 w-4 text-muted-foreground" />
        <Badge variant="outline" className="text-label">
          {STEP_LABELS[currentTurn.step] ?? currentTurn.step}
        </Badge>
        {isStepperMode && (
          <Badge variant="muted" className="text-label">
            form mode
          </Badge>
        )}
      </div>

      {/* Transcript scroll area */}
      <div className="min-h-0 flex-1 overflow-y-auto py-3">
        <div className="flex flex-col gap-3">
          {isStepperMode ? (
            // Stepper mode: render each step as a card
            <StepperView transcript={transcript} />
          ) : (
            // Chat mode: render as chat bubbles
            <ChatView transcript={transcript} />
          )}

          {/* Drift flag callout (context_note) */}
          {currentTurn.context_note && (
            <div className="rounded-md border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-300">
              <span className="font-medium">Note: </span>
              {currentTurn.context_note}
            </div>
          )}

          {/* Current question (agent bubble) — already rendered above in chat mode */}
          {isStepperMode && (
            <StepCard
              step={currentTurn.step}
              question={currentTurn.question ?? ""}
              options={currentTurn.options}
              selectedOptions={selectedOptions}
              onToggleOption={toggleOption}
              extraText={text}
              onExtraTextChange={(v) => setText(v)}
            />
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input bar */}
      <div className="shrink-0 border-t border-border pt-3">
        {!isStepperMode && (
          <div className="flex gap-2">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Your reply… (Enter to send, Shift+Enter for new line)"
              className="min-h-[60px] resize-none"
              disabled={sending}
            />
            <Button
              onClick={() => void handleSend()}
              disabled={!text.trim() || sending}
              size="icon"
              className="h-[60px] w-[60px] shrink-0"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        )}

        {isStepperMode && (
          <div className="flex justify-end">
            <Button
              onClick={() => void handleSend()}
              disabled={
                (selectedOptions.length === 0 && !text.trim()) || sending
              }
            >
              {sending ? "Sending…" : "Next"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Chat mode view
// --------------------------------------------------------------------------- //

function ChatView({ transcript }: { transcript: TranscriptEntry[] }) {
  return (
    <>
      {transcript.map((entry, i) => (
        <ChatBubble key={i} entry={entry} />
      ))}
    </>
  );
}

function ChatBubble({ entry }: { entry: TranscriptEntry }) {
  const isAgent = entry.role === "agent";
  return (
    <div className={cn("flex", isAgent ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
          isAgent
            ? "rounded-tl-sm bg-muted text-foreground"
            : "rounded-tr-sm bg-primary text-primary-foreground",
        )}
      >
        {isAgent && entry.step && (
          <p className="mb-1 text-label font-semibold uppercase tracking-widest opacity-60">
            {STEP_LABELS[entry.step] ?? entry.step}
          </p>
        )}
        {entry.text}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Stepper mode view
// --------------------------------------------------------------------------- //

/** Renders completed turns (agent + user pairs) as cards. */
function StepperView({ transcript }: { transcript: TranscriptEntry[] }) {
  const pairs: Array<{ agentText: string; userText: string; step: string }> =
    [];
  let currentAgent: TranscriptEntry | null = null;

  for (const entry of transcript) {
    if (entry.role === "agent") {
      currentAgent = entry;
    } else if (currentAgent) {
      pairs.push({
        agentText: currentAgent.text,
        userText: entry.text,
        step: currentAgent.step,
      });
      currentAgent = null;
    }
  }

  // If there's a dangling agent turn (current question), don't show it here
  // since StepCard below renders it.

  return (
    <>
      {pairs.map((pair, i) => (
        <Card key={i} className="bg-card/60">
          <CardHeader className="pb-2 pt-3">
            <div className="flex items-center gap-2">
              <Badge variant="outline" className="text-label">
                {STEP_LABELS[pair.step] ?? pair.step}
              </Badge>
            </div>
            <CardDescription className="text-xs leading-relaxed">
              {pair.agentText}
            </CardDescription>
          </CardHeader>
          <CardContent className="pb-3">
            <p className="text-sm">{pair.userText}</p>
          </CardContent>
        </Card>
      ))}
    </>
  );
}

/** Renders the current (unanswered) step as an interactive card. */
function StepCard({
  step,
  question,
  options,
  selectedOptions,
  onToggleOption,
  extraText,
  onExtraTextChange,
}: {
  step: string;
  question: string;
  options: string[] | null;
  selectedOptions: string[];
  onToggleOption: (opt: string) => void;
  extraText: string;
  onExtraTextChange: (v: string) => void;
}) {
  return (
    <Card className="border-primary/30 bg-card">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Badge className="text-label">{STEP_LABELS[step] ?? step}</Badge>
          <CardTitle className="text-sm font-medium">{question}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {options && (
          <div className="flex flex-wrap gap-2">
            {options.map((opt) => {
              const active = selectedOptions.includes(opt);
              return (
                <button
                  key={opt}
                  type="button"
                  onClick={() => onToggleOption(opt)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                    active
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border text-muted-foreground hover:border-primary/50 hover:text-foreground",
                  )}
                >
                  {opt}
                </button>
              );
            })}
          </div>
        )}
        <Textarea
          value={extraText}
          onChange={(e) => onExtraTextChange(e.target.value)}
          placeholder={options ? "Add context or free-text notes…" : "Your reply…"}
          className="min-h-[80px] resize-none text-sm"
        />
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Preview screen
// --------------------------------------------------------------------------- //

interface PreviewScreenProps {
  debrief: Debrief;
  transcript: TranscriptEntry[];
  onSave: (emitScenario: boolean) => void;
}

function PreviewScreen({ debrief, transcript, onSave }: PreviewScreenProps) {
  const [saving, setSaving] = useState(false);

  const verdictText = extractFromTranscript(transcript, "verdict");
  const feelingsText = extractFromTranscript(transcript, "feelings");
  const planText = extractFromTranscript(transcript, "plan");

  const handleSave = (emitScenario: boolean) => {
    if (saving) return;
    setSaving(true);
    onSave(emitScenario);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
      <div className="flex items-center gap-2">
        <MessageCircle className="h-5 w-5 text-primary" />
        <h3 className="text-sm font-semibold">Review before saving</h3>
      </div>
      <p className="text-xs text-muted-foreground">
        This will be written to trade #{debrief.trade_id} as enrichment.
      </p>

      <Card>
        <CardContent className="space-y-3 pt-4">
          <PreviewRow label="Verdict" value={verdictText} />
          <PreviewRow label="Feelings" value={feelingsText} />
          <PreviewRow label="Plan notes" value={planText} />
          <div className="pt-1">
            <span className="text-label uppercase tracking-widest text-muted-foreground">
              Backend
            </span>
            <p className="text-sm">{debrief.llm_backend}</p>
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-col gap-2">
        <Button
          variant="outline"
          onClick={() => handleSave(false)}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save & finish"}
        </Button>
        <Button onClick={() => handleSave(true)} disabled={saving}>
          {saving ? "Saving…" : "Save & make a drill from this"}
        </Button>
      </div>
    </div>
  );
}

function PreviewRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <span className="text-label uppercase tracking-widest text-muted-foreground">
        {label}
      </span>
      <p className="text-sm">{value ?? "—"}</p>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Saved screen
// --------------------------------------------------------------------------- //

interface SavedScreenProps {
  tradeId: number;
  artifacts: DebriefArtifacts;
  onTakeDrill: () => void;
  onGetCritique?: () => void;
}

function SavedScreen({
  tradeId,
  artifacts,
  onTakeDrill,
  onGetCritique,
}: SavedScreenProps) {
  // "Get critique" (PRD-M9 R6) — only offered when the agent layer is available
  // AND a handler was provided (the critique renders in the cockpit's left col).
  const agentStatus = useAgentStatus();
  const agentAvailable = agentStatus.data?.available === true;
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
      <div className="flex items-center gap-2">
        <MessageCircle className="h-5 w-5 text-primary" />
        <h3 className="text-sm font-semibold">Debrief saved</h3>
      </div>

      <Card>
        <CardContent className="space-y-3 pt-4">
          <div>
            <span className="text-label uppercase tracking-widest text-muted-foreground">
              Trade
            </span>
            <p className="text-sm">#{tradeId}</p>
          </div>
          <div>
            <span className="text-label uppercase tracking-widest text-muted-foreground">
              Enrichment
            </span>
            <p className="text-sm">
              {artifacts.enrichment_saved ? "Saved to trade" : "Not saved"}
            </p>
          </div>
          <div>
            <span className="text-label uppercase tracking-widest text-muted-foreground">
              Verdict
            </span>
            <p className="text-sm">{verdictLabel(artifacts.verdict)}</p>
          </div>
          {artifacts.scenario_id !== null && (
            <div>
              <span className="text-label uppercase tracking-widest text-muted-foreground">
                Emitted drill
              </span>
              <p className="text-sm">
                Scenario #{artifacts.scenario_id} created — take it now to close
                the loop.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-col gap-2">
        {agentAvailable && onGetCritique && (
          <Button variant="outline" onClick={onGetCritique} className="gap-1.5">
            <Sparkles className="h-4 w-4" />
            Get critique
          </Button>
        )}
        {artifacts.scenario_id !== null && (
          <Button onClick={onTakeDrill}>Take the drill</Button>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Loading / error sub-components
// --------------------------------------------------------------------------- //

function DebriefSkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="h-20 w-3/4" />
      <Skeleton className="ml-auto h-14 w-2/3" />
      <Skeleton className="h-20 w-3/4" />
      <Skeleton className="mt-auto h-16 w-full" />
    </div>
  );
}

function DebriefError({ message, error }: { message?: string; error?: unknown }) {
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
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-4">
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
              ? "Debrief module not ready"
              : "Error starting debrief"}
        </AlertTitle>
        <AlertDescription>{detail}</AlertDescription>
      </Alert>
    </div>
  );
}
