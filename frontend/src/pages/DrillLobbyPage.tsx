/**
 * DrillLobbyPage — /training/drills
 *
 * Lists active training scenarios so the user can pick one to take as a drill.
 * Each card shows: title, archetype badge, drill_type badge, blind indicator,
 * and a "Start drill" button that opens a mood check-in prompt before navigating
 * to /training/drills/:scenarioId?mood=<value>.
 *
 * Mood threading: Option A (URL query param) chosen over context/store because
 * there is no existing global state pattern in this codebase and a query param
 * keeps the value visible, refresh-safe, and trivial to consume in the player.
 */

import { useState } from "react";
import { AlertCircle, BookOpen, EyeOff, ServerCrash, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

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
import { ApiError } from "@/lib/api";
import { useActiveScenarios } from "@/lib/drills";
import type {
  Archetype,
  DrillType,
  MoodCheckin,
  ScenarioPublic,
} from "@/lib/trainingTypes";

// --------------------------------------------------------------------------- //
// Label helpers
// --------------------------------------------------------------------------- //

function archetypeLabel(a: Archetype): string {
  const labels: Record<Archetype, string> = {
    "gap-and-go": "Gap & Go",
    "failed-breakout": "Failed BO",
    "chop": "Chop",
    "trend-day": "Trend Day",
    "custom": "Custom",
  };
  return labels[a] ?? a;
}

function drillTypeLabel(t: DrillType): string {
  const labels: Record<DrillType, string> = {
    "decision-point": "Decision Point",
    "management-only": "Management",
    "do-nothing": "Do Nothing",
    "spot-setup": "Spot Setup",
    "drawdown-script": "Drawdown Script",
  };
  return labels[t] ?? t;
}

// --------------------------------------------------------------------------- //
// Mood check-in overlay
// --------------------------------------------------------------------------- //

const MOOD_OPTIONS: { value: MoodCheckin; label: string; description: string }[] = [
  { value: "calm", label: "Calm", description: "Focused, no pressure" },
  { value: "rushed", label: "Rushed", description: "Feeling hurried or impatient" },
  { value: "frustrated", label: "Frustrated", description: "Annoyed or coming off a loss" },
];

function MoodCheckinOverlay({
  scenarioId,
  onClose,
}: {
  scenarioId: number;
  onClose: () => void;
}) {
  const navigate = useNavigate();

  function go(mood: MoodCheckin | null) {
    const path = `/training/drills/${scenarioId}`;
    if (mood) {
      navigate(`${path}?mood=${mood}`);
    } else {
      navigate(path);
    }
  }

  return (
    // Backdrop
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <Card className="relative w-full max-w-sm">
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="absolute right-3 top-3 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>

        <CardHeader className="pb-2">
          <CardTitle>How are you feeling right now?</CardTitle>
          <CardDescription>
            Your mood is logged alongside drill performance to surface patterns.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-2 pt-0">
          {MOOD_OPTIONS.map(({ value, label, description }) => (
            <button
              key={value}
              type="button"
              onClick={() => go(value)}
              className="flex w-full items-center gap-3 rounded-md border border-border bg-background px-4 py-3 text-left transition-colors hover:border-primary/60 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="font-medium text-sm">{label}</span>
              <span className="text-xs text-muted-foreground">{description}</span>
            </button>
          ))}

          <button
            type="button"
            onClick={() => go(null)}
            className="w-full pt-1 text-center text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            Skip check-in
          </button>
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Scenario card
// --------------------------------------------------------------------------- //

function ScenarioCard({
  scenario,
  onStartDrill,
}: {
  scenario: ScenarioPublic;
  onStartDrill: (id: number) => void;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle className="text-sm leading-snug">{scenario.title}</CardTitle>
          {scenario.blind_default && (
            <span
              className="flex items-center gap-1 text-label text-muted-foreground"
              title="Ticker and date are hidden in this drill"
            >
              <EyeOff className="h-3 w-3" />
              Blind
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5 pt-1">
          <Badge variant="default">{archetypeLabel(scenario.archetype)}</Badge>
          <Badge variant="outline">{drillTypeLabel(scenario.drill_type)}</Badge>
          {scenario.source_type !== "real" && (
            <Badge variant="muted">{scenario.source_type}</Badge>
          )}
        </div>
        {(scenario.symbol || scenario.session_date) && (
          <CardDescription className="pt-0.5">
            {scenario.symbol}
            {scenario.symbol && scenario.session_date ? " · " : ""}
            {scenario.session_date}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="mt-auto pt-0">
        <Button
          className="w-full"
          size="sm"
          onClick={() => onStartDrill(scenario.id)}
        >
          Start drill
        </Button>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Loading skeleton
// --------------------------------------------------------------------------- //

function LobbySkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} className="flex flex-col">
          <CardHeader className="pb-2">
            <Skeleton className="h-4 w-3/4" />
            <div className="flex gap-1.5 pt-2">
              <Skeleton className="h-4 w-20 rounded-full" />
              <Skeleton className="h-4 w-24 rounded-full" />
            </div>
          </CardHeader>
          <CardContent className="mt-auto pt-0">
            <Skeleton className="h-8 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Error state
// --------------------------------------------------------------------------- //

function LobbyError({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const isBackendDown = status === 0;
  const notImplemented = status === 501 || status === 404;

  return (
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
            ? "Training module not ready yet"
            : "Failed to load scenarios"}
      </AlertTitle>
      <AlertDescription>
        {isBackendDown ? (
          <>
            Start it with{" "}
            <code className="tabular rounded bg-muted px-1 py-0.5">
              make backend
            </code>
            .
          </>
        ) : notImplemented ? (
          <>
            The training-module backend (M6 data-eng task 6.0.2) hasn't landed
            yet. Scenarios will appear here once it's implemented.
          </>
        ) : (
          error instanceof ApiError
            ? (error.detail ?? error.message)
            : String(error)
        )}
      </AlertDescription>
    </Alert>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

export function DrillLobbyPage() {
  const { data: scenarios, isLoading, isError, error } = useActiveScenarios();
  // scenarioId waiting for mood check-in; null = no overlay open
  const [pendingScenarioId, setPendingScenarioId] = useState<number | null>(null);

  return (
    <>
      {/* Mood check-in overlay — shown when user clicks "Start drill" */}
      {pendingScenarioId !== null && (
        <MoodCheckinOverlay
          scenarioId={pendingScenarioId}
          onClose={() => setPendingScenarioId(null)}
        />
      )}

      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <BookOpen className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-lg font-semibold">Drills</h1>
            <p className="text-xs text-muted-foreground">
              Pick a scenario and practice your decision-making process.
            </p>
          </div>
        </div>

        {/* Content */}
        {isLoading && <LobbySkeleton />}
        {isError && <LobbyError error={error} />}
        {!isLoading && !isError && scenarios && scenarios.length === 0 && (
          <div className="flex flex-col items-center gap-4 py-16 text-center">
            <BookOpen className="h-10 w-10 text-muted-foreground/40" />
            <div>
              <p className="font-medium text-muted-foreground">No drills yet</p>
              <p className="mt-1 text-sm text-muted-foreground/70">
                Scenarios authored via the debrief interview or authoring UI will
                appear here.
              </p>
            </div>
          </div>
        )}
        {!isLoading && !isError && scenarios && scenarios.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {scenarios.map((s) => (
              <ScenarioCard
                key={s.id}
                scenario={s}
                onStartDrill={(id) => setPendingScenarioId(id)}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
