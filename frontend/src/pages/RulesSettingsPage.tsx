import { Link } from "react-router-dom";

import { ApiErrorState } from "@/components/journal/ApiStates";
import { Skeleton } from "@/components/ui/skeleton";
import { formatPnl } from "@/lib/format";
import { useDeskRules } from "@/lib/queries";

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 px-4 py-2.5">
      <span className="text-body text-muted-foreground">{label}</span>
      <span className="num text-value">{value}</span>
    </div>
  );
}

/**
 * Rules: a small read-only settings page (the desk rules the app checks every
 * trade against). Not a nav item; reached from the gear in the nav footer.
 * Rules are edited through the rules conversation, not here.
 */
export function RulesSettingsPage() {
  const rules = useDeskRules();
  const r = rules.data;
  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4">
        <h1 className="font-semibold tracking-tight">Settings · Rules</h1>
        <Link to="/journal" className="ml-auto text-body text-primary hover:underline">
          Back to Days
        </Link>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {rules.isLoading && <Skeleton className="h-48 w-full rounded-lg" />}
          {rules.isError && <ApiErrorState error={rules.error} />}
          {r && (
            <>
              <div className="divide-y divide-border rounded-lg border border-border bg-card">
                {r.window?.start && r.window?.end && (
                  <Row label="Trading window (PT)" value={`${r.window.start} – ${r.window.end} PT`} />
                )}
                {r.daily_stop != null && <Row label="Daily stop" value={formatPnl(r.daily_stop)} />}
                {r.daily_target != null && <Row label="Daily target" value={formatPnl(r.daily_target)} />}
                {r.default_risk_pct != null && (
                  <Row label="Assumed stop when no max loss is written" value={`−${r.default_risk_pct}% of premium`} />
                )}
              </div>
              {r.rules && r.rules.length > 0 && (
                <ol className="divide-y divide-border rounded-lg border border-border bg-card">
                  {r.rules.map((x) => (
                    <li key={x.number} className="flex gap-3 px-4 py-2.5 text-body">
                      <span className="num text-muted-foreground">{x.number}</span>
                      <span>{x.text}</span>
                    </li>
                  ))}
                </ol>
              )}
              <p className="text-label text-muted-foreground">
                Read-only. To change a rule, use the rules conversation with the desk assistant.
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
