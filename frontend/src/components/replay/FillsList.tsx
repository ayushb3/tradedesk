import type { ReplayFill } from "@/lib/api";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import { fillValueText, fillsInTimeOrder, orderTypeLabel } from "@/lib/priceChart";
import { cn } from "@/lib/utils";

const clock = (timePt: string) => timePt.replace(/\s*PT$/, "");

/**
 * The real trade's fills, one row per fill in time order: side, time (PT, to the second),
 * qty @ price, dollar value and order type. Fills that land on one candle stay one row each,
 * which the chart markers cannot do. Shows only what the server revealed (the playhead gate),
 * so nothing here can leak a later fill. Clicking a row moves the playhead to that fill.
 */
export function FillsList({
  fills,
  onSeek,
}: {
  /** Fills the server revealed (already gated by the playhead). */
  fills: ReplayFill[];
  onSeek?: (fill: ReplayFill) => void;
}) {
  if (fills.length === 0) return null;
  const rows = fillsInTimeOrder(fills);
  return (
    <section className="space-y-0.5 px-3 pb-2 pt-1.5" aria-label="Your fills">
      <div className="flex items-baseline justify-between">
        <span className="label-caps text-label font-semibold text-muted-foreground">Your fills</span>
        <span className="text-label text-muted-foreground">
          {DISPLAY_TZ_LABEL}
          {onSeek ? " · click to jump" : ""}
        </span>
      </div>
      <ol className="num -mx-1 text-label">
        {rows.map((f, i) => {
          const isBuy = f.side === "buy";
          const type = orderTypeLabel(f.order_type);
          return (
            <li key={`${f.seq}:${f.ts}:${i}`}>
              <button
                type="button"
                disabled={!onSeek}
                onClick={() => onSeek?.(f)}
                title={`${isBuy ? "Buy" : "Sell"} ${f.qty} @ ${f.price.toFixed(2)} at ${f.time_pt}${
                  type ? `, ${f.order_type} order` : ""
                }`}
                className={cn(
                  "grid w-full grid-cols-[0.75rem_auto_minmax(0,1fr)_auto_2rem] items-baseline gap-x-1.5 whitespace-nowrap rounded px-1 py-0.5 text-left",
                  onSeek && "transition-colors hover:bg-muted",
                )}
              >
                <span className={cn("font-semibold", isBuy ? "text-gain" : "text-loss")}>
                  {isBuy ? "B" : "S"}
                </span>
                <span className="text-muted-foreground">{clock(f.time_pt)}</span>
                <span className="truncate">
                  {f.qty} @ {f.price.toFixed(2)}
                </span>
                <span className="text-right">{fillValueText(f.value)}</span>
                <span className="truncate text-right text-muted-foreground">{type || "—"}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
