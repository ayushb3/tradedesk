import { cn } from "@/lib/utils";
import type { Scoreboard } from "@/lib/daysHome";
import { formatPnl, pnlColorClass } from "@/lib/format";

/**
 * One row per rule: days kept / days traded for the visible range. The broken
 * count is the headline. P&L sits beside it (loud, signed) and never replaces it.
 */
export function RuleScoreboard({ board, label }: { board: Scoreboard; label: string }) {
  const { rows, tradedDays, brokenTotal, net } = board;
  return (
    <section aria-label="Rule scoreboard" className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-b border-border px-4 py-3">
        <div>
          <p className="label-caps text-label text-muted-foreground">Rules broken · {label}</p>
          <p
            className={cn(
              "num text-key font-semibold",
              tradedDays === 0 ? "text-muted-foreground" : brokenTotal > 0 ? "text-loss" : "text-gain",
            )}
          >
            {tradedDays === 0 ? "—" : brokenTotal}
            {tradedDays > 0 && (
              <span className="ml-2 text-body font-normal text-muted-foreground">
                over {tradedDays} {tradedDays === 1 ? "day" : "days"} traded
              </span>
            )}
          </p>
        </div>
        <div className="text-right">
          <p className="label-caps text-label text-muted-foreground">Net P&amp;L · {label}</p>
          <p className={cn("num text-key font-semibold", pnlColorClass(tradedDays ? net : null))}>
            {tradedDays ? formatPnl(net) : "—"}
          </p>
        </div>
      </div>
      {tradedDays === 0 ? (
        <p className="px-4 py-3 text-body text-muted-foreground">No trading days in this range.</p>
      ) : (
        <ul className="divide-y divide-border">
          {rows.map((r) => (
            <li key={r.code} className="flex items-center gap-3 px-4 py-2">
              <span className="min-w-0 flex-1 truncate text-body">{r.name}</span>
              {r.unknown > 0 && (
                <span className="text-label text-muted-foreground">{r.unknown} unknown</span>
              )}
              <span className="num w-16 shrink-0 text-right text-value">
                <span className={r.broken > 0 ? "text-foreground" : "text-gain"}>{r.kept}</span>
                <span className="text-muted-foreground"> / {r.traded}</span>
              </span>
              <span
                className={cn(
                  "num w-24 shrink-0 rounded-full border px-2 py-0.5 text-center text-label font-medium",
                  r.broken > 0
                    ? "border-loss/40 bg-loss/10 text-loss"
                    : "border-border bg-muted text-muted-foreground",
                )}
                title={`${r.broken} ${r.broken === 1 ? "day" : "days"} broken`}
              >
                {r.broken} broken
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
