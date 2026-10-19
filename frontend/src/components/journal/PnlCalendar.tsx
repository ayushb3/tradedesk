import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { CalendarData } from "@/lib/api";
import { ruleName, type DayMark } from "@/lib/daysHome";
import { formatPnl, PNL_GREEN, PNL_RED } from "@/lib/format";
import { cn } from "@/lib/utils";

interface PnlCalendarProps {
  /** Month in `YYYY-MM`. */
  month: string;
  data: CalendarData | undefined;
  loading: boolean;
  onMonthChange: (month: string) => void;
  /** Clicking a day with trades jumps elsewhere (e.g. daily review). */
  onSelectDay?: (date: string) => void;
  /** Rule state per date: a red dot per broken rule, a red outline on a stop breach. */
  marks?: Record<string, DayMark>;
}

const WEEKDAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, (m ?? 1) - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, (m ?? 1) - 1, 1));
  return d.toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** react-only month grid; each day cell is tinted by its net P&L intensity. */
export function PnlCalendar({
  month,
  data,
  loading,
  onMonthChange,
  onSelectDay,
  marks,
}: PnlCalendarProps) {
  const [y, m] = month.split("-").map(Number);
  const year = y ?? new Date().getUTCFullYear();
  const monthIdx = (m ?? 1) - 1;

  const firstWeekday = new Date(Date.UTC(year, monthIdx, 1)).getUTCDay();
  const daysInMonth = new Date(Date.UTC(year, monthIdx + 1, 0)).getUTCDate();

  const days = data ?? {};
  const maxAbs = Object.values(days).reduce(
    (max, d) => Math.max(max, Math.abs(d.pnl)),
    0,
  );

  // Monthly total across all populated days.
  const monthTotal = Object.values(days).reduce((sum, d) => sum + d.pnl, 0);

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstWeekday; i++) cells.push(null);
  for (let day = 1; day <= daysInMonth; day++) cells.push(day);

  return (
    <div className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div>
          <h3 className="text-sm font-semibold tracking-tight">
            {monthLabel(month)}
          </h3>
          {!loading && (
            <p className="tabular text-label text-muted-foreground">
              Month net {formatPnl(monthTotal)}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            aria-label="Previous month"
            onClick={() => onMonthChange(shiftMonth(month, -1))}
          >
            <ChevronLeft />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7"
            aria-label="Next month"
            onClick={() => onMonthChange(shiftMonth(month, 1))}
          >
            <ChevronRight />
          </Button>
        </div>
      </div>

      <div className="p-3">
        {loading ? (
          <Skeleton className="h-56 w-full" />
        ) : (
          <>
            <div className="mb-1 grid grid-cols-7 gap-1">
              {WEEKDAYS.map((w) => (
                <div
                  key={w}
                  className="py-1 text-center text-label font-medium uppercase tracking-wide text-muted-foreground"
                >
                  {w}
                </div>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {cells.map((day, i) => {
                if (day === null)
                  return <div key={`pad-${i}`} />;
                const iso = `${month}-${String(day).padStart(2, "0")}`;
                const cell = days[iso];
                return (
                  <DayCell
                    key={iso}
                    day={day}
                    iso={iso}
                    pnl={cell?.pnl}
                    trades={cell?.trades}
                    wins={cell?.wins}
                    losses={cell?.losses}
                    maxAbs={maxAbs}
                    mark={marks?.[iso]}
                    onSelect={onSelectDay}
                  />
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function DayCell({
  day,
  iso,
  pnl,
  trades,
  wins,
  losses,
  maxAbs,
  mark,
  onSelect,
}: {
  day: number;
  iso: string;
  pnl: number | undefined;
  trades: number | undefined;
  wins: number | undefined;
  losses: number | undefined;
  maxAbs: number;
  mark?: DayMark;
  onSelect?: (date: string) => void;
}) {
  const hasTrades = trades != null && trades > 0;
  // Intensity 0.12..0.5 scaled by |pnl| relative to the busiest day.
  const intensity =
    hasTrades && pnl != null && maxAbs > 0
      ? 0.12 + 0.38 * (Math.abs(pnl) / maxAbs)
      : 0;
  const base = pnl != null && pnl >= 0 ? PNL_GREEN : PNL_RED;
  const bg = hasTrades ? hexWithAlpha(base, intensity) : undefined;

  const interactive = hasTrades && Boolean(onSelect);

  // W/L record: use wins/losses if both are available, else fall back to trade count.
  const hasWL = wins != null && losses != null;
  const wlLabel = hasWL ? `${wins}W-${losses}L` : `${trades}t`;

  // Tooltip: include W/L record when available.
  const wlTooltip = hasWL
    ? ` · ${wins}W-${losses}L`
    : trades != null
      ? ` · ${trades} trade${trades === 1 ? "" : "s"}`
      : "";

  return (
    <button
      type="button"
      disabled={!interactive}
      onClick={interactive ? () => onSelect?.(iso) : undefined}
      style={bg ? { backgroundColor: bg } : undefined}
      className={cn(
        "flex min-h-[4.75rem] flex-col items-start justify-between rounded-md border p-1.5 text-left transition-colors",
        mark?.stopBreach
          ? "border-2 border-loss"
          : hasTrades
            ? "border-border/60"
            : "border-transparent",
        interactive ? "cursor-pointer hover:ring-1 hover:ring-ring" : "cursor-default",
      )}
      title={
        hasTrades
          ? `${iso}: ${formatPnl(pnl)}${wlTooltip}${
              mark?.stopBreach ? " · broke the daily stop" : ""
            }${mark?.broken.length ? ` · broken: ${mark.broken.map(ruleName).join(", ")}` : ""}`
          : iso
      }
    >
      <span className="flex w-full items-center justify-between gap-1">
        <span
          className={cn(
            "text-label tabular",
            hasTrades ? "text-foreground/90" : "text-muted-foreground",
          )}
        >
          {day}
        </span>
        {mark && mark.broken.length > 0 && (
          <span className="flex items-center gap-0.5" aria-label={`${mark.broken.length} rules broken`}>
            {mark.broken.map((c) => (
              <span key={c} className="h-1.5 w-1.5 rounded-full bg-loss" />
            ))}
          </span>
        )}
      </span>
      {hasTrades && (
        <span className="w-full">
          <span className="num block truncate text-value font-semibold leading-tight">
            {compactPnl(pnl)}
          </span>
          <span className="tabular block text-label leading-tight text-foreground/70">
            {wlLabel}
          </span>
        </span>
      )}
    </button>
  );
}

/** Add an alpha channel to a #rrggbb hex color. */
function hexWithAlpha(hex: string, alpha: number): string {
  const a = Math.round(Math.min(1, Math.max(0, alpha)) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${hex}${a}`;
}

/** Compact P&L for a tight day cell, e.g. `+1.2k`, `-318`. */
function compactPnl(n: number | undefined): string {
  if (n == null || !Number.isFinite(n)) return "";
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1000) return `${sign}${(abs / 1000).toFixed(1)}k`;
  return `${sign}${Math.round(abs)}`;
}
