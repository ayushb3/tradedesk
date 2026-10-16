import { Skeleton } from "@/components/ui/skeleton";
import type { ByRow } from "@/lib/api";
import {
  formatNumber,
  formatPercent,
  formatPnl,
  PNL_GREEN,
  PNL_RED,
  pnlColorClass,
} from "@/lib/format";

interface BreakdownProps {
  title: string;
  rows: ByRow[] | undefined;
  loading: boolean;
  isError?: boolean;
  /** Rename the leading column (default "Group"). */
  keyLabel?: string;
}

/** A compact CSS-bar breakdown list (by ticker / time-of-day / tag). */
export function Breakdown({
  title,
  rows,
  loading,
  isError,
  keyLabel = "Group",
}: BreakdownProps) {
  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
        <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
          {keyLabel}
        </span>
      </div>
      <div className="p-3">
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : isError ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            Breakdown unavailable.
          </p>
        ) : !rows || rows.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">
            No data yet.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {rows.map((row) => (
              <BarRow
                key={row.key}
                row={row}
                maxAbs={Math.max(
                  ...rows.map((r) => Math.abs(r.pnl)),
                  1,
                )}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function BarRow({ row, maxAbs }: { row: ByRow; maxAbs: number }) {
  const pct = Math.min(100, (Math.abs(row.pnl) / maxAbs) * 100);
  const positive = row.pnl >= 0;
  return (
    <li className="group">
      <div className="mb-0.5 flex items-baseline justify-between gap-2 text-xs">
        <span className="truncate font-medium">{row.key}</span>
        <span className={"tabular shrink-0 " + pnlColorClass(row.pnl)}>
          {formatPnl(row.pnl)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full"
            style={{
              width: `${pct}%`,
              backgroundColor: positive ? PNL_GREEN : PNL_RED,
            }}
          />
        </div>
        <span className="tabular w-24 shrink-0 text-right text-[10px] text-muted-foreground">
          {row.trade_count}t · {formatPercent(row.win_rate)} ·{" "}
          {formatNumber(row.avg_r)}R
        </span>
      </div>
    </li>
  );
}
