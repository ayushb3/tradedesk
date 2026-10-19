/**
 * ProgressPage — /training/progress
 *
 * Gamification surface for the training module (M6 task 6.4.2).
 *
 * Displays:
 *   - Discipline streak cards (process_streak + longest_process_streak).
 *     Labels reward rule-following, NEVER profit. No P&L, no dollar signs.
 *   - Per-archetype mastery: 4-dot level meter (level 0-3 or unattempted),
 *     level_label, attempts_count, rolling_mean_score.
 *   - Due-review queue: SM-2 resurface cards. "Review now" creates a fresh
 *     synthetic scenario (Option B — different seed each review cycle) and
 *     navigates into the drill player.
 *   - Weekly quests: progress bars (progress / target) with checkmark when done.
 *
 * Anti-gambling guarantee: outcome_pnl is NEVER read, displayed, or rewarded
 * on this page. The C4 audit (6.4.3) greps the frontend for 'outcome_pnl' and
 * 'dollar' and should find nothing on this page.
 *
 * Consumes: GET /api/training/progress → ProgressReport
 * Due-review "Review now": POST /api/training/scenarios (synthetic), then
 * navigate to /training/drills/:id (Option B per PLAN §6 C4 acceptance).
 */

import { useState } from "react";
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  Clock,
  Flame,
  ServerCrash,
  TrendingUp,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { createSyntheticScenario, useProgress } from "@/lib/drills";
import type { ArchetypeMastery, DueReview, Quest } from "@/lib/trainingTypes";

// --------------------------------------------------------------------------- //
// Helpers
// --------------------------------------------------------------------------- //

function archetypeDisplayName(arch: string): string {
  const names: Record<string, string> = {
    "gap-and-go": "Gap & Go",
    "failed-breakout": "Failed Breakout",
    "chop": "Chop",
    "trend-day": "Trend Day",
    "custom": "Custom",
  };
  return names[arch] ?? arch;
}

function formatRelativeDate(isoString: string): string {
  try {
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return "today";
    if (diffDays === 1) return "yesterday";
    return `${diffDays} days ago`;
  } catch {
    return isoString;
  }
}

function formatScore(score: number | null): string {
  if (score === null) return "—";
  return Math.round(score * 100).toString();
}

// --------------------------------------------------------------------------- //
// Streak cards
// --------------------------------------------------------------------------- //

function StreakCards({
  current,
  longest,
}: {
  current: number;
  longest: number;
}) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-2">
      <Card>
        <CardContent className="flex flex-col items-center justify-center gap-1.5 py-6">
          <div className="flex items-center gap-1.5">
            <Flame className="h-5 w-5 text-orange-400" />
            <span className="text-3xl font-bold tabular-nums">{current}</span>
          </div>
          <p className="text-center text-xs text-muted-foreground">
            drills in a row following your rules
          </p>
          <p className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
            Discipline streak
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col items-center justify-center gap-1.5 py-6">
          <div className="flex items-center gap-1.5">
            <TrendingUp className="h-5 w-5 text-primary/70" />
            <span className="text-3xl font-bold tabular-nums">{longest}</span>
          </div>
          <p className="text-center text-xs text-muted-foreground">
            your best run
          </p>
          <p className="text-label font-semibold uppercase tracking-widest text-muted-foreground/60">
            Longest streak
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Mastery level dots
// --------------------------------------------------------------------------- //

/**
 * 4-dot meter: filled dots = level (0-3). Unattempted = all empty, greyed out.
 * level null → unattempted.
 */
function LevelDots({ level }: { level: number | null }) {
  const filled = level ?? 0;
  const isUnattempted = level === null;

  return (
    <span
      className="flex items-center gap-0.5"
      aria-label={level === null ? "Unattempted" : `Level ${level} of 3`}
    >
      {Array.from({ length: 4 }).map((_, i) => (
        <span
          key={i}
          className={[
            "h-2.5 w-2.5 rounded-full",
            isUnattempted
              ? "bg-muted-foreground/20"
              : i < filled
                ? "bg-primary"
                : "bg-muted-foreground/25",
          ].join(" ")}
        />
      ))}
    </span>
  );
}

function levelBadgeVariant(label: string): "default" | "outline" | "muted" {
  if (label === "mastered") return "default";
  if (label === "proficient") return "outline";
  return "muted";
}

// --------------------------------------------------------------------------- //
// Mastery list
// --------------------------------------------------------------------------- //

function MasteryRow({ mastery }: { mastery: ArchetypeMastery }) {
  const isUnattempted = mastery.level === null;

  return (
    <div
      className={[
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-1 rounded-md px-3 py-2.5",
        isUnattempted ? "opacity-50" : "",
      ].join(" ")}
    >
      <div className="flex items-center gap-3">
        <LevelDots level={mastery.level} />
        <span className="text-sm font-medium">
          {archetypeDisplayName(mastery.archetype)}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={levelBadgeVariant(mastery.level_label)}>
          {mastery.level_label}
        </Badge>

        {!isUnattempted && (
          <span className="text-xs text-muted-foreground">
            {mastery.attempts_count}{" "}
            {mastery.attempts_count === 1 ? "attempt" : "attempts"},{" "}
            avg {formatScore(mastery.rolling_mean_score)}
          </span>
        )}
      </div>
    </div>
  );
}

function MasterySection({ mastery }: { mastery: ArchetypeMastery[] }) {
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">Mastery by archetype</h2>
      <Card>
        <CardContent className="divide-y divide-border px-0 py-1">
          {mastery.map((m) => (
            <MasteryRow key={m.archetype} mastery={m} />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Due-review queue
// --------------------------------------------------------------------------- //

function DueReviewCard({ review }: { review: DueReview }) {
  const navigate = useNavigate();
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function handleReviewNow() {
    setIsCreating(true);
    setCreateError(null);
    try {
      const title = `${archetypeDisplayName(review.archetype)} — review #${review.review_count + 1}`;
      const scenario = await createSyntheticScenario({
        archetype: review.archetype,
        synth_seed: review.suggested_seed,
        title,
      });
      navigate(`/training/drills/${scenario.id}`);
    } catch (err) {
      setCreateError(
        err instanceof ApiError
          ? (err.detail ?? err.message)
          : "Could not create review drill.",
      );
      setIsCreating(false);
    }
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-sm font-medium">
              {archetypeDisplayName(review.archetype)}
            </p>
            <p className="text-xs text-muted-foreground">
              failed {formatRelativeDate(review.failed_at)} &mdash; process
              score {formatScore(review.last_score)}
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            review #{review.review_count + 1}
          </div>
        </div>

        {createError && (
          <p className="text-xs text-rose-500">{createError}</p>
        )}

        <Button
          size="sm"
          onClick={handleReviewNow}
          disabled={isCreating}
          className="w-fit gap-1.5"
        >
          <BookOpen className="h-3.5 w-3.5" />
          {isCreating ? "Creating drill…" : "Review now → fresh tape"}
        </Button>
      </CardContent>
    </Card>
  );
}

function DueReviewSection({ reviews }: { reviews: DueReview[] }) {
  if (reviews.length === 0) {
    return (
      <div>
        <h2 className="mb-2 text-sm font-semibold">Due for review</h2>
        <p className="text-sm text-muted-foreground">
          Nothing due right now. Keep drilling — failed archetypes resurface
          automatically.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h2 className="mb-1 text-sm font-semibold">Due for review</h2>
      <p className="mb-3 text-xs text-muted-foreground">
        Spaced repetition resurfaces what you struggled with, using a fresh
        tape each time.
      </p>
      <div className="space-y-3">
        {reviews.map((r) => (
          <DueReviewCard key={r.archetype} review={r} />
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Quest progress bar
// --------------------------------------------------------------------------- //

function QuestRow({ quest }: { quest: Quest }) {
  const pct = quest.target > 0
    ? Math.min(100, Math.round((quest.progress / quest.target) * 100))
    : 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm">{quest.label}</span>
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-xs text-muted-foreground tabular-nums">
            {quest.progress}/{quest.target}
          </span>
          {quest.done && (
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
          )}
        </div>
      </div>
      {/* Simple div-based progress meter — no external component needed */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={[
            "h-full rounded-full transition-all",
            quest.done ? "bg-emerald-500" : "bg-primary",
          ].join(" ")}
          style={{ width: `${pct}%` }}
          role="progressbar"
          aria-valuenow={quest.progress}
          aria-valuemin={0}
          aria-valuemax={quest.target}
          aria-label={quest.label}
        />
      </div>
    </div>
  );
}

function QuestsSection({ quests }: { quests: Quest[] }) {
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">This week&apos;s quests</h2>
      <Card>
        <CardContent className="space-y-4 py-4">
          {quests.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No quests this week.
            </p>
          ) : (
            quests.map((q) => <QuestRow key={q.key} quest={q} />)
          )}
        </CardContent>
      </Card>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Loading skeleton
// --------------------------------------------------------------------------- //

function ProgressSkeleton() {
  return (
    <div className="space-y-6">
      {/* Streak cards */}
      <div className="grid grid-cols-2 gap-4">
        {[0, 1].map((i) => (
          <Card key={i}>
            <CardContent className="flex flex-col items-center gap-2 py-6">
              <Skeleton className="h-8 w-16" />
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-2 w-24" />
            </CardContent>
          </Card>
        ))}
      </div>
      {/* Mastery */}
      <div className="space-y-2">
        <Skeleton className="h-4 w-40" />
        <Card>
          <CardContent className="divide-y divide-border py-1 px-0">
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="flex items-center justify-between px-3 py-2.5"
              >
                <div className="flex items-center gap-3">
                  <Skeleton className="h-2.5 w-12" />
                  <Skeleton className="h-4 w-28" />
                </div>
                <Skeleton className="h-4 w-20 rounded-full" />
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Error state
// --------------------------------------------------------------------------- //

function ProgressError({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const isBackendDown = status === 0;
  const notImplemented = status === 501;

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
            ? "Progress module not ready yet"
            : "Failed to load progress"}
      </AlertTitle>
      <AlertDescription>
        {isBackendDown ? (
          <>
            Start it with{" "}
            <code className="rounded bg-muted px-1 py-0.5">make backend</code>.
          </>
        ) : notImplemented ? (
          <>
            The mastery model (M6 quant task 6.4.1) hasn&apos;t been implemented yet.
            Come back once it lands.
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
// Empty state (no drills taken yet)
// --------------------------------------------------------------------------- //

function EmptyProgress() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col items-center gap-4 py-16 text-center">
      <TrendingUp className="h-10 w-10 text-muted-foreground/30" />
      <div>
        <p className="font-medium text-muted-foreground">
          No drills completed yet
        </p>
        <p className="mt-1 text-sm text-muted-foreground/70">
          Complete your first drill to start tracking your mastery and
          discipline streak.
        </p>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={() => navigate("/training/drills")}
        className="gap-1.5"
      >
        <BookOpen className="h-3.5 w-3.5" />
        Take your first drill
      </Button>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //

export function ProgressPage() {
  const { data: report, isLoading, isError, error } = useProgress();

  const hasAnyAttempts =
    report !== undefined &&
    report.mastery.some((m) => m.attempts_count > 0);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <TrendingUp className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-lg font-semibold">Your progress</h1>
            <p className="text-xs text-muted-foreground">
              Mastery tracks rule-following, not profits.
            </p>
          </div>
        </div>
        {report?.generated_at && (
          <p className="shrink-0 text-xs text-muted-foreground">
            Updated {formatRelativeDate(report.generated_at)}
          </p>
        )}
      </div>

      {/* Loading */}
      {isLoading && <ProgressSkeleton />}

      {/* Error */}
      {isError && <ProgressError error={error} />}

      {/* Empty state */}
      {!isLoading && !isError && report && !hasAnyAttempts && (
        <>
          <EmptyProgress />
          <MasterySection mastery={report.mastery} />
        </>
      )}

      {/* Content */}
      {!isLoading && !isError && report && hasAnyAttempts && (
        <div className="space-y-6">
          <StreakCards
            current={report.process_streak}
            longest={report.longest_process_streak}
          />
          <MasterySection mastery={report.mastery} />
          <DueReviewSection reviews={report.due_reviews} />
          <QuestsSection quests={report.quests} />
        </div>
      )}
    </div>
  );
}
