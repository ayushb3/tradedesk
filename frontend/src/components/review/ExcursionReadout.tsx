import type { Excursion } from "@/lib/api";
import { formatNumber, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * MFE / MAE readout (PRD-M7 R9). Renders the max favorable and adverse
 * excursion over the hold window as points + % of entry. Labeled *estimated*
 * when the source series is synthetic (BS premium). Basis-agnostic: the caller
 * passes the excursion from either the premium (options) or underlying
 * (shares) series.
 */
export function ExcursionReadout({ excursion }: { excursion: Excursion }) {
  const basisLabel =
    excursion.basis === "premium" ? "premium" : "underlying";
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
      <Metric
        label="MFE"
        title="Max favorable excursion"
        value={excursion.mfe}
        pct={excursion.mfe_pct}
        colorClass="text-[#26a69a]"
      />
      <Metric
        label="MAE"
        title="Max adverse excursion"
        value={excursion.mae}
        pct={excursion.mae_pct}
        colorClass="text-[#ef5350]"
      />
      <span className="text-label uppercase tracking-wide text-muted-foreground">
        {basisLabel} basis
        {excursion.estimated && (
          <span className="ml-1 text-[#d9a441]">· estimated</span>
        )}
      </span>
    </div>
  );
}

function Metric({
  label,
  title,
  value,
  pct,
  colorClass,
}: {
  label: string;
  title: string;
  value: number;
  pct: number;
  colorClass: string;
}) {
  return (
    <div className="flex flex-col" title={title}>
      <span className="text-label uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className={cn("tabular text-sm font-medium", colorClass)}>
        {formatNumber(value)}{" "}
        <span className="text-xs font-normal text-muted-foreground">
          ({formatPercent(pct, { fromRatio: false, digits: 1 })})
        </span>
      </span>
    </div>
  );
}
