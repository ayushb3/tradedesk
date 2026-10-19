import { useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import { ExcursionReadout } from "@/components/review/ExcursionReadout";
import { ReviewChart, type PriceBand } from "@/components/review/ReviewChart";
import { Button } from "@/components/ui/button";
import { isoToUtcSeconds } from "@/lib/chart";
import { premiumWindow } from "@/lib/premiumWindow";
import type {
  Candle,
  Excursion,
  ReviewFill,
  ReviewOption,
  Trade,
} from "@/lib/api";

// MFE/MAE band colors mirror the up/down candle theme (favorable = teal).
const MFE_COLOR = "#26a69a";
const MAE_COLOR = "#ef5350";
const STOP_COLOR = "#d9a441";

interface OptionPremiumSectionProps {
  option: ReviewOption;
  premiumBars: Candle[] | null;
  fills: ReviewFill[];
  excursion: Excursion | null;
  /** Premium where the position has lost its risk (#51); drawn as a "Stop" line. */
  stopPrice?: number | null;
  trade: Trade;
  hoveredFillId: number | null;
  onHoverFill: (id: number | null) => void;
}

/**
 * Compute MFE/MAE horizontal bands in premium-price space. The excursion gives
 * magnitudes relative to the entry premium; the extreme levels depend on
 * direction (a long profits as premium rises, a short as it falls).
 */
function excursionBands(
  excursion: Excursion | null,
  trade: Trade,
): PriceBand[] {
  if (!excursion || excursion.basis !== "premium") return [];
  const entry = trade.avg_entry;
  if (entry == null || !Number.isFinite(entry)) return [];
  const isLong = trade.direction === "long";
  const favorable = isLong ? entry + excursion.mfe : entry - excursion.mfe;
  const adverse = isLong ? entry - excursion.mae : entry + excursion.mae;
  return [
    { price: favorable, color: MFE_COLOR, title: "MFE" },
    { price: adverse, color: MAE_COLOR, title: "MAE" },
  ];
}

/**
 * Option premium chart (PRD-M7 R4/R5/R9): the contract's own price series over
 * the hold, with the same entry/exit fill markers as the underlying chart —
 * placed at the fill premium prices (the fills' `price` IS the premium for
 * option trades). Shows an "estimated premium" badge when the series is the
 * Black-Scholes fallback, MFE/MAE readouts, and MFE/MAE bands on the chart.
 *
 * Premium bars are 1m (the review payload's series); the timeframe selector
 * governs the underlying chart only.
 */
export function OptionPremiumSection({
  option,
  premiumBars,
  fills,
  excursion,
  stopPrice,
  trade,
  hoveredFillId,
  onHoverFill,
}: OptionPremiumSectionProps) {
  const estimated = option.synthetic || option.method === "bs_interp";
  const bands = excursionBands(excursion, trade);
  if (stopPrice != null && Number.isFinite(stopPrice)) {
    bands.push({ price: stopPrice, color: STOP_COLOR, title: "Stop" });
  }
  const bars = premiumBars ?? [];

  // Open on the trade (entry-15m .. exit+30m), not the whole session; "Whole
  // session" (or normal zoom/pan) widens it. Bars after the exit are greyed.
  const [wholeSession, setWholeSession] = useState(false);
  const exitS = trade.close_ts ? isoToUtcSeconds(trade.close_ts) : null;
  const visibleRange = useMemo(() => {
    if (bars.length === 0) return null;
    return premiumWindow(
      isoToUtcSeconds(trade.open_ts),
      exitS,
      isoToUtcSeconds(bars[0].ts),
      isoToUtcSeconds(bars[bars.length - 1].ts),
    );
  }, [bars, trade.open_ts, exitS]);

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold tracking-tight">
          {option.descriptor} premium
        </h2>
        <span className="tabular text-xs text-muted-foreground">1m</span>
        {estimated && <EstimatedBadge />}
        {bars.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => setWholeSession((v) => !v)}
          >
            {wholeSession ? "Fit to trade" : "Whole session"}
          </Button>
        )}
        {excursion && (
          <div className="ml-auto">
            <ExcursionReadout excursion={excursion} />
          </div>
        )}
      </div>

      <div className="h-[420px] rounded-lg border border-border bg-background/40 p-1">
        {bars.length === 0 ? (
          <div className="flex h-full items-center justify-center p-4">
            <Alert variant="default" className="max-w-md">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>No premium series</AlertTitle>
              <AlertDescription>
                No premium bars are available for{" "}
                <span className="tabular">{option.descriptor}</span> over this
                hold window.
              </AlertDescription>
            </Alert>
          </div>
        ) : (
          <ReviewChart
            candles={bars}
            fills={fills}
            timeframe="1m"
            hoveredFillId={hoveredFillId}
            onHoverFill={onHoverFill}
            priceBands={bands}
            visibleRange={wholeSession ? null : visibleRange}
            dimAfter={exitS}
          />
        )}
      </div>
    </section>
  );
}
