import { Play } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { RuleChip } from "@/components/days/RuleChip";
import { formatR } from "@/components/days/VerdictBand";
import { ruleLabel, type TradeRow } from "@/lib/dayPage";
import { formatPnl, pnlColorClass } from "@/lib/format";
import { cn } from "@/lib/utils";

/** The day trades in time order. A row opens the trade review; Replay is its own link. */
export function TradeRows({ rows }: { rows: TradeRow[] }) {
  const navigate = useNavigate();
  return (
    <ul className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
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
          <span className="num w-20 shrink-0 text-body text-muted-foreground">{r.timePt}</span>
          <span className="min-w-0 flex-1 truncate text-body font-medium">{r.contract}</span>
          <span className={cn("num w-28 shrink-0 text-right text-value", pnlColorClass(r.r))}>
            {formatR(r.r)}
            {r.assumed && r.r != null && (
              <span className="ml-1 text-label font-normal text-muted-foreground">assumed</span>
            )}
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
            href={`/replay?trade=${r.tradeId}&lead=15`}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              navigate(`/replay?trade=${r.tradeId}&lead=15`);
            }}
            className="inline-flex shrink-0 items-center gap-1 text-label font-medium text-play hover:underline"
          >
            <Play className="h-3 w-3" />
            Replay
          </a>
        </li>
      ))}
    </ul>
  );
}
