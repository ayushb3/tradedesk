import { RuleChip } from "@/components/days/RuleChip";
import type { Verdict } from "@/lib/dayPage";
import { formatNumber, formatPnl, pnlColorClass } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Signed R, e.g. "+0.8R" / "−1.2R". */
export function formatR(r: number | null | undefined): string {
  if (r == null || !Number.isFinite(r)) return "—";
  const sign = r > 0 ? "+" : r < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(r), 1)}R`;
}

/**
 * The day in one band: net P&L (loud, never a score), the sum of R, and the rule
 * chips from the day stop result and per-trade rule results.
 */
export function VerdictBand({ verdict }: { verdict: Verdict }) {
  const { net, sumR, rCount, assumedCount, chips } = verdict;
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-2">
        <div className="flex flex-col">
          <span className="label-caps text-label text-muted-foreground">Day net</span>
          <span className={cn("num text-key font-bold", pnlColorClass(net))}>{formatPnl(net)}</span>
        </div>
        <div className="flex flex-col">
          <span className="label-caps text-label text-muted-foreground">Sum of R</span>
          <span className={cn("num text-key font-semibold", pnlColorClass(sumR))}>{formatR(sumR)}</span>
          {rCount > 0 && assumedCount > 0 && (
            <span className="text-label text-muted-foreground">
              {assumedCount} of {rCount} {rCount === 1 ? "trade uses" : "trades use"} an assumed R
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {chips.map((c) => (
          <RuleChip key={c.key} label={c.label} tone={c.tone} />
        ))}
      </div>
    </section>
  );
}
