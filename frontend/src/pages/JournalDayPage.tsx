import { useEffect, useState } from "react";
import { ArrowLeft, CalendarDays, ChevronLeft, ChevronRight, Play } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { DayCallouts } from "@/components/days/DayCallouts";
import { DayJournal } from "@/components/days/DayJournal";
import { DayPlans } from "@/components/days/DayPlans";
import { SessionStrip } from "@/components/days/SessionStrip";
import { TradeRows } from "@/components/days/TradeRows";
import { VerdictBand } from "@/components/days/VerdictBand";
import { Button } from "@/components/ui/button";
import { CollapsibleSection } from "@/components/ui/collapsible-section";
import { Calendar } from "@/components/ui/calendar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiErrorState, EmptyState } from "@/components/journal/ApiStates";
import { ApiError, type DeskPosition } from "@/lib/api";
import { dayNavKey, dayVerdict, isTypingTarget, replayDayHref, tradeRows } from "@/lib/dayPage";
import { adjacentTradingDay, tradingDays } from "@/lib/daysHome";
import { formatDayMonth } from "@/lib/format";
import { useDailyReview, useDayCallouts, useDeskDay, useDeskDays } from "@/lib/queries";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** "4 trades · 2 rule breaks" for the folded Trades header. */
function tradesSummary(positions: DeskPosition[]): string {
  const n = positions.length;
  const broken = positions.filter((p) => p.rule_results.some((r) => r.kept === false)).length;
  const head = `${n} ${n === 1 ? "trade" : "trades"}`;
  return broken > 0 ? `${head} · ${broken} with a rule break` : `${head} · no rule breaks`;
}

/** Local-midnight Date for a `YYYY-MM-DD` (what react-day-picker works in). */
function isoToLocal(iso: string): Date | undefined {
  if (!ISO_DATE.test(iso)) return undefined;
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function localToIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * A session day: the verdict band (net, sum of R, rule chips), the SPY session
 * strip with each trade as a band, the trades as time-ordered rows, the day's plans
 * and the day journal (saved). The header steps between trading days (`[` / `]` or
 * Alt+arrows) and opens a replay of the day. Reached from the Days calendar.
 */
export function JournalDayPage() {
  const { date = "" } = useParams<{ date: string }>();
  const navigate = useNavigate();
  const { data: day, isLoading, isError, error } = useDeskDay(date);
  // Reflection prompts come from the review payload, keyed on the same :date.
  const review = useDailyReview(date);
  const callouts = useDayCallouts(date);
  const days = useDeskDays();

  const allDays = days.data?.days ?? [];
  const prevDay = adjacentTradingDay(allDays, date, -1);
  const nextDay = adjacentTradingDay(allDays, date, 1);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target)) return;
      const dir = dayNavKey(e);
      if (dir == null) return;
      const to = dir < 0 ? prevDay : nextDay;
      e.preventDefault(); // Alt+arrow is also browser back/forward
      if (to) navigate(`/days/${to}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, prevDay, nextDay]);

  const noDay = isError && error instanceof ApiError && error.status === 404;
  const positions = day?.positions ?? [];
  const validDate = ISO_DATE.test(date);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate("/days")}
          className="gap-1.5"
        >
          <ArrowLeft className="h-4 w-4" />
          Days
        </Button>

        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            disabled={!prevDay}
            onClick={() => prevDay && navigate(`/days/${prevDay}`)}
            aria-label={prevDay ? `Previous trading day, ${formatDayMonth(prevDay)}` : "No earlier trading day"}
            title="Previous trading day ( [ or Alt+← )"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <DayPicker
            date={date}
            tradingDates={tradingDays(allDays)}
            onPick={(d) => navigate(`/days/${d}`)}
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-8 w-8"
            disabled={!nextDay}
            onClick={() => nextDay && navigate(`/days/${nextDay}`)}
            aria-label={nextDay ? `Next trading day, ${formatDayMonth(nextDay)}` : "No later trading day"}
            title="Next trading day ( ] or Alt+→ )"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>

        {positions.length > 0 && (
          <span className="num text-label text-muted-foreground">
            {positions.length} {positions.length === 1 ? "trade" : "trades"}
          </span>
        )}

        {validDate && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto gap-1.5"
            onClick={() => navigate(replayDayHref(date))}
          >
            <Play className="h-4 w-4" />
            Replay day
          </Button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-5xl flex-col gap-4">
          {isLoading ? (
            <>
              <Skeleton className="h-28 w-full rounded-lg" />
              <Skeleton className="h-28 w-full rounded-lg" />
              <Skeleton className="h-48 w-full rounded-lg" />
            </>
          ) : noDay || (day && positions.length === 0) ? (
            <EmptyState
              title="No trades this day"
              description="Nothing was logged for this session. Pick another day from the Days calendar to review your process."
            />
          ) : isError || !day ? (
            <ApiErrorState error={error} />
          ) : (
            <>
              {/* Summary first: net, R and rule chips, then the plans line. The long
                  sections below fold (remembered per viewer). */}
              <VerdictBand verdict={dayVerdict(day)} />
              <DayPlans plans={day.plans ?? []} notTaken={day.plans_not_taken ?? 0} />
              <SessionStrip date={date} positions={positions} />
              <CollapsibleSection
                id="day.trades"
                title="Trades"
                summary={tradesSummary(positions)}
                testId="day-trades"
              >
                <TradeRows rows={tradeRows(positions)} />
              </CollapsibleSection>
              <DayCallouts report={callouts.data} />
            </>
          )}
          {validDate && !isLoading && (
            <DayJournal key={date} date={date} prompts={review.data?.prompts ?? []} />
          )}
        </div>
      </div>
    </div>
  );
}

/** The day title as a button that opens a small calendar; only trading days can be picked. */
function DayPicker({
  date,
  tradingDates,
  onPick,
}: {
  date: string;
  tradingDates: string[];
  onPick: (date: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = isoToLocal(date);
  const trading = new Set(tradingDates);
  const first = isoToLocal(tradingDates[0] ?? "");
  const last = isoToLocal(tradingDates[tradingDates.length - 1] ?? "");

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2 px-2" aria-label="Pick a trading day">
          <CalendarDays className="h-4 w-4 text-muted-foreground" />
          <h1 className="font-semibold tracking-tight">{formatDayMonth(date)}</h1>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="single"
          selected={selected}
          defaultMonth={selected}
          fromDate={first}
          toDate={last}
          disabled={(d) => !trading.has(localToIso(d))}
          onSelect={(d) => {
            if (!d) return;
            setOpen(false);
            onPick(localToIso(d));
          }}
        />
        <p className="border-t border-border px-3 py-2 text-label text-muted-foreground">
          Days with trades. <kbd className="num">[</kbd> / <kbd className="num">]</kbd> step between them.
        </p>
      </PopoverContent>
    </Popover>
  );
}
