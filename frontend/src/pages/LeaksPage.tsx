/**
 * LeaksPage — /training/leaks
 *
 * Consumes GET /api/training/leaks and renders each detected behavioral leak
 * as a card with:
 *   - Severity dot indicator (1 = grey, 2 = amber, 3 = red)
 *   - Name + description
 *   - Prescribed drill titles (resolved via individual useScenario hooks)
 *   - Expandable evidence section (trade IDs, attempt IDs, debrief IDs)
 *   - "Start playlist" button — navigates to the first drill with
 *     ?playlist=remaining-ids&mood=<preserved-if-set> in the URL
 *
 * Playlist mechanic: URL-driven (no local storage, no context).
 * See DrillPlayerPage for the full playlist walker contract.
 */

import { useState } from "react";
import { AlertCircle, AlertTriangle, ChevronDown, ChevronRight, List, ServerCrash, Sparkles } from "lucide-react";
import { useNavigate } from "react-router-dom";

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
import { ApiError } from "@/lib/api";
import { useAgentStatus } from "@/lib/queries";
import { useLeaks, useScenario } from "@/lib/drills";
import type { Leak, LeakEvidence } from "@/lib/trainingTypes";

// --------------------------------------------------------------------------- //
// Severity dot indicator
// --------------------------------------------------------------------------- //

function SeverityDots({ severity }: { severity: 1 | 2 | 3 }) {
  // severity 3 = red triplet, 2 = amber pair, 1 = single grey
  const dotClass =
    severity === 3
      ? "bg-rose-500"
      : severity === 2
        ? "bg-amber-400"
        : "bg-muted-foreground/40";

  return (
    <span className="flex items-center gap-0.5" aria-label={`Severity ${severity}`}>
      {Array.from({ length: severity }).map((_, i) => (
        <span key={i} className={`h-2 w-2 rounded-full ${dotClass}`} />
      ))}
    </span>
  );
}

// --------------------------------------------------------------------------- //
// Prescribed drill title: one hook per scenario id
// --------------------------------------------------------------------------- //

function ScenarioTitle({ scenarioId }: { scenarioId: number }) {
  const { data, isLoading, isError } = useScenario(scenarioId);

  if (isLoading) {
    return <Skeleton className="inline-block h-3 w-32 align-middle" />;
  }
  if (isError || !data) {
    return <span className="text-muted-foreground">Scenario #{scenarioId}</span>;
  }
  return <span>{data.title}</span>;
}

// --------------------------------------------------------------------------- //
// Evidence expansion section
// --------------------------------------------------------------------------- //

function EvidenceSection({
  evidence,
  agentAvailable,
  onCritique,
}: {
  evidence: LeakEvidence;
  agentAvailable: boolean;
  onCritique: (tradeId: number) => void;
}) {
  const tradeIds = evidence.trade_ids ?? [];
  const attemptIds = evidence.attempt_ids ?? [];
  const debriefIds = evidence.debrief_ids ?? [];

  const hasEvidence =
    tradeIds.length > 0 || attemptIds.length > 0 || debriefIds.length > 0;

  if (!hasEvidence) {
    return (
      <p className="text-xs text-muted-foreground">No evidence IDs recorded.</p>
    );
  }

  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      {tradeIds.length > 0 && (
        <div>
          <span className="font-medium text-foreground">Trades:</span>{" "}
          {/* Each evidence trade offers a Critique link (PRD-M9 R6) when the
              agent layer is available. */}
          <span className="inline-flex flex-wrap items-center gap-1.5 align-middle">
            {tradeIds.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => onCritique(id)}
                className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 font-medium text-foreground/90 transition-colors hover:bg-accent/50"
              >
                {agentAvailable && <Sparkles className="h-3 w-3 text-primary" />}#{id}
              </button>
            ))}
          </span>
        </div>
      )}
      {attemptIds.length > 0 && (
        <p>
          <span className="font-medium text-foreground">Attempts:</span>{" "}
          {attemptIds.join(", ")}
        </p>
      )}
      {debriefIds.length > 0 && (
        <p>
          <span className="font-medium text-foreground">Debriefs:</span>{" "}
          {debriefIds.join(", ")}
        </p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Leak card
// --------------------------------------------------------------------------- //

const LEAK_DISPLAY_NAMES: Record<string, string> = {
  revenge_trading: "Revenge trading",
  chop_overtrading: "Chop overtrading",
  moved_stops: "Moved stops",
  psychology_loss: "Psychology failures",
};

export function LeakCard({ leak }: { leak: Leak }) {
  const navigate = useNavigate();
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const agentStatus = useAgentStatus();
  const agentAvailable = agentStatus.data?.available === true;

  const displayName = LEAK_DISPLAY_NAMES[leak.name] ?? leak.name;
  const hasPlaylist = leak.prescribed_scenario_ids.length > 0;

  function handleStartPlaylist() {
    const ids = leak.prescribed_scenario_ids;
    if (ids.length === 0) return;
    const [first, ...rest] = ids;
    const params = new URLSearchParams();
    if (rest.length > 0) params.set("playlist", rest.join(","));
    const qs = params.toString();
    navigate(`/training/drills/${first}${qs ? `?${qs}` : ""}`);
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <SeverityDots severity={leak.severity} />
            <CardTitle className="text-sm">{displayName}</CardTitle>
          </div>
          <Badge
            variant={
              leak.severity === 3
                ? "default"
                : leak.severity === 2
                  ? "outline"
                  : "muted"
            }
            className={
              leak.severity === 3
                ? "border-transparent bg-rose-500/20 text-rose-400"
                : leak.severity === 2
                  ? "border-amber-400/40 text-amber-400"
                  : ""
            }
          >
            {leak.severity === 3 ? "Pattern" : leak.severity === 2 ? "Concern" : "Suggestion"}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Description */}
        <p className="text-sm text-muted-foreground">{leak.description}</p>

        {/* Prescribed drills list */}
        {hasPlaylist && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Suggested drills
            </p>
            <ul className="space-y-0.5">
              {leak.prescribed_scenario_ids.map((id) => (
                <li key={id} className="flex items-center gap-1.5 text-sm">
                  <span className="text-muted-foreground">•</span>
                  <ScenarioTitle scenarioId={id} />
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Action row */}
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {hasPlaylist && (
            <Button size="sm" onClick={handleStartPlaylist} className="gap-1.5">
              <List className="h-3.5 w-3.5" />
              Start playlist
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setEvidenceOpen((v) => !v)}
            className="gap-1"
          >
            {evidenceOpen ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            Show evidence
          </Button>
        </div>

        {/* Evidence expansion */}
        {evidenceOpen && (
          <div className="rounded-md border border-border bg-muted/20 px-3 py-2.5">
            <EvidenceSection
              evidence={leak.evidence}
              agentAvailable={agentAvailable}
              onCritique={(tradeId) => navigate(`/trades/${tradeId}`)}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Loading skeleton
// --------------------------------------------------------------------------- //

export function LeaksSkeleton() {
  return (
    <div className="space-y-4">
      {[1, 2].map((i) => (
        <Card key={i}>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Skeleton className="h-2 w-6" />
              <Skeleton className="h-4 w-40" />
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-2/3" />
            <div className="flex gap-2">
              <Skeleton className="h-8 w-28" />
              <Skeleton className="h-8 w-28" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Error state
// --------------------------------------------------------------------------- //

export function LeaksError({ error }: { error: unknown }) {
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
            ? "Leak detector not ready yet"
            : "Failed to load leaks"}
      </AlertTitle>
      <AlertDescription>
        {isBackendDown ? (
          <>
            The data service isn't reachable. Start it, then reload.
          </>
        ) : notImplemented ? (
          <>
            The leak detector (M6 quant task 6.3.3) hasn't been implemented yet.
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
// Page
// --------------------------------------------------------------------------- //

export function LeaksPage() {
  const { data: report, isLoading, isError, error } = useLeaks();

  // Format the generated_at timestamp for display (e.g. "today, 04:42").
  function formatGeneratedAt(iso: string): string {
    try {
      const d = new Date(iso);
      const today = new Date();
      const isToday =
        d.getFullYear() === today.getFullYear() &&
        d.getMonth() === today.getMonth() &&
        d.getDate() === today.getDate();
      const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      return isToday ? `today, ${time}` : d.toLocaleDateString();
    } catch {
      return iso;
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto p-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 text-primary" />
          <div>
            <h1 className="text-lg font-semibold">Leaks detected</h1>
            <p className="text-xs text-muted-foreground">
              Behavioral patterns in your trading that recurring drills can fix.
            </p>
          </div>
        </div>
        {report?.generated_at && (
          <p className="shrink-0 text-xs text-muted-foreground">
            Last checked {formatGeneratedAt(report.generated_at)}
          </p>
        )}
      </div>

      {/* Content */}
      {isLoading && <LeaksSkeleton />}
      {isError && <LeaksError error={error} />}

      {!isLoading && !isError && report && report.leaks.length === 0 && (
        <div className="flex flex-col items-center gap-4 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-muted-foreground/30" />
          <div>
            <p className="font-medium text-muted-foreground">
              You're clean — nothing to work on
            </p>
            <p className="mt-1 text-sm text-muted-foreground/70">
              No behavioral leaks detected in your recent trades and drills. Keep
              drilling and debriefing to maintain the pattern.
            </p>
          </div>
        </div>
      )}

      {!isLoading && !isError && report && report.leaks.length > 0 && (
        <div className="space-y-4">
          {report.leaks.map((leak) => (
            <LeakCard key={leak.name} leak={leak} />
          ))}
        </div>
      )}
    </div>
  );
}
