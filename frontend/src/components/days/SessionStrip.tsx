import { useNavigate } from "react-router-dom";

import { linePoints, stripWindow, tradeBands } from "@/lib/dayPage";
import type { DeskPosition } from "@/lib/api";
import { formatClock, DISPLAY_TZ_LABEL } from "@/lib/format";
import { useReplaySession } from "@/lib/queries";
import { cn } from "@/lib/utils";

const W = 1000;
const H = 80;
const BAND: Record<"gain" | "loss" | "flat", string> = {
  gain: "bg-gain/30 border-gain/70 hover:bg-gain/50",
  loss: "bg-loss/30 border-loss/70 hover:bg-loss/50",
  flat: "bg-grey/30 border-grey/70 hover:bg-grey/50",
};

/**
 * SPY for the session as a slim line, with every trade drawn as a green/red band
 * over its hold. A click on a band opens that trade review.
 */
export function SessionStrip({ date, positions }: { date: string; positions: DeskPosition[] }) {
  const navigate = useNavigate();
  const { data, isLoading } = useReplaySession({ symbol: "SPY", date });

  // Regular session (06:30-13:00 PT), widened only to take in trades outside it.
  const { startMs, endMs } = stripWindow(date, positions);
  const closes = (data?.candles ?? [])
    .map((c) => ({ ms: Date.parse(c.ts), close: c.close }))
    .filter((c) => c.ms >= startMs && c.ms < endMs);
  const haveBars = closes.length > 1;

  const bands = tradeBands(positions, startMs, endMs);
  const pts = haveBars ? linePoints(closes, W, H, startMs, endMs) : "";
  const iso = (ms: number) => new Date(ms).toISOString();

  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between">
        <span className="label-caps text-label text-muted-foreground">SPY, proxy for SPX</span>
        {!haveBars && !isLoading && (
          <span className="text-label text-muted-foreground">SPY bars unavailable</span>
        )}
      </div>
      <div className="relative h-24 overflow-hidden rounded-lg border border-border bg-card">
        {pts && (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            className="absolute inset-x-0 top-2 h-20 w-full"
          >
            <polyline
              points={pts}
              fill="none"
              stroke="hsl(var(--grey))"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        )}
        {bands.map((b) => (
          <button
            key={b.tradeId}
            type="button"
            onClick={() => navigate(`/trades/${b.tradeId}`)}
            aria-label={`Open trade ${b.tradeId} review`}
            title={`Trade ${b.tradeId}`}
            style={{ left: `${b.leftPct}%`, width: `${b.widthPct}%` }}
            className={cn("absolute inset-y-0 min-w-[6px] border-x transition-colors", BAND[b.tone])}
          />
        ))}
      </div>
      {(
        <div className="num flex justify-between text-label text-muted-foreground">
          <span>
            {formatClock(iso(startMs))} {DISPLAY_TZ_LABEL}
          </span>
          <span>
            {formatClock(iso(endMs))} {DISPLAY_TZ_LABEL}
          </span>
        </div>
      )}
    </section>
  );
}
