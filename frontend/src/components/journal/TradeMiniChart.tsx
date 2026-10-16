import { useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { ReviewChart } from "@/components/review/ReviewChart";
import { isoToUtcSeconds } from "@/lib/chart";
import { useCandles, useTradeReview } from "@/lib/queries";

interface TradeMiniChartProps {
  tradeId: number;
}

/**
 * A compact, read-only underlying candle chart scoped to a single trade's hold
 * window, with entry/exit fill markers. Reuses the full ReviewChart wrapper (so
 * markers, ET axis formatting, and fill vocabulary stay identical to the
 * cockpit) at a small fixed height — no replay controls, no timeframe switch.
 *
 * Rendered inline when a day card is expanded; full analysis stays on the
 * cockpit (/trades/:id). Fetches are enabled only while mounted, so collapsed
 * cards cost nothing.
 */
export function TradeMiniChart({ tradeId }: TradeMiniChartProps) {
  const review = useTradeReview(tradeId);
  const underlying = review.data?.underlying;
  const fills = review.data?.fills ?? [];

  const candles = useCandles({
    symbol: underlying?.symbol ?? "",
    date: underlying?.session_date ?? "",
    timeframe: "1m",
  });

  // Frame the chart to the hold window: keep bars from just before the first
  // fill through just after the last, with a small margin so entry/exit
  // markers aren't flush against the edges. Falls back to the full day if the
  // fill window can't be derived.
  const windowed = useMemo(() => {
    const all = candles.data ?? [];
    if (all.length === 0 || fills.length === 0) return all;
    const times = fills.map((f) => isoToUtcSeconds(f.ts) as number);
    const first = Math.min(...times);
    const last = Math.max(...times);
    const margin = 5 * 60; // 5 minutes of bars on each side
    const lo = first - margin;
    const hi = last + margin;
    const clipped = all.filter((c) => {
      const t = isoToUtcSeconds(c.ts) as number;
      return t >= lo && t <= hi;
    });
    return clipped.length > 0 ? clipped : all;
  }, [candles.data, fills]);

  // Local hover state — the mini chart's crosshair is self-contained (no fill
  // table to link to), so the ReviewChart's two-way hover just talks to itself.
  const [hoveredFillId, setHoveredFillId] = useState<number | null>(null);

  const loading = review.isLoading || candles.isLoading;
  const failed = review.isError || candles.isError;

  return (
    <div className="h-40 w-full rounded-md border border-border bg-background/40">
      {loading ? (
        <Skeleton className="h-full w-full rounded-md" />
      ) : failed || windowed.length === 0 ? (
        <div className="flex h-full items-center justify-center text-[10px] text-muted-foreground">
          No candles for this window
        </div>
      ) : (
        <ReviewChart
          candles={windowed}
          fills={fills}
          timeframe="1m"
          hoveredFillId={hoveredFillId}
          onHoverFill={setHoveredFillId}
        />
      )}
    </div>
  );
}
