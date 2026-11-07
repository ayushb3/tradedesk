import { Link } from "react-router-dom";

import { RuleChip } from "@/components/days/RuleChip";
import { CollapsibleSection } from "@/components/ui/collapsible-section";
import type { DeskPlan } from "@/lib/api";
import { formatCurrency } from "@/lib/format";

function contract(p: DeskPlan): string {
  const side = p.option_type === "CALL" ? "C" : p.option_type === "PUT" ? "P" : "";
  return p.strike != null ? `${p.symbol} ${p.strike}${side}` : p.symbol;
}

const usd0 = (n: number) => formatCurrency(n).replace(/\.00$/, "");

/** One line for the day summary: "2 written · 1 not taken · 1 late". */
export function plansSummary(plans: DeskPlan[], notTaken: number): string {
  if (plans.length === 0) return "none written";
  const late = plans.filter((p) => p.late).length;
  return [
    `${plans.length} written`,
    notTaken > 0 ? `${notTaken} not taken` : null,
    late > 0 ? `${late} written late` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/**
 * The plan cards written for the day, compact: time, caller, contract, size and max
 * loss, and whether it was taken. A plan not taken is a good outcome, not a miss.
 * Folds to its one-line summary (remembered per viewer).
 */
export function DayPlans({ plans, notTaken }: { plans: DeskPlan[]; notTaken: number }) {
  const sorted = [...plans].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
  return (
    <CollapsibleSection
      id="day.plans"
      title="Plans"
      summary={plansSummary(plans, notTaken)}
      defaultOpen={false}
      testId="day-plans"
    >
      {plans.length === 0 ? (
        <p className="text-label text-muted-foreground">No plan written for this day.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
          {sorted.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
              <span className="num w-20 shrink-0 text-body text-muted-foreground">{p.created_pt}</span>
              <span className="min-w-0 flex-1 truncate text-body font-medium">
                {contract(p)}
                <span className="ml-2 font-normal text-muted-foreground">{p.caller}</span>
              </span>
              <span className="num shrink-0 text-label text-muted-foreground">
                size {usd0(p.size_usd)} · max loss {usd0(p.max_loss_usd)}
                {p.target ? ` · target ${p.target}` : ""}
              </span>
              <span className="flex shrink-0 items-center gap-1.5">
                {p.late && <RuleChip label="Written late" tone="broken" title="Written after the entry" />}
                {p.status === "matched" && p.matched_trade_id != null ? (
                  <Link to={`/trades/${p.matched_trade_id}`} className="text-label font-medium text-play hover:underline">
                    Taken
                  </Link>
                ) : (
                  <RuleChip
                    label={p.status === "unmatched" ? "Not taken" : "Open"}
                    tone={p.status === "unmatched" ? "kept" : "unknown"}
                  />
                )}
              </span>
              {p.note && <p className="w-full text-label text-muted-foreground">{p.note}</p>}
            </li>
          ))}
        </ul>
      )}
    </CollapsibleSection>
  );
}
