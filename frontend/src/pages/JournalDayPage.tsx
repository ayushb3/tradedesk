import { useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  Maximize2,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiErrorState, EmptyState } from "@/components/journal/ApiStates";
import { Sparkline } from "@/components/review/Sparkline";
import { TradeMiniChart } from "@/components/journal/TradeMiniChart";
import type { AnalyticsSummary, DayTradeCard } from "@/lib/api";
import {
  formatClock,
  formatDayMonth,
  formatFactor,
  formatHoldTime,
  formatNumber,
  formatPercent,
  formatPnl,
  pnlColorClass,
  DISPLAY_TZ_LABEL,
} from "@/lib/format";
import { useDailyReview, useDayTrades } from "@/lib/queries";
import { cn } from "@/lib/utils";

/**
 * A session day's trades — the single day surface (#67). Shows a day-summary
 * stat row, the scannable trade-card grid, and end-of-day reflection prompts.
 * Reached from the Journal P&L calendar (day click); cards open the per-trade
 * cockpit for full analysis, or expand a mini chart in place for a quick look.
 * Framing is process-first: the header counts trades, not wins.
 */
export function JournalDayPage() {
  const { date = "" } = useParams<{ date: string }>();
  const navigate = useNavigate();
  const { data, isLoading, isError, error } = useDayTrades(date);
  // Day-summary stats + reflection prompts come from the review payload, keyed
  // on the same :date param as the cards.
  const review = useDailyReview(date);

  const trades = data?.trades ?? [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate("/journal")}
          className="gap-1.5"
        >
          <ArrowLeft className="h-4 w-4" />
          Journal
        </Button>
        <div className="flex items-center gap-2">
          <CalendarDays className="h-4 w-4 text-muted-foreground" />
          <h1 className="font-semibold tracking-tight">
            {formatDayMonth(date)}
          </h1>
          {trades.length > 0 && (
            <span className="tabular text-xs text-muted-foreground">
              {trades.length} {trades.length === 1 ? "trade" : "trades"}
            </span>
          )}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          {isLoading ? (
            <>
              <Skeleton className="h-20 w-full rounded-lg" />
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-36 w-full rounded-lg" />
                ))}
              </div>
            </>
          ) : isError ? (
            <ApiErrorState error={error} />
          ) : trades.length === 0 ? (
            <EmptyState
              title="No trades this day"
              description="Nothing was logged for this session. Pick another day from the Journal calendar to review your process."
            />
          ) : (
            <>
              <DailySummary summary={review.data?.summary} />

              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {trades.map((t) => (
                  <TradeCard
                    key={t.id}
                    trade={t}
                    onOpen={() => navigate(`/trades/${t.id}`)}
                  />
                ))}
              </div>

              <JournalPrompts key={date} prompts={review.data?.prompts ?? []} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function TradeCard({
  trade,
  onOpen,
}: {
  trade: DayTradeCard;
  onOpen: () => void;
}) {
  const label = trade.contract_label ?? trade.symbol;
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card/40 p-3 transition-colors hover:border-primary/40">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold tracking-tight">{label}</span>
        <span
          className={cn(
            "text-[10px] font-medium uppercase",
            trade.direction === "long" ? "text-[#26a69a]" : "text-[#ef5350]",
          )}
        >
          {trade.direction}
        </span>
        {trade.simulated && (
          <Badge variant="muted" className="text-[10px]">
            sim
          </Badge>
        )}
        {trade.grade && (
          <Badge variant="outline" className="ml-auto text-[10px]">
            {trade.grade}
          </Badge>
        )}
      </div>

      {/* Chart affordance: click the sparkline / expand toggle to open a mini
          candle chart IN PLACE; the "Open" button navigates to the cockpit. */}
      <div className="flex flex-col gap-1.5">
        {expanded ? (
          <TradeMiniChart tradeId={trade.id} />
        ) : trade.sparkline ? (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-expanded={false}
            aria-label="Expand mini chart"
            className="flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-muted-foreground transition-colors hover:bg-muted/40 hover:text-foreground"
          >
            <ChevronRight className="h-3.5 w-3.5 shrink-0" />
            <Sparkline data={trade.sparkline} className="w-full" width={200} />
          </button>
        ) : (
          <div className="flex h-7 items-center text-[10px] text-muted-foreground">
            No price path
          </div>
        )}
        {expanded && (
          <button
            type="button"
            onClick={() => setExpanded(false)}
            aria-expanded={true}
            className="flex w-fit items-center gap-1 text-[10px] text-muted-foreground transition-colors hover:text-foreground"
          >
            <ChevronDown className="h-3 w-3" />
            Collapse
          </button>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2">
        <Field label="Net P&L">
          <span className={cn("tabular font-medium", pnlColorClass(trade.net_pnl))}>
            {formatPnl(trade.net_pnl)}
          </span>
        </Field>
        <Field label="R">
          <span className={cn("tabular", pnlColorClass(trade.r_multiple))}>
            {formatNumber(trade.r_multiple)}
          </span>
        </Field>
        <Field label="Hold">
          <span className="tabular text-muted-foreground">
            {formatHoldTime(trade.hold_time_s)}
          </span>
        </Field>
      </div>

      <div className="flex items-center justify-between gap-2">
        <p className="tabular text-[10px] text-muted-foreground">
          {formatClock(trade.open_ts)}
          {trade.close_ts ? ` → ${formatClock(trade.close_ts)}` : ""} {DISPLAY_TZ_LABEL}
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={onOpen}
          className="h-6 gap-1 px-2 text-[10px]"
        >
          <Maximize2 className="h-3 w-3" />
          Open
        </Button>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-xs">{children}</span>
    </div>
  );
}

/** Day-summary stat row (moved from the retired Daily Review page, #67). */
function DailySummary({
  summary,
}: {
  summary: Partial<AnalyticsSummary> | undefined;
}) {
  const stats: { label: string; value: string; colorClass?: string }[] = [
    {
      label: "Net P&L",
      value: formatPnl(summary?.total_pnl),
      colorClass: pnlColorClass(summary?.total_pnl),
    },
    { label: "Trades", value: formatNumber(summary?.trade_count, 0) },
    { label: "Win rate", value: formatPercent(summary?.win_rate) },
    { label: "Profit factor", value: formatFactor(summary?.profit_factor) },
    {
      label: "Avg R",
      value: formatNumber(summary?.avg_r),
      colorClass: pnlColorClass(summary?.avg_r),
    },
    { label: "Avg hold", value: formatHoldTime(summary?.avg_hold_time_s) },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {stats.map((s) => (
        <div
          key={s.label}
          className="rounded-lg border border-border bg-card p-3"
        >
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {s.label}
          </p>
          <p className={"tabular mt-1 text-lg font-semibold " + (s.colorClass ?? "")}>
            {s.value}
          </p>
        </div>
      ))}
    </div>
  );
}

/**
 * Reflection prompts as a review checklist. Answers are local to the session —
 * there's no persistence endpoint in the M3 contract, so this is a working
 * scratchpad for end-of-day journaling. (Persistence tracked separately, #68.)
 */
function JournalPrompts({ prompts }: { prompts: string[] }) {
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  const [answers, setAnswers] = useState<Record<number, string>>({});

  if (prompts.length === 0) {
    return (
      <section className="space-y-2">
        <h2 className="text-sm font-semibold tracking-tight">
          Journal prompts
        </h2>
        <p className="text-xs text-muted-foreground">
          No prompts for this session.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-2">
      <h2 className="text-sm font-semibold tracking-tight">Journal prompts</h2>
      <ul className="space-y-2">
        {prompts.map((prompt, i) => (
          <li key={i} className="rounded-lg border border-border bg-card p-3">
            <label className="flex cursor-pointer items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={checked[i] ?? false}
                onChange={(e) =>
                  setChecked((prev) => ({ ...prev, [i]: e.target.checked }))
                }
                className="mt-0.5 h-4 w-4 accent-primary"
              />
              <span
                className={
                  checked[i] ? "text-foreground" : "text-muted-foreground"
                }
              >
                {prompt}
              </span>
            </label>
            <Textarea
              value={answers[i] ?? ""}
              onChange={(e) =>
                setAnswers((prev) => ({ ...prev, [i]: e.target.value }))
              }
              placeholder="Your reflection…"
              className="mt-2 min-h-[60px]"
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
