/**
 * LearnDrillPage: /learn/drills/:id
 *
 * State machine (derived, not stored):
 *   loading   drill or bars not here yet
 *   playing   bars animate from bars_from up to the last revealed bar (play / pause / step)
 *   decision  the playhead is on the last revealed bar: answer the question steps in order
 *   reveal    after the answer: full bars play forward, GradeResult + explanation + lessons
 *
 * No future leak: before the answer the server only sends bars with ts < pause_at, and
 * guardBars() cuts again on the client. Times are PT and labelled.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, FastForward, FolderOpen, Pause, Play, RotateCcw, ServerCrash, StepForward } from "lucide-react";

import { PriceChart, type PaneSpec } from "@/components/chart/PriceChart";
import { Markdown } from "@/components/learn/Markdown";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type Candle,
  type DrillPublic,
  type GradeResult,
  type LearnStepName,
  type LessonRef,
  type ReplayFill,
} from "@/lib/api";
import { floorMinute } from "@/lib/stackedReplay";
import { isoToUtcSeconds } from "@/lib/chart";
import {
  EMPTY_DRAFT,
  LEAD_MINUTES,
  PLAY_SPEEDS,
  TIMEFRAMES,
  VIEW_MINUTES,
  aggregateBars,
  barsFor,
  bucketStart,
  hourlyLevels,
  kcuTimeframe,
  tfLabel,
  type TfChoice,
  canSubmit,
  chartPickFor,
  currentStep,
  fmtPrice,
  guardBars,
  isNotConfigured,
  missingText,
  nextUndone,
  parsePrice,
  ptTime,
  requiredSteps,
  roundPrice,
  scoreText,
  setEnter,
  shortDate,
  startIndex,
  stepIndex,
  stepDone,
  toAnswerRequest,
  topicName,
  type Draft,
} from "@/lib/learn";
import { useLearnAnswer, useLearnBars, useLearnDrill, useLearnResult, useLearnToday } from "@/lib/queries";
import { isInputFocused } from "@/lib/useReplay";

const DEFAULT_SPEED = 1; // bars per second
const COLOR = { level: "#3b82f6", stop: "#f59e0b", target: "#26a69a", key: "#a1a1aa" };

const STEP_TITLE: Record<LearnStepName, string> = {
  level: "Level",
  enter: "Enter or wait",
  stop: "Stop",
  target: "Target",
};

type Phase = "loading" | "playing" | "decision" | "reveal";

function numOrNull(v: number | string | null | undefined): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Marks for the chart: the draft while answering; yours plus the key once graded. */
function buildMarks(phase: Phase, draft: Draft, grade: GradeResult | null): NonNullable<PaneSpec["marks"]> {
  const marks: NonNullable<PaneSpec["marks"]> = [];
  if (phase === "reveal" && grade) {
    const yours = (s: LearnStepName) => numOrNull(grade.steps.find((x) => x.step === s)?.yours);
    const mine = { level: yours("level"), stop: yours("stop"), target: yours("target") };
    if (mine.level != null) marks.push({ label: "Your level", price: mine.level, color: COLOR.level });
    if (mine.stop != null) marks.push({ label: "Your stop", price: mine.stop, color: COLOR.stop });
    if (mine.target != null) marks.push({ label: "Your target", price: mine.target, color: COLOR.target });
    const k = grade.answer_key;
    if (k.level != null) marks.push({ label: "Key level", price: k.level, color: COLOR.key });
    if (k.stop != null) marks.push({ label: "Key stop", price: k.stop, color: COLOR.key });
    if (k.target != null) marks.push({ label: "Key target", price: k.target, color: COLOR.key });
    return marks;
  }
  if (draft.level != null) marks.push({ label: "Level", price: draft.level, color: COLOR.level, key: "level" });
  if (draft.enter === "enter" && draft.stop != null)
    marks.push({ label: "Stop", price: draft.stop, color: COLOR.stop, key: "stop" });
  if (draft.enter === "enter" && draft.target != null)
    marks.push({ label: "Target", price: draft.target, color: COLOR.target, key: "target" });
  return marks;
}

function PriceField({
  label,
  hint,
  value,
  active,
  onChange,
  onFocus,
}: {
  label: string;
  hint: string;
  value: number | null;
  active: boolean;
  onChange: (v: number | null) => void;
  onFocus: () => void;
}) {
  const [text, setText] = useState(value == null ? "" : String(value));
  // A chart click changes the value from outside; typing must not be overwritten mid-edit.
  useEffect(() => {
    if (parsePrice(text) !== value) setText(value == null ? "" : String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <label className="flex flex-col gap-1">
      <span className="flex items-baseline justify-between text-label">
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">
          {active ? "Click the chart or type" : value != null ? "Drag the line to adjust" : hint}
        </span>
      </span>
      <Input
        inputMode="decimal"
        value={text}
        placeholder="Price"
        aria-label={`${label} price`}
        className={active ? "ring-1 ring-ring" : undefined}
        onFocus={onFocus}
        onChange={(e) => {
          setText(e.target.value);
          onChange(parsePrice(e.target.value));
        }}
      />
    </label>
  );
}

function ChoiceButton({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <Button type="button" variant={on ? "default" : "outline"} size="sm" aria-pressed={on} onClick={onClick}>
      {children}
    </Button>
  );
}

function DecisionPanel({
  drill,
  draft,
  setDraft,
  armed,
  setArmed,
  submitting,
  error,
  onSubmit,
}: {
  drill: DrillPublic;
  draft: Draft;
  setDraft: (d: Draft) => void;
  armed: LearnStepName | null;
  setArmed: (s: LearnStepName | null) => void;
  submitting: boolean;
  error: string | null;
  onSubmit: () => void;
}) {
  const steps = requiredSteps(drill.question_steps, draft.enter);
  const active = armed && steps.includes(armed) ? armed : currentStep(drill.question_steps, draft);
  const ready = canSubmit(drill.question_steps, draft);
  const hasEnter = drill.question_steps.includes("enter");
  return (
    <div className="flex flex-col gap-4" data-testid="decision-panel">
      <div>
        <h2 className="text-value font-semibold">Your call</h2>
        <p className="text-label text-muted-foreground">
          The chart stops at {ptTime(drill.pause_at)}. Answer in order; nothing after this point is shown until you submit.
        </p>
      </div>
      {steps.map((s, i) => {
        const done = stepDone(s, draft);
        return (
          <div key={s} className="flex flex-col gap-2 border-t border-border pt-3 first:border-t-0 first:pt-0">
            <div className="flex items-center gap-2 text-label text-muted-foreground">
              <span className="num">{i + 1}.</span>
              <span className="label-caps font-semibold text-foreground">{STEP_TITLE[s]}</span>
              {done && <Badge variant="muted">Set</Badge>}
            </div>
            {s === "level" && (
              <PriceField
                label="Level"
                hint="The price you are watching"
                value={draft.level}
                active={active === "level"}
                onChange={(v) => setDraft({ ...draft, level: v })}
                onFocus={() => setArmed("level")}
              />
            )}
            {s === "enter" && hasEnter && (
              <div className="flex flex-col gap-2">
                <div className="flex gap-2">
                  <ChoiceButton on={draft.enter === "enter"} onClick={() => setDraft(setEnter(draft, "enter"))}>
                    Enter
                  </ChoiceButton>
                  <ChoiceButton on={draft.enter === "wait"} onClick={() => setDraft(setEnter(draft, "wait"))}>
                    Wait
                  </ChoiceButton>
                </div>
                {draft.enter === "enter" && (
                  <div className="flex gap-2">
                    <ChoiceButton on={draft.side === "long"} onClick={() => setDraft({ ...draft, side: "long" })}>
                      Long
                    </ChoiceButton>
                    <ChoiceButton on={draft.side === "short"} onClick={() => setDraft({ ...draft, side: "short" })}>
                      Short
                    </ChoiceButton>
                  </div>
                )}
                {draft.enter === "wait" && <p className="text-label text-muted-foreground">Wait skips the stop and target.</p>}
              </div>
            )}
            {s === "stop" && (
              <PriceField
                label="Stop"
                hint="Where the idea is wrong"
                value={draft.stop}
                active={active === "stop"}
                onChange={(v) => setDraft({ ...draft, stop: v })}
                onFocus={() => setArmed("stop")}
              />
            )}
            {s === "target" && (
              <PriceField
                label="Target"
                hint="Where you take profit"
                value={draft.target}
                active={active === "target"}
                onChange={(v) => setDraft({ ...draft, target: v })}
                onFocus={() => setArmed("target")}
              />
            )}
          </div>
        );
      })}
      {error && (
        <Alert variant="destructive">
          <ServerCrash className="h-4 w-4" />
          <AlertTitle>Could not submit</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <Button type="button" disabled={!ready || submitting} onClick={onSubmit}>
        {submitting ? "Submitting..." : "Submit answer"}
      </Button>
      {!ready && (
        <p className="text-label text-muted-foreground" data-testid="submit-missing">
          {missingText(drill.question_steps, draft)}
        </p>
      )}
    </div>
  );
}

function verdict(points: number, max: number, graded: boolean): { text: string; cls: string } {
  if (!graded) return { text: "Not graded", cls: "text-muted-foreground" };
  if (points >= max) return { text: "Match", cls: "text-gain" };
  if (points > 0) return { text: "Partial", cls: "text-stop" };
  return { text: "Miss", cls: "text-muted-foreground" };
}

function LessonLinks({ lessons }: { lessons: LessonRef[] }) {
  if (!lessons.length) return null;
  return (
    <div className="flex flex-col gap-1">
      <h3 className="label-caps text-label font-semibold text-muted-foreground">Lessons</h3>
      <ul className="flex flex-col gap-1">
        {lessons.map((l) => (
          <li key={l.id} className="flex items-center justify-between gap-2 text-body">
            <span className="min-w-0 truncate">{l.title ?? `Lesson ${l.id}`}</span>
            {l.video_url ? (
              <a
                href={l.video_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex shrink-0 items-center gap-1 text-primary hover:underline"
              >
                Video <ExternalLink className="h-3.5 w-3.5" />
              </a>
            ) : (
              <span className="shrink-0 text-label text-muted-foreground">No video</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RevealPanel({
  grade,
  drillLessons,
  onBack,
  onNext,
}: {
  grade: GradeResult | null;
  drillLessons: LessonRef[];
  onBack: () => void;
  onNext: (() => void) | null;
}) {
  return (
    <div className="flex flex-col gap-4" data-testid="reveal-panel">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-value font-semibold">Review</h2>
        {grade && (
          <span className="num text-key" aria-label={`Score ${grade.score} out of 100`}>
            {grade.score}
            <span className="text-label text-muted-foreground"> / 100</span>
          </span>
        )}
      </div>
      {!grade ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <>
          <ul className="flex flex-col divide-y divide-border">
            {grade.steps.map((s) => {
              const v = verdict(s.points, s.max, s.graded);
              return (
                <li key={s.step} className="flex flex-col gap-1 py-2 first:pt-0">
                  <div className="flex items-center justify-between gap-2">
                    <span className="label-caps text-label font-semibold">{STEP_TITLE[s.step]}</span>
                    <span className={`text-label font-medium ${v.cls}`}>
                      {v.text}
                      {s.graded && (
                        <span className="num text-muted-foreground">
                          {" "}
                          {s.points}/{s.max}
                        </span>
                      )}
                    </span>
                  </div>
                  <div className="num flex gap-4 text-body">
                    <span>
                      <span className="text-label text-muted-foreground">Key </span>
                      {fmtPrice(s.key)}
                    </span>
                    <span>
                      <span className="text-label text-muted-foreground">Yours </span>
                      {fmtPrice(s.yours)}
                    </span>
                  </div>
                  {s.note && <p className="text-label text-muted-foreground">{s.note}</p>}
                </li>
              );
            })}
          </ul>
          {grade.explanation && <Markdown md={grade.explanation} />}
          <LessonLinks lessons={grade.lessons.length ? grade.lessons : drillLessons} />
        </>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        <Button type="button" variant="outline" onClick={onBack}>
          <ArrowLeft /> Back to Learn
        </Button>
        {onNext && (
          <Button type="button" onClick={onNext}>
            Next drill
          </Button>
        )}
      </div>
    </div>
  );
}

export function LearnDrillPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const drillQ = useLearnDrill(id);
  const today = useLearnToday();
  const drill = drillQ.data && !isNotConfigured(drillQ.data) ? drillQ.data : null;

  const [submittedGrade, setSubmittedGrade] = useState<GradeResult | null>(null);
  const answered = Boolean(submittedGrade) || drill?.status === "done";
  const barsQ = useLearnBars(id, answered, Boolean(drill));
  const resultQ = useLearnResult(id, drill?.status === "done" && !submittedGrade);
  const grade = submittedGrade ?? resultQ.data ?? null;
  const answer = useLearnAnswer(id ?? "");

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [armed, setArmed] = useState<LearnStepName | null>(null);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(DEFAULT_SPEED);
  const [tfChoice, setTfChoice] = useState<TfChoice>("auto");
  const [showHourly, setShowHourly] = useState(true);
  const inited = useRef(false);

  // A new drill resets everything.
  useEffect(() => {
    setSubmittedGrade(null);
    setDraft(EMPTY_DRAFT);
    setArmed(null);
    setIdx(0);
    setPlaying(true);
    inited.current = false;
  }, [id]);

  const rawBars = barsQ.data && !isNotConfigured(barsQ.data) ? barsQ.data : null;
  const candles: Candle[] = useMemo(
    () => (rawBars && drill ? guardBars(rawBars.candles, drill.pause_at, answered) : []),
    [rawBars, drill, answered],
  );
  const n = candles.length;

  const timeframe = rawBars?.timeframe ?? drill?.timeframe ?? "1m";

  // First time bars are here: a fresh drill starts LEAD_MINUTES before the pause (the bars
  // before it are already drawn as context), a done one at the pause.
  useEffect(() => {
    if (inited.current || !drill || n === 0) return;
    inited.current = true;
    const cut = Date.parse(drill.pause_at);
    const pre = candles.filter((c) => Date.parse(c.ts) < cut).length;
    setIdx(answered ? Math.max(0, pre - 1) : startIndex(pre, barsFor(LEAD_MINUTES, timeframe)));
    setPlaying(true);
  }, [drill, n, answered, candles, timeframe]);

  // Animate: `speed` bars per second while playing, until the last bar in hand.
  useEffect(() => {
    if (!playing || n === 0 || idx >= n - 1) return;
    const t = setTimeout(() => setIdx((i) => stepIndex(i, n)), 1000 / speed);
    return () => clearTimeout(t);
  }, [playing, idx, n, speed]);

  const atEnd = n > 0 && idx >= n - 1;
  const fullBarsLoading = answered && barsQ.isPlaceholderData;
  const phase: Phase = !drill || n === 0 ? "loading" : answered ? "reveal" : atEnd ? "decision" : "playing";

  const skip = () => {
    setPlaying(false);
    setIdx(Math.max(0, n - 1));
  };

  // Play the drill again from the lead start; before the answer this also clears the draft.
  const restart = () => {
    if (!drill || n === 0) return;
    const cut = Date.parse(drill.pause_at);
    const pre = candles.filter((c) => Date.parse(c.ts) < cut).length;
    if (!answered) {
      setDraft(EMPTY_DRAFT);
      setArmed(null);
    }
    setIdx(startIndex(pre, barsFor(LEAD_MINUTES, timeframe)));
    setPlaying(true);
  };

  // Keyboard: ArrowRight steps one bar, Space plays or pauses, End skips ahead.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isInputFocused(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (phase === "decision" || phase === "loading") return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        setPlaying(false);
        setIdx((i) => stepIndex(i, n));
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setPlaying(false);
        setIdx((i) => Math.max(0, i - 1));
      } else if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "End") {
        e.preventDefault();
        setPlaying(false);
        setIdx(Math.max(0, n - 1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, n]);

  const pick = phase === "decision" ? chartPickFor(armed && requiredSteps(drill?.question_steps ?? [], draft.enter).includes(armed) ? armed : currentStep(drill?.question_steps ?? [], draft)) : null;

  const onPriceClick = useMemo(() => {
    if (!pick) return undefined;
    return (price: number) => {
      const p = roundPrice(price);
      setDraft((d) => ({ ...d, [pick]: p }));
      setArmed(null);
    };
  }, [pick]);

  // Drag a line you already set: decision phase only (after the answer the lines are fixed).
  const onMarkDrag = useMemo(() => {
    if (phase !== "decision") return undefined;
    return (key: string, price: number) => {
      if (key !== "level" && key !== "stop" && key !== "target") return;
      setDraft((d) => ({ ...d, [key]: roundPrice(price) }));
    };
  }, [phase]);

  // The chart's bars: the revealed 1m tape (up to the playhead) rolled up into the chosen bar
  // size, buckets aligned to the open. Auto follows KCU: 2m, then 5m, then 10m.
  const anchorSec = drill ? floorMinute(Date.parse(drill.bars_from) / 1000) : 0;
  const playMinSec = n > 0 ? floorMinute(isoToUtcSeconds(candles[Math.min(idx, n - 1)].ts)) : 0;
  const stepMin = tfChoice === "auto" ? kcuTimeframe((playMinSec - anchorSec) / 60) : tfChoice;
  const revealed = useMemo(() => candles.slice(0, Math.min(idx, n - 1) + 1), [candles, idx, n]);
  const chartBars = useMemo(() => aggregateBars(revealed, stepMin, anchorSec), [revealed, stepMin, anchorSec]);
  const levels = useMemo(
    () => (showHourly ? hourlyLevels(revealed, anchorSec, playMinSec) : []),
    [showHourly, revealed, anchorSec, playMinSec],
  );

  // After the answer: an arrow and IN line on the bar the key enters on (the first bar after the
  // pause), so the reveal shows where the trade was taken. Nothing for a "wait" key.
  const keyEntry: ReplayFill[] = useMemo(() => {
    const k = grade?.answer_key;
    if (!drill || !answered || !k || k.enter !== "enter") return [];
    const at = bucketStart(floorMinute(Date.parse(drill.pause_at) / 1000), stepMin * 60, anchorSec);
    return [
      {
        seq: 1,
        role: "entry",
        side: k.side === "short" ? "sell" : "buy",
        qty: 0,
        price: k.level ?? 0,
        ts: new Date(at * 1000).toISOString(),
        time_pt: ptTime(drill.pause_at),
      },
    ];
  }, [drill, answered, grade, stepMin, anchorSec]);

  const bottom: PaneSpec | null = useMemo(
    () =>
      drill && n > 0
        ? {
            label: `${drill.ticker} ${tfLabel(stepMin)}${tfChoice === "auto" ? " · auto" : ""}`,
            candles: chartBars,
            levels,
            marks: buildMarks(phase, draft, grade),
          }
        : null,
    [drill, n, chartBars, levels, stepMin, tfChoice, phase, draft, grade],
  );

  const back = () => navigate("/learn");

  if (drillQ.isLoading) return <PageFrame><Skeleton className="h-96 w-full" /></PageFrame>;
  if (drillQ.data && isNotConfigured(drillQ.data))
    return (
      <PageFrame>
        <Alert>
          <FolderOpen className="h-4 w-4" />
          <AlertTitle>The learn folder is not set up</AlertTitle>
          <AlertDescription>
            Set <code className="num">TRADEDESK_LEARN_DIR</code> and restart the backend to use drills.
          </AlertDescription>
        </Alert>
        <BackButton onClick={back} />
      </PageFrame>
    );
  if (drillQ.isError || !drill)
    return (
      <PageFrame>
        <Alert variant="destructive">
          <ServerCrash className="h-4 w-4" />
          <AlertTitle>{drillQ.error instanceof ApiError && drillQ.error.status === 404 ? "Drill not found" : "Could not load the drill"}</AlertTitle>
          <AlertDescription>It may have been replaced by a newer set. Go back and pick one from today&apos;s list.</AlertDescription>
        </Alert>
        <BackButton onClick={back} />
      </PageFrame>
    );

  const todayDrills = today.data && !isNotConfigured(today.data) ? today.data.drills : [];
  const next = nextUndone(todayDrills, drill.id);
  const stepSec = stepMin * 60;
  const playheadSec = n > 0 ? bucketStart(playMinSec, stepSec, anchorSec) : 0;
  const startSec = n > 0 ? bucketStart(isoToUtcSeconds(candles[0].ts), stepSec, anchorSec) : 0;
  const endSec = n > 0 ? bucketStart(isoToUtcSeconds(candles[n - 1].ts), stepSec, anchorSec) : 0;
  const phaseLabel =
    phase === "loading" ? "Loading" : phase === "playing" ? "Watching" : phase === "decision" ? "Your call" : atEnd ? "Review" : "Playing the rest";

  const onSubmit = () => {
    answer.mutate(toAnswerRequest(draft), {
      onSuccess: (res) => {
        setSubmittedGrade(res);
        setPlaying(true);
      },
    });
  };
  const submitError =
    answer.error instanceof ApiError
      ? (answer.error.detail ?? answer.error.message)
      : answer.error
        ? "Something went wrong. Try again."
        : null;

  return (
    <PageFrame>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="ghost" size="sm" onClick={back}>
            <ArrowLeft /> Back to Learn
          </Button>
          <h1 className="text-value font-semibold">
            <span className="num">{drill.ticker}</span>
            <span className="text-muted-foreground"> · {topicName(drill.topic)}</span>
          </h1>
          {drill.review && <Badge variant="outline">Review</Badge>}
        </div>
        <p className="text-label text-muted-foreground">
          Session {shortDate(drill.date)} · pauses at {ptTime(drill.pause_at)} · {phaseLabel}
          {answered && drill.score != null && !grade ? ` · score ${scoreText(drill.score)}` : ""}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="flex flex-col gap-2">
          <div className="h-[30rem] overflow-hidden rounded-lg border border-border bg-card lg:h-[36rem]" data-testid="drill-chart">
            {bottom ? (
              <PriceChart
                top={null}
                bottom={bottom}
                startSec={startSec}
                endSec={endSec}
                playheadSec={playheadSec}
                mode={{ kind: "replay" }}
                stopPrice={null}
                fills={keyEntry}
                onPriceClick={onPriceClick}
                onMarkDrag={onMarkDrag}
                tradingView
                barSec={stepSec}
                initialBars={Math.max(24, barsFor(VIEW_MINUTES, tfLabel(stepMin)))}
              />
            ) : (
              <div className="p-4">
                <Skeleton className="h-full min-h-72 w-full" />
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={phase === "loading" || phase === "decision" || atEnd}
              onClick={() => setPlaying((p) => !p)}
            >
              {playing && !atEnd ? <Pause /> : <Play />}
              {playing && !atEnd ? "Pause" : "Play"}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={phase === "loading" || phase === "decision" || atEnd}
              onClick={() => {
                setPlaying(false);
                setIdx((i) => stepIndex(i, n));
              }}
            >
              <StepForward /> Step
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={phase === "loading" || phase === "decision" || atEnd}
              onClick={skip}
            >
              <FastForward /> Skip
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={phase === "loading"} onClick={restart}>
              <RotateCcw /> {answered ? "Rewind" : "Restart"}
            </Button>
            <div className="flex items-center gap-1" role="group" aria-label="Timeframe">
              {(["auto", ...TIMEFRAMES] as TfChoice[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  aria-pressed={tfChoice === t}
                  onClick={() => setTfChoice(t)}
                  title={t === "auto" ? "KCU: 2m for the first 30 min, 5m for the next hour, then 10m" : undefined}
                  className={
                    "num rounded-md border px-1.5 py-0.5 text-label transition-colors " +
                    (tfChoice === t ? "border-ring text-foreground" : "border-border text-muted-foreground hover:bg-muted")
                  }
                >
                  {t === "auto" ? "Auto" : tfLabel(t)}
                </button>
              ))}
              <button
                type="button"
                aria-pressed={showHourly}
                onClick={() => setShowHourly((v) => !v)}
                className={
                  "num rounded-md border px-1.5 py-0.5 text-label transition-colors " +
                  (showHourly ? "border-ring text-foreground" : "border-border text-muted-foreground hover:bg-muted")
                }
              >
                1h levels
              </button>
            </div>
            <div className="flex items-center gap-1" role="group" aria-label="Playback speed">
              {PLAY_SPEEDS.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={speed === s}
                  onClick={() => setSpeed(s)}
                  className={
                    "num rounded-md border px-1.5 py-0.5 text-label transition-colors " +
                    (speed === s ? "border-ring text-foreground" : "border-border text-muted-foreground hover:bg-muted")
                  }
                >
                  {s}/s
                </button>
              ))}
            </div>
            <span className="num text-label text-muted-foreground">
              {n > 0 ? `Bar ${idx + 1} of ${n}, ${ptTime(candles[Math.min(idx, n - 1)].ts)}` : ""}
              {fullBarsLoading ? " · loading the rest" : ""}
            </span>
            <span className="ml-auto hidden text-label text-muted-foreground md:inline">
              ArrowLeft / ArrowRight step · Space plays or pauses · End skips
            </span>
          </div>
        </div>

        <Card className="self-start">
          <CardContent className="p-4">
            {phase === "decision" ? (
              <DecisionPanel
                drill={drill}
                draft={draft}
                setDraft={setDraft}
                armed={armed}
                setArmed={setArmed}
                submitting={answer.isPending}
                error={submitError}
                onSubmit={onSubmit}
              />
            ) : phase === "reveal" ? (
              <RevealPanel
                grade={grade}
                drillLessons={drill.lessons}
                onBack={back}
                onNext={next ? () => navigate(`/learn/drills/${encodeURIComponent(next.id)}`) : null}
              />
            ) : (
              <div className="flex flex-col gap-1">
                <h2 className="text-value font-semibold">{phase === "loading" ? "Loading bars" : "Watch the tape"}</h2>
                <p className="text-body text-muted-foreground">
                  {phase === "loading"
                    ? "Fetching the session."
                    : `Playback starts ${LEAD_MINUTES} minutes before the pause; everything earlier is already on the chart. Skip when you've seen enough.`}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </PageFrame>
  );
}

function PageFrame({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 p-6">{children}</div>;
}

function BackButton({ onClick }: { onClick: () => void }) {
  return (
    <div>
      <Button type="button" variant="outline" onClick={onClick}>
        <ArrowLeft /> Back to Learn
      </Button>
    </div>
  );
}
