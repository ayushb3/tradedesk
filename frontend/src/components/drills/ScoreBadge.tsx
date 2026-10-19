/**
 * ScoreBadge — large process-score display used in the DrillPlayerPage
 * finished state. Shows a 0..100 integer with a "process, not P&L" tooltip.
 */

import { cn } from "@/lib/utils";

interface ScoreBadgeProps {
  /** 0..1 float from the backend. Rendered as 0..100. */
  score: number;
  className?: string;
}

export function ScoreBadge({ score, className }: ScoreBadgeProps) {
  const pct = Math.round(score * 100);

  const color =
    pct >= 80
      ? "text-emerald-400"
      : pct >= 60
        ? "text-yellow-400"
        : "text-rose-400";

  return (
    <div
      className={cn("flex flex-col items-center gap-1", className)}
      title="Process score — measures rule adherence and decision quality, not P&L."
    >
      <span className={cn("text-5xl font-bold tabular-nums", color)}>
        {pct}
      </span>
      <span className="text-xs text-muted-foreground">/ 100</span>
      <span className="mt-1 text-label text-muted-foreground">
        process score
      </span>
    </div>
  );
}
