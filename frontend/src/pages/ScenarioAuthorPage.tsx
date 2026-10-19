/**
 * Scenario authoring UI (M6 task 6.1.3).
 *
 * Layout: ReplayChart (full-session view, reused) on the left; a structured
 * authoring form on the right. The user scrubs the tape with ReplayControls,
 * pins the start/decision/end bars and adds bar-anchored annotations, then
 * writes the answer key and POSTs to /api/training/scenarios.
 *
 * Data flow:
 * - Calls /api/replay/session (existing) to load the full tape; the author
 *   sees the complete session and sets bar indices from it.
 * - On save, POSTs ScenarioCreateInput to /api/training/scenarios.
 * - The drill player (6.1.2) will later call /api/training/scenarios/{id}/session
 *   to get the sliced, optionally-blinded view — that's separate from this page.
 */
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  BookMarked,
  Pin,
  PinOff,
  ServerCrash,
  Trash2,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/components/ui/toast";
import { ReplayChart } from "@/components/replay/ReplayChart";
import { ReplayControls } from "@/components/replay/ReplayControls";
import { SYMBOLS, type Symbol } from "@/components/replay/ReplayToolbar";
import { ApiError } from "@/lib/api";
import { useReplaySession } from "@/lib/queries";
import { useReplayEngine } from "@/lib/useReplayEngine";
import { formatEt } from "@/lib/chart";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import {
  ALL_ATTEMPT_ACTION_TYPES,
  ARCHETYPE_LABELS,
  ATTEMPT_ACTION_LABELS,
  DRILL_TYPE_LABELS,
  SOURCE_TYPE_LABELS,
  useCreateScenario,
  type Archetype,
  type AttemptActionType,
  type DrillType,
  type ScenarioAnnotation,
  type ScenarioCreateInput,
  type ScenarioSource,
} from "@/lib/training";

const DEFAULT_SYMBOL: Symbol = "AAPL";
const DEFAULT_DATE = "2026-07-01";

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

interface FormErrors {
  title?: string;
  source_type?: string;
  answer_key?: string;
  bars?: string;
}

function validateForm(
  title: string,
  sourceType: ScenarioSource | "",
  correctActions: AttemptActionType[],
  startBar: number | null,
  decisionBar: number | null,
  endBar: number | null,
): FormErrors {
  const errors: FormErrors = {};
  if (!title.trim()) errors.title = "Title is required.";
  if (!sourceType) errors.source_type = "Source type is required.";
  if (correctActions.length === 0)
    errors.answer_key = "Select at least one correct action.";
  if (startBar !== null && endBar !== null && endBar <= startBar)
    errors.bars = "End bar must be after start bar.";
  if (
    decisionBar !== null &&
    startBar !== null &&
    endBar !== null &&
    (decisionBar < startBar || decisionBar > endBar)
  )
    errors.bars = "Decision bar must be between start and end bars.";
  return errors;
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ScenarioAuthorPage() {
  const navigate = useNavigate();
  const { toast } = useToast();
  const createScenario = useCreateScenario();

  // Session picker
  const [symbol, setSymbol] = useState<Symbol>(DEFAULT_SYMBOL);
  const [date, setDate] = useState<string>(DEFAULT_DATE);

  // Load the full replay session (same endpoint as ReplayPage)
  const { data, isLoading, isError, error } = useReplaySession({ symbol, date });
  const total = data?.candles.length ?? 0;
  const engine = useReplayEngine(total);
  const ready = !isLoading && !isError && data && total > 0;

  // Reset engine when session changes
  const sessionKey = `${symbol}:${date}`;
  const [loggedKey, setLoggedKey] = useState(sessionKey);
  if (sessionKey !== loggedKey) {
    setLoggedKey(sessionKey);
  }

  // Authoring form state
  const [title, setTitle] = useState("");
  const [archetype, setArchetype] = useState<Archetype>("custom");
  const [sourceType, setSourceType] = useState<ScenarioSource | "">("");
  const [drillType, setDrillType] = useState<DrillType>("decision-point");
  const [startBar, setStartBar] = useState<number | null>(null);
  const [decisionBar, setDecisionBar] = useState<number | null>(null);
  const [endBar, setEndBar] = useState<number | null>(null);
  const [annotations, setAnnotations] = useState<ScenarioAnnotation[]>([]);
  const [annotationText, setAnnotationText] = useState("");
  const [correctActions, setCorrectActions] = useState<AttemptActionType[]>([]);
  const [explanation, setExplanation] = useState("");
  const [blindDefault, setBlindDefault] = useState(false);
  const [errors, setErrors] = useState<FormErrors>({});

  const currentBar = engine.currentBar;

  function barLabel(bar: number | null): string {
    if (bar === null) return "—";
    if (!data) return String(bar);
    const candle = data.candles[bar];
    if (!candle) return String(bar);
    return `bar ${bar} (${formatEt(candle.ts, data.meta.tz)})`;
  }

  function toggleAction(action: AttemptActionType) {
    setCorrectActions((prev) =>
      prev.includes(action) ? prev.filter((a) => a !== action) : [...prev, action],
    );
  }

  function addAnnotation() {
    if (!annotationText.trim()) return;
    setAnnotations((prev) => [
      ...prev,
      { bar: currentBar, text: annotationText.trim(), kind: "note" },
    ]);
    setAnnotationText("");
  }

  function removeAnnotation(idx: number) {
    setAnnotations((prev) => prev.filter((_, i) => i !== idx));
  }

  function handleSave() {
    const errs = validateForm(
      title,
      sourceType,
      correctActions,
      startBar,
      decisionBar,
      endBar,
    );
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;

    const payload: ScenarioCreateInput = {
      title: title.trim(),
      archetype,
      source_type: sourceType as ScenarioSource,
      drill_type: drillType,
      symbol: sourceType === "synthetic" ? null : symbol,
      session_date: sourceType === "synthetic" ? null : date,
      start_bar: startBar ?? 0,
      decision_bar: decisionBar,
      end_bar: endBar,
      annotations,
      answer_key: {
        correct_actions: correctActions,
        explanation: explanation.trim(),
        management_notes: [],
      },
      blind_default: blindDefault,
    };

    createScenario.mutate(payload, {
      onSuccess: (scenario) => {
        toast({
          title: "Scenario saved",
          description: `"${scenario.title}" created (id ${scenario.id})`,
          variant: "success",
        });
        navigate("/training/scenarios");
      },
      onError: (err) => {
        toast({
          title: "Save failed",
          description:
            err instanceof Error ? err.message : "Could not create scenario.",
          variant: "destructive",
        });
      },
    });
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Top bar */}
      <div className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
        <BookMarked className="h-4 w-4 text-primary" />
        <span className="font-semibold">New Scenario</span>

        <div className="flex items-center gap-2">
          <Label htmlFor="author-symbol" className="text-sm">
            Symbol
          </Label>
          <Select
            value={symbol}
            onValueChange={(v) => setSymbol(v as Symbol)}
          >
            <SelectTrigger id="author-symbol" className="h-8 w-28 tabular">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SYMBOLS.map((s) => (
                <SelectItem key={s} value={s} className="tabular">
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2">
          <Label htmlFor="author-date" className="text-sm">
            Date
          </Label>
          <Input
            id="author-date"
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="h-8 w-40 tabular"
          />
        </div>

        <div className="ml-auto flex items-center gap-2">
          <ReplayControls engine={engine} disabled={!ready} />
        </div>
      </div>

      {/* Main body: chart + form */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Left: chart */}
        <div className="relative min-h-0 flex-1">
          {isLoading && <ChartSkeleton />}
          {isError && <ChartError error={error} />}
          {!isLoading && !isError && data && total === 0 && (
            <EmptyState symbol={symbol} date={date} />
          )}
          {ready && (
            <>
              <BarOverlay
                currentBar={currentBar}
                total={total}
                startBar={startBar}
                decisionBar={decisionBar}
                endBar={endBar}
                session={data}
              />
              <ReplayChart
                session={data}
                currentIndex={engine.currentIndex}
                trades={[]}
              />
              {/* Scrubber */}
              <div className="pointer-events-auto absolute inset-x-3 bottom-2 z-10 flex items-center gap-3 rounded-md bg-card/80 px-3 py-1.5 backdrop-blur">
                <input
                  type="range"
                  min={0}
                  max={Math.max(0, total - 1)}
                  value={engine.currentIndex}
                  onChange={(e) => engine.setIndex(Number(e.target.value))}
                  className="h-1 flex-1 cursor-pointer accent-primary"
                  aria-label="Scrub replay position"
                />
                <span className="tabular text-label text-muted-foreground">
                  {engine.currentIndex + 1}/{total}
                </span>
              </div>
            </>
          )}
        </div>

        {/* Right: authoring form */}
        <aside className="flex w-80 shrink-0 flex-col gap-0 overflow-y-auto border-l border-border bg-card">
          <div className="flex flex-col gap-4 p-4">
            {/* Title */}
            <FormField
              label="Title"
              error={errors.title}
              htmlFor="scenario-title"
            >
              <Input
                id="scenario-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. PDH break — long entry"
                className="h-8"
              />
            </FormField>

            {/* Archetype */}
            <FormField label="Archetype" htmlFor="scenario-archetype">
              <Select
                value={archetype}
                onValueChange={(v) => setArchetype(v as Archetype)}
              >
                <SelectTrigger id="scenario-archetype" className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.entries(ARCHETYPE_LABELS) as [Archetype, string][]).map(
                    ([k, label]) => (
                      <SelectItem key={k} value={k}>
                        {label}
                      </SelectItem>
                    ),
                  )}
                </SelectContent>
              </Select>
            </FormField>

            {/* Source type */}
            <FormField
              label="Source"
              error={errors.source_type}
              htmlFor="scenario-source"
            >
              <Select
                value={sourceType}
                onValueChange={(v) => setSourceType(v as ScenarioSource)}
              >
                <SelectTrigger id="scenario-source" className="h-8">
                  <SelectValue placeholder="Select source…" />
                </SelectTrigger>
                <SelectContent>
                  {(
                    Object.entries(SOURCE_TYPE_LABELS) as [
                      ScenarioSource,
                      string,
                    ][]
                  ).map(([k, label]) => (
                    <SelectItem key={k} value={k}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>

            {/* Drill type */}
            <FormField label="Drill Type" htmlFor="scenario-drill-type">
              <Select
                value={drillType}
                onValueChange={(v) => setDrillType(v as DrillType)}
              >
                <SelectTrigger id="scenario-drill-type" className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(
                    Object.entries(DRILL_TYPE_LABELS) as [DrillType, string][]
                  ).map(([k, label]) => (
                    <SelectItem key={k} value={k}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormField>

            <Divider />

            {/* Bar range */}
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Bar Range
              </span>
              {errors.bars && (
                <p className="text-xs text-destructive">{errors.bars}</p>
              )}
              <BarButton
                label="Start"
                value={barLabel(startBar)}
                set={ready ? () => setStartBar(currentBar) : undefined}
                clear={startBar !== null ? () => setStartBar(null) : undefined}
                highlight={startBar !== null && startBar === currentBar}
              />
              <BarButton
                label="Decision"
                value={barLabel(decisionBar)}
                set={ready ? () => setDecisionBar(currentBar) : undefined}
                clear={
                  decisionBar !== null ? () => setDecisionBar(null) : undefined
                }
                highlight={decisionBar !== null && decisionBar === currentBar}
              />
              <BarButton
                label="End"
                value={barLabel(endBar)}
                set={ready ? () => setEndBar(currentBar) : undefined}
                clear={endBar !== null ? () => setEndBar(null) : undefined}
                highlight={endBar !== null && endBar === currentBar}
              />
            </div>

            <Divider />

            {/* Annotations */}
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Annotations
              </span>
              {annotations.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No annotations yet.
                </p>
              )}
              {annotations.map((ann, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-2 rounded-md bg-muted/40 px-2 py-1.5 text-xs"
                >
                  <Pin className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                  <span className="flex-1">
                    <span className="tabular font-medium">bar {ann.bar}</span>{" "}
                    — {ann.text}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeAnnotation(idx)}
                    className="text-muted-foreground hover:text-destructive"
                    aria-label="Remove annotation"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
              <div className="flex gap-1.5">
                <Input
                  value={annotationText}
                  onChange={(e) => setAnnotationText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addAnnotation();
                    }
                  }}
                  placeholder="Note at current bar…"
                  className="h-7 flex-1 text-xs"
                  disabled={!ready}
                />
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 px-2 text-xs"
                  onClick={addAnnotation}
                  disabled={!ready || !annotationText.trim()}
                  title={`Pin at bar ${currentBar}`}
                >
                  <Pin className="h-3 w-3" />
                  <span className="ml-1">Pin</span>
                </Button>
              </div>
            </div>

            <Divider />

            {/* Answer key */}
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Answer Key
              </span>
              {errors.answer_key && (
                <p className="text-xs text-destructive">{errors.answer_key}</p>
              )}
              <p className="text-xs text-muted-foreground">
                Correct action(s) at the decision bar:
              </p>
              <div className="flex flex-wrap gap-1.5">
                {ALL_ATTEMPT_ACTION_TYPES.map((action) => {
                  const selected = correctActions.includes(action);
                  return (
                    <button
                      key={action}
                      type="button"
                      onClick={() => toggleAction(action)}
                      className={`rounded-full border px-2 py-0.5 text-label font-medium transition-colors ${
                        selected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border text-muted-foreground hover:border-primary/50 hover:text-foreground"
                      }`}
                    >
                      {ATTEMPT_ACTION_LABELS[action]}
                    </button>
                  );
                })}
              </div>
              <Label htmlFor="scenario-explanation" className="text-xs mt-1">
                Explanation (shown after drill)
              </Label>
              <Textarea
                id="scenario-explanation"
                value={explanation}
                onChange={(e) => setExplanation(e.target.value)}
                placeholder="Explain the setup and why the correct actions apply…"
                className="min-h-[72px] resize-none text-xs"
              />
            </div>

            <Divider />

            {/* Blind default */}
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={blindDefault}
                onChange={(e) => setBlindDefault(e.target.checked)}
                className="h-4 w-4 accent-primary"
              />
              <span>Blind by default</span>
              <span className="text-xs text-muted-foreground">
                (hides ticker/date from the player)
              </span>
            </label>

            <Divider />

            {/* Save */}
            <Button
              onClick={handleSave}
              disabled={createScenario.isPending}
              className="w-full"
            >
              {createScenario.isPending ? "Saving…" : "Save Scenario"}
            </Button>
          </div>
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small sub-components
// ---------------------------------------------------------------------------

function Divider() {
  return <div className="h-px w-full bg-border" />;
}

function FormField({
  label,
  htmlFor,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={htmlFor} className="text-xs">
        {label}
      </Label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

function BarButton({
  label,
  value,
  set,
  clear,
  highlight,
}: {
  label: string;
  value: string;
  set?: () => void;
  clear?: () => void;
  highlight: boolean;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-16 shrink-0 text-xs text-muted-foreground">
        {label}:
      </span>
      <span
        className={`flex-1 truncate text-xs tabular ${highlight ? "text-primary font-medium" : "text-foreground"}`}
      >
        {value}
      </span>
      {set && (
        <Button
          size="icon"
          variant="ghost"
          className="h-6 w-6"
          onClick={set}
          title={`Set ${label.toLowerCase()} bar at current position`}
        >
          <Pin className="h-3 w-3" />
        </Button>
      )}
      {clear && (
        <Button
          size="icon"
          variant="ghost"
          className="h-6 w-6 text-muted-foreground hover:text-destructive"
          onClick={clear}
          title={`Clear ${label.toLowerCase()} bar`}
        >
          <PinOff className="h-3 w-3" />
        </Button>
      )}
    </div>
  );
}

function BarOverlay({
  currentBar,
  total,
  startBar,
  decisionBar,
  endBar,
  session,
}: {
  currentBar: number;
  total: number;
  startBar: number | null;
  decisionBar: number | null;
  endBar: number | null;
  session: { candles: { ts: string }[]; meta: { tz: string } };
}) {
  const candle = session.candles[currentBar];
  const barTime = candle ? formatEt(candle.ts, session.meta.tz) : "—";

  const markers: { label: string; bar: number; color: string }[] = [];
  if (startBar !== null)
    markers.push({ label: "Start", bar: startBar, color: "#26a69a" });
  if (decisionBar !== null)
    markers.push({ label: "Decision", bar: decisionBar, color: "#f5a623" });
  if (endBar !== null)
    markers.push({ label: "End", bar: endBar, color: "#ef5350" });

  return (
    <div className="pointer-events-none absolute left-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-4 gap-y-1 rounded-md bg-card/80 px-3 py-1.5 text-xs backdrop-blur">
      <span className="tabular text-muted-foreground">
        bar {currentBar + 1} / {total}
      </span>
      <span className="tabular text-muted-foreground">{barTime} {DISPLAY_TZ_LABEL}</span>
      {markers.map(({ label, bar, color }) => (
        <span key={label} className="flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: color }}
          />
          <span style={{ color }} className="tabular">
            {label}: {bar}
          </span>
        </span>
      ))}
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="absolute inset-0 flex flex-col gap-2 p-4">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="min-h-0 flex-1" />
    </div>
  );
}

function EmptyState({ symbol, date }: { symbol: string; date: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <Alert className="max-w-md">
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>No session data</AlertTitle>
        <AlertDescription>
          No bars for <span className="tabular">{symbol}</span> on{" "}
          <span className="tabular">{date}</span>.
        </AlertDescription>
      </Alert>
    </div>
  );
}

function ChartError({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const isBackendDown = status === 0;
  const notImplemented = status === 501 || status === 404;
  const message =
    error instanceof ApiError
      ? error.detail || error.message
      : error instanceof Error
        ? error.message
        : "Something went wrong loading the replay session.";

  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <Alert
        variant={notImplemented ? "default" : "destructive"}
        className="max-w-md"
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
              ? "Replay endpoint not ready"
              : "Couldn't load session"}
        </AlertTitle>
        <AlertDescription>
          {isBackendDown ? (
            <>
              The data service isn't reachable. Start it, then reload.
            </>
          ) : notImplemented ? (
            <>
              <code className="tabular rounded bg-muted px-1 py-0.5">
                /api/replay/session
              </code>{" "}
              isn't implemented yet.
            </>
          ) : (
            message
          )}
        </AlertDescription>
      </Alert>
    </div>
  );
}
