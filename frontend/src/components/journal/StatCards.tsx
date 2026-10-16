import { Skeleton } from "@/components/ui/skeleton";
import type { AnalyticsSummary } from "@/lib/api";
import {
  formatFactor,
  formatHoldTime,
  formatNumber,
  formatPercent,
  formatPnl,
  pnlColorClass,
} from "@/lib/format";
import { cn } from "@/lib/utils";

interface StatCardsProps {
  summary: AnalyticsSummary | undefined;
  loading: boolean;
}

interface Stat {
  label: string;
  value: string;
  colorClass?: string;
  emphasis?: boolean;
}

/** Headline metric row for the journal dashboard. Colors P&L; monospace values. */
export function StatCards({ summary, loading }: StatCardsProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="rounded-lg border border-border bg-card p-3"
          >
            <Skeleton className="h-3 w-16" />
            <Skeleton className="mt-2 h-6 w-20" />
          </div>
        ))}
      </div>
    );
  }

  const s = summary;
  const stats: Stat[] = [
    {
      label: "Net P&L",
      value: formatPnl(s?.total_pnl),
      colorClass: pnlColorClass(s?.total_pnl),
      emphasis: true,
    },
    { label: "Win rate", value: formatPercent(s?.win_rate) },
    { label: "Profit factor", value: formatFactor(s?.profit_factor) },
    {
      label: "Avg R",
      value: formatNumber(s?.avg_r),
      colorClass: pnlColorClass(s?.avg_r),
    },
    {
      label: "Expectancy",
      value: formatPnl(s?.expectancy),
      colorClass: pnlColorClass(s?.expectancy),
    },
    { label: "Trades", value: formatNumber(s?.trade_count, 0) },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {stats.map((stat) => (
        <div
          key={stat.label}
          className="rounded-lg border border-border bg-card p-3"
        >
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {stat.label}
          </p>
          <p
            className={cn(
              "tabular mt-1 font-semibold",
              stat.emphasis ? "text-xl" : "text-lg",
              stat.colorClass,
            )}
          >
            {stat.value}
          </p>
        </div>
      ))}
      {s && s.trade_count > 0 && (
        <p className="col-span-2 -mt-1 text-[11px] text-muted-foreground sm:col-span-3 lg:col-span-6">
          Avg win {formatPnl(s.avg_win)} · avg loss {formatPnl(s.avg_loss)} ·
          avg hold {formatHoldTime(s.avg_hold_time_s)}
        </p>
      )}
    </div>
  );
}
