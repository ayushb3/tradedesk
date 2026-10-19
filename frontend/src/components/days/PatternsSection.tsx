import { AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";

import { RuleChip } from "@/components/days/RuleChip";
import { formatDayMonth } from "@/lib/format";
import type { RuleBreak } from "@/lib/daysHome";
import { useLeaks } from "@/lib/drills";
import { LeakCard, LeaksError, LeaksSkeleton } from "@/pages/LeaksPage";

/**
 * Patterns: rule breaks across the shown trades (each links to its trades), then
 * the behavioral leaks, which moved here from their own page.
 */
export function PatternsSection({ breaks }: { breaks: RuleBreak[] }) {
  const leaks = useLeaks();
  const list = leaks.data?.leaks ?? [];
  return (
    <section id="patterns" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-body font-semibold tracking-tight">Patterns</h2>
      </div>

      <div className="rounded-lg border border-border bg-card">
        <p className="label-caps border-b border-border px-4 py-2 text-label text-muted-foreground">
          Rule breaks
        </p>
        {breaks.length === 0 ? (
          <p className="px-4 py-3 text-body text-muted-foreground">No rules broken in these trades.</p>
        ) : (
          <ul className="divide-y divide-border">
            {breaks.map((b) => (
              <li key={b.code} className="flex flex-col gap-1.5 px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <RuleChip tone="broken" label={b.name} />
                  <span className="num text-label text-muted-foreground">
                    {b.trades.length} {b.trades.length === 1 ? "trade" : "trades"} · {b.days}{" "}
                    {b.days === 1 ? "day" : "days"}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {b.trades.map((t) => (
                    <Link
                      key={t.id}
                      to={`/trades/${t.id}`}
                      className="num rounded-md border border-border px-1.5 py-0.5 text-label text-foreground/90 transition-colors hover:bg-muted"
                    >
                      {formatDayMonth(t.date)} · {t.label}
                    </Link>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {leaks.isLoading && <LeaksSkeleton />}
      {leaks.isError && <LeaksError error={leaks.error} />}
      {leaks.data && list.length > 0 && (
        <div className="flex flex-col gap-3">
          <p className="label-caps text-label text-muted-foreground">Leaks</p>
          {list.map((leak) => (
            <LeakCard key={leak.name} leak={leak} />
          ))}
        </div>
      )}
    </section>
  );
}
