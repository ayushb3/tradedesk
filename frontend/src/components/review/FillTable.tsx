import type { ReviewFill } from "@/lib/api";
import { fillValueText, orderTypeLabel } from "@/lib/priceChart";
import { formatClockSeconds } from "@/lib/secondsReplay";
import { isoToUtcSeconds } from "@/lib/chart";
import { cn } from "@/lib/utils";

interface FillTableProps {
  fills: ReviewFill[];
  /** Currently-hovered fill id (shared with the chart markers), or null. */
  hoveredFillId: number | null;
  onHoverFill: (id: number | null) => void;
}

/**
 * The trade's executed fills, one row each, ordered as delivered (seq asc).
 * Hovering a row raises `onHoverFill` so the matching chart marker highlights;
 * `hoveredFillId` (driven from the page) highlights the row when the marker
 * side does the hovering — a two-way link. Numbers are neutral/tabular; this
 * is an execution log, not a scorecard.
 */
export function FillTable({ fills, hoveredFillId, onHoverFill }: FillTableProps) {
  if (fills.length === 0) {
    return (
      <p className="rounded-lg border border-border bg-background/40 p-3 text-xs text-muted-foreground">
        No fills recorded for this trade.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border bg-background/40 text-label uppercase tracking-wide text-muted-foreground">
            <th className="px-1.5 py-2 text-left font-medium">Time (PT)</th>
            <th className="px-1.5 py-2 text-left font-medium">Side</th>
            <th className="px-1.5 py-2 text-left font-medium">Role</th>
            <th className="px-1.5 py-2 text-right font-medium">Qty</th>
            {/* Value sits under Price, so all eight fields fit the 360px review panel. */}
            <th className="px-1.5 py-2 text-right font-medium" title="Fill price, with the fill value under it">Price</th>
            <th className="px-1.5 py-2 text-left font-medium">Type</th>
            <th className="px-1.5 py-2 text-right font-medium">Fees</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f) => {
            const hovered = f.id === hoveredFillId;
            const isBuy = f.side === "buy";
            return (
              <tr
                key={f.id}
                onMouseEnter={() => onHoverFill(f.id)}
                onMouseLeave={() => onHoverFill(null)}
                className={cn(
                  "cursor-default border-b border-border/60 transition-colors last:border-0",
                  hovered ? "bg-muted" : "hover:bg-muted/50",
                )}
              >
                <td className="tabular px-1.5 py-1.5 text-muted-foreground">
                  {formatClockSeconds(isoToUtcSeconds(f.ts))}
                </td>
                <td className="px-1.5 py-1.5">
                  <span
                    className={cn(
                      "font-medium uppercase",
                      isBuy ? "text-[#26a69a]" : "text-[#ef5350]",
                    )}
                  >
                    {f.side}
                  </span>
                </td>
                <td className="px-1.5 py-1.5">
                  <span className="capitalize text-muted-foreground">
                    {f.role}
                  </span>
                </td>
                <td className="tabular px-1.5 py-1.5 text-right">{f.qty}</td>
                <td className="tabular px-1.5 py-1.5 text-right">
                  <div>{f.price.toFixed(2)}</div>
                  <div className="text-muted-foreground">
                    {fillValueText(Math.round(f.qty * f.price * f.multiplier * 100) / 100)}
                  </div>
                </td>
                <td className="max-w-16 truncate px-1.5 py-1.5 text-muted-foreground" title={f.order_type ?? undefined}>
                  {orderTypeLabel(f.order_type) || "—"}
                </td>
                <td className="tabular px-1.5 py-1.5 text-right text-muted-foreground">
                  {f.fees.toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
