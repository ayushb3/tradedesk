import { Badge } from "@/components/ui/badge";
import {
  DISPLAY_TZ_LABEL,
  formatClock,
  formatCurrency,
  formatHoldTime,
  formatNumber,
  formatPnl,
  formatR,
  pnlColorClass,
} from "@/lib/format";
import { ruleChips, type DayTally, type Tone } from "@/lib/tradeReview";
import type { Trade } from "@/lib/api";
import { cn } from "@/lib/utils";

const TONE: Record<Tone, string> = {
  kept: "border-gain/40 bg-gain/10 text-gain",
  broken: "border-loss/40 bg-loss/10 text-loss",
  unknown: "border-border bg-muted/40 text-grey",
};

interface Props {
  trade: Trade;
  label: string;
  tally: DayTally | null;
}

/**
 * Verdict band: the result first. R is the hero (one per screen), net $ beneath it, then
 * the rule chips and the day tally against the daily stop. Replaces the six equal-weight
 * header stats. P&L is shown loud but is never a score.
 */
export function VerdictBand({ trade, label, tally }: Props) {
  const chips = ruleChips(trade.rule_results);
  const assumed = trade.r_basis === "assumed";

  return (
    <div className="rounded-lg border border-border bg-background/40 p-4" data-testid="verdict-band">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-value font-semibold tracking-tight">{label}</span>
        <span className={cn("text-label label-caps font-medium", trade.direction === "long" ? "text-gain" : "text-loss")}>
          {trade.direction}
        </span>
        {trade.simulated && <Badge variant="muted">sim</Badge>}
        <Badge variant="outline" className="capitalize">
          {trade.status}
        </Badge>
        {trade.enrichment?.grade && <Badge variant="outline">Grade {trade.enrichment.grade}</Badge>}
        <span className="num ml-auto text-label text-muted-foreground">
          {formatClock(trade.open_ts)}
          {trade.close_ts ? ` → ${formatClock(trade.close_ts)}` : ""} {DISPLAY_TZ_LABEL} · {formatHoldTime(trade.hold_time_s)}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="flex flex-col">
          <div className="flex items-baseline gap-2">
            <span className={cn("num text-hero font-bold leading-none", pnlColorClass(trade.r_multiple))} data-testid="hero-r">
              {formatR(trade.r_multiple)}
            </span>
            {assumed && trade.r_multiple != null && (
              <span
                className="text-label label-caps text-stop"
                title="No max loss was written before entry, so R is measured on an assumed stop (a share of the premium paid)."
              >
                assumed
              </span>
            )}
          </div>
          <span className={cn("num mt-1 text-key", pnlColorClass(trade.net_pnl))} data-testid="net-pnl">
            {formatPnl(trade.net_pnl)}
          </span>
          <span className="num text-label text-muted-foreground">
            net · {formatNumber(trade.qty, 0)} × {formatNumber(trade.avg_entry)} → {formatNumber(trade.avg_exit)} · fees{" "}
            {formatCurrency(trade.fees)}
          </span>
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2" data-testid="rule-chips">
            {chips.map((c) => (
              <span
                key={c.code}
                title={c.detail}
                className={cn("rounded-full border px-2.5 py-0.5 text-label", TONE[c.tone])}
              >
                {c.label}
              </span>
            ))}
            {chips.length === 0 && <span className="text-label text-muted-foreground">No rule results for this trade.</span>}
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {tally && (
              <span className="num text-body" data-testid="day-tally" title="Net of every trade closed up to and including this one">
                <span className="text-label label-caps text-muted-foreground">Day after this trade </span>
                <span className={cn("font-medium", pnlColorClass(tally.net))}>{formatPnl(tally.net)}</span>
                {tally.limit !== null && (
                  <span className={cn("ml-1", tally.atStop ? "text-loss" : "text-muted-foreground")}>
                    vs stop {formatPnl(tally.limit)}
                    {tally.atStop ? " · at the stop" : ""}
                  </span>
                )}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
