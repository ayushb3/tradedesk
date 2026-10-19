/**
 * CritiquePanel — renders a Trade Critic result (PRD-M9 R3/R5/R8/R10).
 *
 * Layout, top → bottom:
 *   1. Four phase verdicts (setup / entry / management / exit), markdown
 *      rendered with the M8 whitespace-pre-wrap convention (one visual
 *      language — NO markdown library).
 *   2. Rule checks, each with a pass/fail/unclear adherence badge (distinct
 *      colors + icon), a curriculum citation deep-linking into the M8 reader,
 *      and — when uncited — a visually-distinct "unverified" treatment
 *      (muted + dashed border + tag) so an ungrounded claim never reads as
 *      authoritative (R3).
 *   3. Prescription: readings deep-link to the lesson reader; a drill either
 *      links to an existing scenario (drill_id) or offers a "Create this drill"
 *      button that POSTs the drill_spec and navigates to the new drill (R10).
 *
 * Anti-gambling: this surface reports process adherence only — the critic's
 * schema forbids P&L-referencing praise/blame (M6 audit / PRD-M9 R11), so
 * there is no P&L styling here.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BookMarked,
  CheckCircle2,
  ExternalLink,
  HelpCircle,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MermaidDiagram } from "@/components/knowledge/MermaidDiagram";
import type {
  CriticAdherence,
  CriticPrescription,
  CriticResult,
  CriticRuleCheck,
  KnowledgeSearchHit,
} from "@/lib/api";
import { type ScenarioCreateSpec } from "@/lib/api";
import { createScenario, type ScenarioCreateInput } from "@/lib/training";
import { cn } from "@/lib/utils";
import { useKnowledgeLesson } from "@/lib/queries";

// Fenced Mermaid blocks may appear inside a verdict's markdown; extract them so
// a diagram renders as a diagram (R5) rather than a literal code fence.
const MERMAID_FENCE = /```mermaid\s*\n([\s\S]*?)```/g;

const PHASE_LABELS: Array<{ key: keyof CriticResult["verdict_by_phase"]; label: string }> = [
  { key: "setup", label: "Setup" },
  { key: "entry", label: "Entry" },
  { key: "management", label: "Management" },
  { key: "exit", label: "Exit" },
];

/** Deep-link a citation into the M8 lesson reader, anchored at the cited chunk. */
export function citationHref(hit: KnowledgeSearchHit): string {
  return `/knowledge/lessons/${hit.lesson_id}#chunk-${hit.seq}`;
}

export function CritiquePanel({ result }: { result: CriticResult }) {
  return (
    <div className="flex flex-col gap-5">
      <PhaseVerdicts verdicts={result.verdict_by_phase} />
      <RuleChecks checks={result.rule_checks} />
      <Prescription prescription={result.prescription} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Markdown rendering (whitespace-pre-wrap; splits out Mermaid fences)          //
// --------------------------------------------------------------------------- //

function Markdown({ md }: { md: string }) {
  const text = md.trim();
  if (!text) {
    return <p className="text-sm text-muted-foreground">—</p>;
  }

  MERMAID_FENCE.lastIndex = 0;
  if (!MERMAID_FENCE.test(text)) {
    return (
      <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
        {text}
      </div>
    );
  }

  // Interleave prose segments and Mermaid diagrams.
  MERMAID_FENCE.lastIndex = 0;
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = MERMAID_FENCE.exec(text)) !== null) {
    const before = text.slice(cursor, match.index).trim();
    if (before) {
      parts.push(
        <div
          key={`t${i}`}
          className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90"
        >
          {before}
        </div>,
      );
    }
    parts.push(<MermaidDiagram key={`m${i}`} source={match[1].trim()} />);
    cursor = match.index + match[0].length;
    i += 1;
  }
  const tail = text.slice(cursor).trim();
  if (tail) {
    parts.push(
      <div
        key="tail"
        className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90"
      >
        {tail}
      </div>,
    );
  }
  return <div className="flex flex-col gap-3">{parts}</div>;
}

// --------------------------------------------------------------------------- //
// Phase verdicts                                                               //
// --------------------------------------------------------------------------- //

function PhaseVerdicts({
  verdicts,
}: {
  verdicts: CriticResult["verdict_by_phase"];
}) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold tracking-tight">Phase verdicts</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        {PHASE_LABELS.map(({ key, label }) => (
          <div
            key={key}
            className="rounded-lg border border-border bg-background/40 p-3"
          >
            <p className="mb-1.5 text-label font-semibold uppercase tracking-widest text-muted-foreground">
              {label}
            </p>
            <Markdown md={verdicts[key] ?? ""} />
          </div>
        ))}
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- //
// Rule checks                                                                  //
// --------------------------------------------------------------------------- //

const ADHERENCE_META: Record<
  CriticAdherence,
  { label: string; icon: typeof CheckCircle2; className: string }
> = {
  pass: {
    label: "Pass",
    icon: CheckCircle2,
    className: "border-transparent bg-emerald-500/20 text-emerald-400",
  },
  fail: {
    label: "Fail",
    icon: XCircle,
    className: "border-transparent bg-rose-500/20 text-rose-400",
  },
  unclear: {
    label: "Unclear",
    icon: HelpCircle,
    className: "border-amber-400/40 bg-amber-400/10 text-amber-400",
  },
};

function AdherenceBadge({ adherence }: { adherence: CriticAdherence }) {
  const meta = ADHERENCE_META[adherence] ?? ADHERENCE_META.unclear;
  const Icon = meta.icon;
  return (
    <Badge className={cn("gap-1", meta.className)}>
      <Icon className="h-3 w-3" />
      {meta.label}
    </Badge>
  );
}

function RuleChecks({ checks }: { checks: CriticRuleCheck[] }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold tracking-tight">
        Rule checks{" "}
        <span className="text-xs font-normal text-muted-foreground">
          per course material
        </span>
      </h3>
      {checks.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No rule checks in this critique.
        </p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {checks.map((check, i) => (
            <RuleCheckRow key={i} check={check} />
          ))}
        </ul>
      )}
    </section>
  );
}

function RuleCheckRow({ check }: { check: CriticRuleCheck }) {
  const unverified = check.source_citation === null;
  return (
    <li
      className={cn(
        "rounded-lg border p-3",
        unverified
          ? "border-dashed border-muted-foreground/40 bg-muted/20"
          : "border-border bg-background/40",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p
          className={cn(
            "text-sm font-medium",
            unverified ? "text-muted-foreground" : "text-foreground",
          )}
        >
          {check.rule}
        </p>
        <AdherenceBadge adherence={check.adherence} />
      </div>

      {check.note ? (
        <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
          {check.note}
        </p>
      ) : null}

      <div className="mt-2">
        {unverified ? (
          <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-muted-foreground/40 px-2 py-0.5 text-label font-medium text-muted-foreground">
            <ShieldAlert className="h-3 w-3" />
            unverified — no curriculum citation
          </span>
        ) : (
          <Citation hit={check.source_citation as KnowledgeSearchHit} />
        )}
      </div>
    </li>
  );
}

function Citation({ hit }: { hit: KnowledgeSearchHit }) {
  return (
    <a
      href={citationHref(hit)}
      className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-0.5 text-label font-medium text-foreground/90 transition-colors hover:bg-accent/50"
    >
      <BookMarked className="h-3 w-3 shrink-0 text-muted-foreground" />
      <span className="truncate">{hit.citation}</span>
    </a>
  );
}

// --------------------------------------------------------------------------- //
// Prescription                                                                 //
// --------------------------------------------------------------------------- //

function Prescription({ prescription }: { prescription: CriticPrescription }) {
  const { drill_id, drill_spec, readings } = prescription;
  const hasReadings = readings.length > 0;
  const hasDrill = drill_id != null || drill_spec != null;

  if (!hasReadings && !hasDrill) return null;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-primary/30 bg-primary/5 p-4">
      <h3 className="text-sm font-semibold tracking-tight">Prescription</h3>

      {hasReadings ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-label font-semibold uppercase tracking-widest text-muted-foreground">
            Readings
          </p>
          <ul className="flex flex-col gap-1">
            {readings.map((lessonId) => (
              <li key={lessonId}>
                <ReadingLink lessonId={lessonId} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {hasDrill ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-label font-semibold uppercase tracking-widest text-muted-foreground">
            Drill
          </p>
          <DrillPrescription drillId={drill_id} drillSpec={drill_spec} />
        </div>
      ) : null}
    </section>
  );
}

/** A reading deep-link resolving the lesson's citation for a readable label. */
function ReadingLink({ lessonId }: { lessonId: number }) {
  const lesson = useKnowledgeLesson(lessonId);
  const label =
    lesson.data?.citation ?? lesson.data?.title ?? `Lesson #${lessonId}`;
  return (
    <a
      href={`/knowledge/lessons/${lessonId}`}
      className="inline-flex items-center gap-1.5 text-sm text-foreground/90 transition-colors hover:text-foreground hover:underline"
    >
      <BookMarked className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span>{label}</span>
    </a>
  );
}

function DrillPrescription({
  drillId,
  drillSpec,
}: {
  drillId: number | null;
  drillSpec: ScenarioCreateSpec | null;
}) {
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (drillId != null) {
    return (
      <Button
        size="sm"
        variant="outline"
        className="w-fit gap-1.5"
        onClick={() => navigate(`/training/drills/${drillId}`)}
      >
        Take drill #{drillId}
      </Button>
    );
  }

  if (drillSpec == null) return null;

  const handleCreate = async () => {
    if (creating) return;
    setCreating(true);
    setError(null);
    try {
      // drill_spec IS a ScenarioCreate body — POST it verbatim, then jump into
      // the created drill (R10).
      const created = await createScenario(
        drillSpec as unknown as ScenarioCreateInput,
      );
      navigate(`/training/drills/${created.id}`);
    } catch (err) {
      // TrainingApiError (and ApiError) both carry an optional `detail`; fall
      // back to the message otherwise.
      const detail =
        err && typeof err === "object" && "detail" in err
          ? (err as { detail?: string }).detail
          : undefined;
      setError(detail ?? (err instanceof Error ? err.message : String(err)));
      setCreating(false);
    }
  };

  return (
    <div className="flex flex-col gap-1.5">
      <Button
        size="sm"
        className="w-fit gap-1.5"
        disabled={creating}
        onClick={() => void handleCreate()}
      >
        {creating ? "Creating…" : "Create this drill"}
        {!creating && <ExternalLink className="h-3.5 w-3.5" />}
      </Button>
      {error ? (
        <p className="text-xs text-rose-400">Couldn't create the drill: {error}</p>
      ) : null}
    </div>
  );
}
