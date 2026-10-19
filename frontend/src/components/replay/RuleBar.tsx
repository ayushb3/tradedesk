import { DISPLAY_TZ_LABEL, formatPnl } from "@/lib/format";
import { meterGeometry, type RuleBarState } from "@/lib/ruleBar";

const money = (n: number | null) =>
  n === null ? "—" : `${n < 0 ? "-" : n > 0 ? "+" : ""}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

/**
 * The replayed day's state before this trade: day P&L so far against the daily
 * stop (a meter), the target, the cutoff and the call source. Missing values
 * show "—"; nothing is invented.
 */
export function RuleBar({ state, loading }: { state: RuleBarState; loading: boolean }) {
  const { dayNet, stop, target, cutoff, entry, caller, priorTrades } = state;
  const geo = meterGeometry(dayNet, stop, target);
  const netTone = dayNet === null || dayNet === 0 ? "text-foreground" : dayNet > 0 ? "text-gain" : "text-loss";

  return (
    <section
      aria-label="Rule bar"
      className="flex shrink-0 flex-wrap items-center gap-x-8 gap-y-3 border-b border-border px-4 py-3"
    >
      <div className="flex items-center gap-4">
        <div>
          <div className="label-caps text-label text-muted-foreground">Day so far</div>
          <div className={`num text-key ${netTone}`}>{loading ? "…" : dayNet === null ? "—" : formatPnl(dayNet)}</div>
          <div className="num text-label text-muted-foreground">
            {dayNet === null
              ? "before this entry"
              : `${priorTrades} earlier ${priorTrades === 1 ? "trade" : "trades"}`}
          </div>
        </div>

        <div className="w-56">
          <div className="relative h-2 rounded-full bg-muted" role="img" aria-label="Day P&L against the daily stop and target">
            {geo && (
              <>
                <div
                  className={`absolute inset-y-0 ${dayNet! < 0 ? "bg-loss" : "bg-gain"}`}
                  style={{ left: `${geo.fillLeftPct}%`, width: `${geo.fillWidthPct}%` }}
                />
                <div className="absolute -inset-y-1 w-px bg-foreground/60" style={{ left: `${geo.zeroPct}%` }} />
              </>
            )}
          </div>
          <div className="num mt-1 flex justify-between text-label">
            <span className="text-stop">stop {money(stop)}</span>
            <span className="text-muted-foreground">target {money(target)}</span>
          </div>
        </div>
      </div>

      <div>
        <div className="label-caps text-label text-muted-foreground">Cutoff</div>
        <div className="flex items-baseline gap-2">
          <span className="num text-value">{cutoff ? `${cutoff} ${DISPLAY_TZ_LABEL}` : "—"}</span>
          {entry && (
            <span className={`text-label ${entry === "after" ? "text-loss" : "text-gain"}`}>
              entry {entry}
            </span>
          )}
        </div>
      </div>

      <div>
        <div className="label-caps text-label text-muted-foreground">Call source</div>
        <div className="text-value">{caller ?? "—"}</div>
      </div>
    </section>
  );
}
