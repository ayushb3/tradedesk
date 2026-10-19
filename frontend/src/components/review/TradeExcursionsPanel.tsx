import { useState } from "react";

import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import { Skeleton } from "@/components/ui/skeleton";
import type { PostExitWindow, TradeExcursions } from "@/lib/api";
import { formatCurrency, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Props {
  data: TradeExcursions | undefined;
  isLoading: boolean;
  isError: boolean;
}

/** Signed R, e.g. "+1.20R" / "-0.40R"; an em dash when there is no value. */
function fmtR(r: number | null | undefined): string {
  if (r == null || !Number.isFinite(r)) return "—";
  return `${r > 0 ? "+" : ""}${formatNumber(r)}R`;
}

function fmtUsd(v: number): string {
  return `${v > 0 ? "+" : ""}${formatCurrency(v)}`;
}

/**
 * Trade excursions (#51): what the position went through during the hold (MAE /
 * MFE in $ and R, time under cost, the stop line) and, behind a toggle that is
 * off by default, the post-exit path. Process context only: no score, and
 * deliberately no "money left on the table" figure. R uses the desk ledger's
 * risk (the written max loss, else the default % of premium).
 */
export function TradeExcursionsPanel({ data, isLoading, isError }: Props) {
  const [showPostExit, setShowPostExit] = useState(false);

  if (isLoading) return <Skeleton className="h-28 w-full rounded-lg" />;
  if (isError || !data) {
    return (
      <Section>
        <p className="text-xs text-muted-foreground">Excursions could not be loaded.</p>
      </Section>
    );
  }

  const riskLabel =
    data.risk_source === "written_max_loss" ? "written max loss" : "default % of premium";

  return (
    <Section>
      {!data.available || !data.hold ? (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {data.reason ?? "Option bars unavailable"}. Fills are listed below.
          </p>
          <ul className="tabular space-y-0.5 text-xs text-muted-foreground">
            {data.fills.map((f, i) => (
              <li key={i}>
                {f.ts_pt.replace(/ PT$/, "")} {data.tz_label} · {f.side === "B" ? "Buy" : "Sell"}{" "}
                {f.qty} @ {formatNumber(f.px)}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
            <Stat label="MAE" sub={data.hold.mae_ts_pt ? `at ${data.hold.mae_ts_pt}` : "never under cost"}>
              <span className="tabular text-[#ef5350]">
                {fmtUsd(data.hold.mae_usd)} · {fmtR(data.hold.mae_r)}
              </span>
            </Stat>
            <Stat label="MFE" sub={data.hold.mfe_ts_pt ? `at ${data.hold.mfe_ts_pt}` : "never over cost"}>
              <span className="tabular text-[#26a69a]">
                {fmtUsd(data.hold.mfe_usd)} · {fmtR(data.hold.mfe_r)}
              </span>
            </Stat>
            <Stat label="Under cost" sub="minutes, during the hold">
              <span className="tabular">{data.hold.drawdown_minutes} min</span>
            </Stat>
            <Stat
              label="Stop line"
              sub={`${riskLabel}${data.risk != null ? ` · ${formatCurrency(data.risk)} risk` : ""}`}
            >
              <span className="tabular">
                {data.stop_price != null ? formatNumber(data.stop_price) : "—"}
              </span>
              {data.hold.crossed_stop && (
                <span className="ml-1 text-xs text-[#d9a441]">
                  crossed{data.hold.crossed_stop_at_pt ? ` ${data.hold.crossed_stop_at_pt}` : ""}
                </span>
              )}
            </Stat>
          </div>

          <div className="border-t border-border pt-2">
            <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={showPostExit}
                onChange={(e) => setShowPostExit(e.target.checked)}
                aria-label="Show the post-exit path"
              />
              Show post-exit path
            </label>
            {showPostExit && (
              <PostExit windows={data.post_exit} closed={data.status === "closed"} />
            )}
          </div>
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground">
        <span>Times in {data.tz_label}</span>
        {data.estimated && <EstimatedBadge />}
      </div>
    </Section>
  );
}

function PostExit({ windows, closed }: { windows: PostExitWindow[]; closed: boolean }) {
  if (!closed) {
    return <p className="mt-2 text-xs text-muted-foreground">Still open: no exit yet.</p>;
  }
  return (
    <div className="mt-2 space-y-1 opacity-60" data-testid="post-exit-path">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        After the final exit · best / worst, in R
      </p>
      <div className="grid grid-cols-3 gap-3">
        {windows.map((w) => (
          <div key={w.minutes} className="text-xs">
            <div className="text-muted-foreground">{w.minutes} min</div>
            <div className={cn("tabular")}>
              {fmtR(w.best_r)} / {fmtR(w.worst_r)}
            </div>
            {w.bars > 0 && !w.complete && (
              <div className="text-[10px] text-muted-foreground">partial window</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-background/40 p-4">
      <h2 className="mb-2 text-sm font-semibold tracking-tight">Excursions</h2>
      {children}
    </section>
  );
}

function Stat({
  label,
  sub,
  children,
}: {
  label: string;
  sub?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-sm">{children}</span>
      {sub && <span className="text-[10px] text-muted-foreground">{sub}</span>}
    </div>
  );
}
