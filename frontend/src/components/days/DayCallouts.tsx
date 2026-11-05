import { Link } from "react-router-dom";

import { RuleChip } from "@/components/days/RuleChip";
import type { CalloutRef, DayCalloutReport } from "@/lib/api";
import { calloutGap } from "@/lib/dayPage";

function callLabel(c: CalloutRef): string {
  const dir = c.side === "long" ? " calls" : c.side === "short" ? " puts" : "";
  return `${c.type} ${c.ticker}${dir}`;
}

/**
 * Post-close report: each fill next to the nearest captured callout (same ticker group
 * and direction, within the window). Report only, read after the close.
 */
export function DayCallouts({ report }: { report: DayCalloutReport | undefined }) {
  if (!report) return null;
  const { fills, calls_not_taken: notTaken } = report;
  return (
    <section className="space-y-2" data-testid="day-callouts">
      <div className="flex items-baseline gap-2">
        <h2 className="text-sm font-semibold tracking-tight">Fills vs. callouts</h2>
        {report.callouts_found && fills.length > 0 && (
          <span className="text-label text-muted-foreground">
            {report.matched} of {fills.length} fills near a call · within {report.window_min} min
          </span>
        )}
      </div>
      {!report.callouts_found ? (
        <p className="text-label text-muted-foreground">
          No captured callouts for this day. Run callout-listener during the call to fill this in.
        </p>
      ) : (
        <>
          {fills.length > 0 && (
            <ul className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
              {fills.map((f, i) => (
                <li key={`${f.trade_id}-${f.ts}-${i}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
                  <span className="num w-24 shrink-0 text-body text-muted-foreground">{f.ts_pt}</span>
                  <Link
                    to={`/trades/${f.trade_id}`}
                    className="min-w-0 flex-1 truncate text-body font-medium hover:underline"
                  >
                    {f.side === "B" ? "Buy" : "Sell"} {f.qty} {f.label ?? f.ticker}
                    <span className="num ml-2 font-normal text-muted-foreground">@ {f.px.toFixed(2)}</span>
                  </Link>
                  {f.callout && f.gap_s != null ? (
                    <span className="num shrink-0 text-label text-muted-foreground">
                      {callLabel(f.callout)} at {f.callout.ts_pt} · {calloutGap(f.gap_s)}
                    </span>
                  ) : (
                    <RuleChip label="No call nearby" tone="unknown" title={`No matching call within ${report.window_min} min`} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {notTaken.length > 0 && (
            <p className="text-label text-muted-foreground">
              Calls not taken: {notTaken.map((c) => `${callLabel(c)} ${c.ts_pt}`).join(" · ")}
            </p>
          )}
        </>
      )}
    </section>
  );
}
