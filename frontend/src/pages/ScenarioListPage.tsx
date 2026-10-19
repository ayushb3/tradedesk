/**
 * ScenarioListPage — /training/scenarios
 *
 * Unified entry point for both scenario management and drill-taking.
 *
 * Each row shows: title, archetype, drill type, symbol/date, blind flag, created
 * date — plus a two-icon action group:
 *   - Pencil (Edit)  → /training/scenarios/new  (authoring view; follow-up will
 *                       support /training/scenarios/:id/edit once backend ships)
 *   - Play triangle  → mood check-in overlay → /training/drills/:id?mood=<value>
 *
 * The mood check-in overlay is lifted in from what was DrillLobbyPage.
 * DrillLobbyPage (/training/drills) now redirects here; the drill player route
 * /training/drills/:scenarioId is unchanged.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertCircle,
  BookMarked,
  EyeOff,
  Pencil,
  Play,
  PlusCircle,
  ServerCrash,
  X,
} from "lucide-react";

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ARCHETYPE_LABELS,
  DRILL_TYPE_LABELS,
  useScenarios,
  type ScenarioPublic,
} from "@/lib/training";
import type { MoodCheckin } from "@/lib/trainingTypes";

// ---------------------------------------------------------------------------
// Mood check-in overlay (lifted from DrillLobbyPage)
// ---------------------------------------------------------------------------

const MOOD_OPTIONS: { value: MoodCheckin; label: string; description: string }[] = [
  { value: "calm", label: "Calm", description: "Focused, no pressure" },
  { value: "rushed", label: "Rushed", description: "Feeling hurried or impatient" },
  {
    value: "frustrated",
    label: "Frustrated",
    description: "Annoyed or coming off a loss",
  },
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
    navigate(mood ? `${path}?mood=${mood}` : path);
  }

  return (
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
          aria-label="Close mood check-in"
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

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ScenarioListPage() {
  const navigate = useNavigate();
  const { data, isLoading, isError, error } = useScenarios();
  // scenarioId waiting for mood check-in; null = overlay closed
  const [pendingScenarioId, setPendingScenarioId] = useState<number | null>(null);

  return (
    <>
      {/* Mood check-in overlay — shown when user clicks the Play icon */}
      {pendingScenarioId !== null && (
        <MoodCheckinOverlay
          scenarioId={pendingScenarioId}
          onClose={() => setPendingScenarioId(null)}
        />
      )}

      <div className="flex h-full flex-col">
        {/* Header */}
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
          <div className="flex items-center gap-2">
            <BookMarked className="h-4 w-4 text-primary" />
            <span className="font-semibold">Training Scenarios</span>
          </div>
          <Button
            size="sm"
            onClick={() => navigate("/training/scenarios/new")}
            className="gap-1.5"
          >
            <PlusCircle className="h-4 w-4" />
            New Scenario
          </Button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-4">
          {isLoading && <ListSkeleton />}
          {isError && <ListError error={error} />}
          {!isLoading && !isError && (
            <>
              {!data || data.length === 0 ? (
                <EmptyState onNew={() => navigate("/training/scenarios/new")} />
              ) : (
                <ScenarioTable
                  scenarios={data}
                  onEdit={() => navigate(`/training/scenarios/new`)}
                  onPlay={(s) => setPendingScenarioId(s.id)}
                />
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Scenario table with Edit + Play actions
// ---------------------------------------------------------------------------

function ScenarioTable({
  scenarios,
  onEdit,
  onPlay,
}: {
  scenarios: ScenarioPublic[];
  onEdit: (scenario: ScenarioPublic) => void;
  onPlay: (scenario: ScenarioPublic) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Title</TableHead>
          <TableHead>Archetype</TableHead>
          <TableHead>Drill Type</TableHead>
          <TableHead>Symbol / Date</TableHead>
          <TableHead>Blind</TableHead>
          <TableHead>Created</TableHead>
          <TableHead className="w-20 text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {scenarios.map((s) => (
          <TableRow key={s.id}>
            <TableCell className="font-medium">{s.title}</TableCell>
            <TableCell>
              <Badge variant="outline">
                {ARCHETYPE_LABELS[s.archetype] ?? s.archetype}
              </Badge>
            </TableCell>
            <TableCell>
              <Badge variant="muted">
                {DRILL_TYPE_LABELS[s.drill_type] ?? s.drill_type}
              </Badge>
            </TableCell>
            <TableCell className="tabular text-sm text-muted-foreground">
              {s.symbol && s.session_date ? (
                <>
                  {s.symbol} · {s.session_date}
                </>
              ) : s.blind_default ? (
                <span className="flex items-center gap-1 text-muted-foreground/70">
                  <EyeOff className="h-3 w-3" />
                  <span className="italic text-xs">hidden</span>
                </span>
              ) : (
                <span className="italic text-xs">—</span>
              )}
            </TableCell>
            <TableCell>
              {s.blind_default ? (
                <Badge variant="default">Blind</Badge>
              ) : (
                <span className="text-xs text-muted-foreground">No</span>
              )}
            </TableCell>
            <TableCell className="tabular text-xs text-muted-foreground">
              {formatCreatedAt(s.created_at)}
            </TableCell>
            <TableCell className="text-right">
              <div className="flex items-center justify-end gap-1">
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 text-muted-foreground hover:text-foreground"
                  title="Edit scenario"
                  onClick={() => onEdit(s)}
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7 text-muted-foreground hover:text-primary"
                  title="Play drill"
                  onClick={() => onPlay(s)}
                >
                  <Play className="h-3.5 w-3.5" />
                </Button>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Utilities and minor sub-components
// ---------------------------------------------------------------------------

function formatCreatedAt(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 pt-16 text-center">
      <p className="text-sm text-muted-foreground">
        No scenarios yet. Author one from a replay session.
      </p>
      <Button size="sm" onClick={onNew} className="gap-1.5">
        <PlusCircle className="h-4 w-4" />
        New Scenario
      </Button>
    </div>
  );
}

function ListError({ error }: { error: unknown }) {
  const status =
    error &&
    typeof error === "object" &&
    "status" in error &&
    typeof (error as { status: unknown }).status === "number"
      ? (error as { status: number }).status
      : undefined;
  const isBackendDown = status === 0;
  const notImplemented = status === 501 || status === 404;
  const message =
    error instanceof Error ? error.message : "Could not load scenarios.";

  return (
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
            ? "Training endpoint not ready yet"
            : "Could not load scenarios"}
      </AlertTitle>
      <AlertDescription>
        {isBackendDown ? (
          <>
            The data service isn't reachable. Start it, then reload.
          </>
        ) : notImplemented ? (
          <>
            <code className="tabular rounded bg-muted px-1 py-0.5">
              /api/training/scenarios
            </code>{" "}
            returns 501 — the training store (data-eng task 6.0.2) isn't wired
            yet. The UI is ready; check back once that task lands.
          </>
        ) : (
          message
        )}
      </AlertDescription>
    </Alert>
  );
}
