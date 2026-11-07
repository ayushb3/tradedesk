import { Play } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { RuleChip } from "@/components/days/RuleChip";
import { formatR } from "@/components/days/VerdictBand";
import { ruleLabel, type TradeRow } from "@/lib/dayPage";
import { formatPnl, pnlColorClass } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Shown only when the trade had a written stop; R from an assumed risk is left blank here. */
export const R_COLUMN_LABEL = "R (with stop)";
const R_TITLE = "R = P&L / the written max loss. Blank when no stop was written.";

/**
 * The day trades in time order. A row opens the trade review; Replay opens the same trade
 * page with the replay playing inline (?replay=1). A dot marks trades not reviewed yet.
 */
export function TradeRows({ rows }: { rows: TradeRow[] }) {
  const navigate = useNavigate();
  return (
    <ul className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
      <li
        aria-hidden
        className="flex flex-wrap items-center gap-x-4 px-3 py-1.5 text-label label-caps text-muted-foreground"
      >
        <span className="w-24 shrink-0">Time</span>
        <span className="min-w-0 flex-1">Contract</span>
        <span className="w-28 shrink-0 text-right" title={R_TITLE}>
          {R_COLUMN_LABEL}
        </span>
        <span className="w-24 shrink-0 text-right">P&amp;L</span>
        <span className="min-w-[8rem] flex-1">Rules</span>
        <span className="w-14 shrink-0" />
      </li>
      {rows.map((r) => (
        <li
          key={r.tradeId}
          role="link"
          tabIndex={0}
          onClick={() => navigate(`/trades/${r.tradeId}`)}
          onKeyDown={(e) => {
            if (e.key === "Enter") navigate(`/trades/${r.tradeId}`);
          }}
          className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2.5 transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none"
        >
          <span className="num flex w-24 shrink-0 items-center gap-1.5 whitespace-nowrap text-body text-muted-foreground">
            <span
              className={cn("h-1.5 w-1.5 shrink-0 rounded-full", r.reviewed ? "bg-transparent" : "bg-primary")}
              title={r.reviewed ? "Reviewed" : "Not reviewed yet"}
              aria-label={r.reviewed ? "Reviewed" : "Not reviewed yet"}
            />
            {r.timePt}
          </span>
          <span className="min-w-0 flex-1 truncate text-body font-medium">{r.contract}</span>
          <span
            className={cn("num w-28 shrink-0 text-right text-value", pnlColorClass(r.r))}
            title={R_TITLE}
          >
            {r.r != null && !r.assumed ? formatR(r.r) : ""}
          </span>
          <span className={cn("num w-24 shrink-0 text-right text-value", pnlColorClass(r.pnl))}>
            {r.open ? "open" : formatPnl(r.pnl)}
          </span>
          <span className="flex min-w-[8rem] flex-1 flex-wrap items-center gap-1.5">
            {r.broken.map((b) => (
              <RuleChip key={b.code} label={ruleLabel(b.code)} tone="broken" title={b.detail} />
            ))}
            {r.keptCount > 0 && (
              <span className="text-label text-muted-foreground">{r.keptCount} kept</span>
            )}
          </span>
          <a
            href={`/trades/${r.tradeId}?replay=1`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              navigate(`/trades/${r.tradeId}?replay=1`);
            }}
            className="inline-flex w-14 shrink-0 items-center gap-1 text-label font-medium text-play hover:underline"
          >
            <Play className="h-3 w-3" />
            Replay
          </a>
        </li>
      ))}
    </ul>
  );
}
