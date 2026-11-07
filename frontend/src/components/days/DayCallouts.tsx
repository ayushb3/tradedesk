import { Link } from "react-router-dom";

import { RuleChip } from "@/components/days/RuleChip";
import { CollapsibleSection } from "@/components/ui/collapsible-section";
import type { CalloutRef, DayCalloutReport } from "@/lib/api";
import { calloutGap } from "@/lib/dayPage";

function callLabel(c: CalloutRef): string {
  const dir = c.side === "long" ? " calls" : c.side === "short" ? " puts" : "";
  return `${c.type} ${c.ticker}${dir}`;
}

/** One line for the folded section: "3 of 5 fills near a call · 2 unmatched · 1 call not taken". */
export function calloutsSummary(report: DayCalloutReport): string {
  const n = report.fills.length;
  const parts = n > 0 ? [`${report.matched} of ${n} fills near a call`] : ["no fills"];
  if (n - report.matched > 0) parts.push(`${n - report.matched} unmatched`);
  const nt = report.calls_not_taken.length;
  if (nt > 0) parts.push(`${nt} ${nt === 1 ? "call" : "calls"} not taken`);
  return parts.join(" · ");
}

/**
 * Post-close report: each fill next to the nearest captured callout (same ticker group
 * and direction, within the window). Report only, read after the close. With no captured
 * callouts it is a single muted line; with callouts it folds to a one-line summary.
 */
export function DayCallouts({ report }: { report: DayCalloutReport | undefined }) {
  if (!report) return null;
  const { fills, calls_not_taken: notTaken } = report;
  if (!report.callouts_found) {
    return (
      <p className="text-label text-muted-foreground" data-testid="day-callouts">
        Fills vs. callouts: no callouts captured this day (run callout-listener during the call).
      </p>
    );
  }
  return (
    <CollapsibleSection
      id="day.callouts"
      title="Fills vs. callouts"
      summary={`${calloutsSummary(report)} · within ${report.window_min} min`}
      defaultOpen={false}
      testId="day-callouts"
    >
      <div className="space-y-2">
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
      </div>
    </CollapsibleSection>
  );
}
