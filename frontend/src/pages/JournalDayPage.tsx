import { useState } from "react";
import { ArrowLeft, CalendarDays } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { SessionStrip } from "@/components/days/SessionStrip";
import { TradeRows } from "@/components/days/TradeRows";
import { VerdictBand } from "@/components/days/VerdictBand";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiErrorState, EmptyState } from "@/components/journal/ApiStates";
import { ApiError } from "@/lib/api";
import { dayVerdict, tradeRows } from "@/lib/dayPage";
import { formatDayMonth } from "@/lib/format";
import { useDailyReview, useDeskDay } from "@/lib/queries";

/**
 * A session day: the verdict band (net, sum of R, rule chips), the SPY session
 * strip with each trade as a band, and the trades as time-ordered rows. Rows and
 * bands open the trade review. Reached from the Journal calendar.
 */
export function JournalDayPage() {
  const { date = "" } = useParams<{ date: string }>();
  const navigate = useNavigate();
  const { data: day, isLoading, isError, error } = useDeskDay(date);
  // Reflection prompts come from the review payload, keyed on the same :date.
  const review = useDailyReview(date);

  const noDay = isError && error instanceof ApiError && error.status === 404;
  const positions = day?.positions ?? [];

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
          <h1 className="font-semibold tracking-tight">{formatDayMonth(date)}</h1>
          {positions.length > 0 && (
            <span className="num text-label text-muted-foreground">
              {positions.length} {positions.length === 1 ? "trade" : "trades"}
            </span>
          )}
        </div>
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
              description="Nothing was logged for this session. Pick another day from the Journal calendar to review your process."
            />
          ) : isError || !day ? (
            <ApiErrorState error={error} />
          ) : (
            <>
              <VerdictBand verdict={dayVerdict(day)} />
              <SessionStrip date={date} positions={positions} />
              <TradeRows rows={tradeRows(positions)} />
              <JournalPrompts key={date} prompts={review.data?.prompts ?? []} />
            </>
          )}
        </div>
      </div>
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
