import { useMemo, useState } from "react";
import { AlertCircle } from "lucide-react";

import { PriceChart, type PaneSpec } from "@/components/chart/PriceChart";
import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import type { ReplayFill, ReviewFill, Trade } from "@/lib/api";
import { isoToUtcSeconds } from "@/lib/chart";
import { DISPLAY_TZ_LABEL, formatClock } from "@/lib/format";
import { useReplayContract, useReplayTrade } from "@/lib/queries";
import { LEAD_MIN, revealBound, reviewWindow } from "@/lib/reviewChart";
import { candlesInWindow, floorMinute, spyPaneLabel } from "@/lib/stackedReplay";
import { cn } from "@/lib/utils";

interface Props {
  trade: Trade;
  fills: ReviewFill[];
  /** Option trades get the contract pane; share trades show the stock alone. */
  hasOption: boolean;
}

/**
 * Trade review chart: the shared PriceChart (the same one Replay uses) in its review mode.
 * x-range runs 15 min before entry to 30 min after exit, the hold is shaded, the written stop
 * is the amber line, and the bars after the exit are grey behind a toggle. This is post-trade,
 * so the exit is visible (Replay never shows it early).
 */
export function ReviewStackChart({ trade, fills, hasOption }: Props) {
  const [showAfterExit, setShowAfterExit] = useState(false);
  const session = useReplayTrade(trade.id, LEAD_MIN);
  const contract = useReplayContract(trade.id, LEAD_MIN, hasOption);

  const contractData = contract.data;
  const hasContract = hasOption && contractData?.available === true && (contractData.candles?.length ?? 0) > 0;
  const spy = session.data?.session;

  const view = useMemo(() => {
    if (!spy || spy.candles.length === 0) return null;
    const first = isoToUtcSeconds(spy.candles[0].ts);
    const last = isoToUtcSeconds(spy.candles[spy.candles.length - 1].ts);
    const w = reviewWindow(trade.open_ts, trade.close_ts, first, last);
    const vwap = new Map<number, number>();
    spy.candles.forEach((c, i) => {
      const v = spy.indicators.vwap?.[i];
      if (v != null && !Number.isNaN(v)) vwap.set(floorMinute(isoToUtcSeconds(c.ts)), v);
    });
    return {
      ...w,
      spyCandles: candlesInWindow(spy.candles, w.startSec, w.endSec),
      contractCandles: hasContract ? candlesInWindow(contractData!.candles, w.startSec, w.endSec) : null,
      vwap,
    };
  }, [spy, trade.open_ts, trade.close_ts, hasContract, contractData]);

  const replayFills: ReplayFill[] = useMemo(
    () =>
      fills.map((f) => ({
        seq: f.seq,
        role: f.role,
        side: f.side,
        qty: f.qty,
        price: f.price,
        ts: f.ts,
        time_pt: `${formatClock(f.ts)} ${DISPLAY_TZ_LABEL}`,
      })),
    [fills],
  );

  const top: PaneSpec | null = useMemo(
    () =>
      view?.contractCandles
        ? {
            label: `${contractData?.symbol ?? "Contract"} premium`,
            candles: view.contractCandles,
            badge: contractData?.estimated ? <EstimatedBadge /> : undefined,
          }
        : null,
    [view, contractData],
  );
  const bottom: PaneSpec | null = useMemo(
    () =>
      view && spy
        ? {
            label: spyPaneLabel(spy.symbol, spy.meta.source_label),
            candles: view.spyCandles,
            vwap: view.vwap,
          }
        : null,
    [view, spy],
  );

  const loading = session.isLoading || (hasOption && contract.isLoading);
  const exitSec = view?.exitSec ?? null;
  const bound = view ? revealBound(view.endSec, exitSec, showAfterExit) : 0;
  // With the after-exit bars hidden the window stops just past the exit (no dead right side);
  // "Show after exit" brings back the full exit + 30 min window.
  const chartEnd = view ? (showAfterExit || exitSec === null ? view.endSec : Math.min(view.endSec, exitSec + 3 * 60)) : 0;
  const note =
    hasOption && !contract.isLoading && !hasContract
      ? (contractData?.reason ?? (contract.isError ? "option bars could not be loaded" : "option bars unavailable"))
      : null;

  return (
    <section className="space-y-2" data-testid="review-chart">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="text-body font-semibold tracking-tight">Trade window</h2>
        <span className="flex items-center gap-3 text-label text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="inline-block h-2.5 w-4 rounded-sm bg-play/30" /> Hold
          </span>
          {hasContract && contractData?.stop_price != null && (
            <span className="flex items-center gap-1">
              <span className="inline-block h-0 w-4 border-t-2 border-dashed border-stop" />
              {contractData.stop_assumed ? "Stop (assumed)" : "Written stop"}
            </span>
          )}
          <span>Times in {DISPLAY_TZ_LABEL}</span>
        </span>
        <div className="ml-auto flex items-center gap-3">
          {exitSec !== null && (
            <button
              type="button"
              aria-pressed={showAfterExit}
              onClick={() => setShowAfterExit((v) => !v)}
              className={cn(
                "rounded-md border px-3 py-1 text-label transition-colors",
                showAfterExit
                  ? "border-grey/50 bg-grey/20 text-foreground"
                  : "border-border text-muted-foreground hover:bg-muted",
              )}
            >
              {showAfterExit ? "Hide after exit" : "Show after exit"}
            </button>
          )}
        </div>
      </div>

      {note && (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Contract bars unavailable</AlertTitle>
          <AlertDescription>{note}. Showing the underlying only.</AlertDescription>
        </Alert>
      )}

      <div className="h-[580px] rounded-lg border border-border bg-background/40 p-1">
        {loading ? (
          <Skeleton className="h-full w-full" />
        ) : session.isError || !view || !bottom ? (
          <div className="flex h-full items-center justify-center p-4">
            <Alert className="max-w-md">
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Could not load the chart</AlertTitle>
              <AlertDescription>
                No bars returned for <span className="num">{trade.symbol}</span> on{" "}
                <span className="num">{trade.session_date}</span>.
              </AlertDescription>
            </Alert>
          </div>
        ) : (
          <PriceChart
            top={top}
            bottom={bottom}
            startSec={view.startSec}
            endSec={chartEnd}
            playheadSec={bound}
            stopPrice={top ? (contractData?.stop_price ?? null) : null}
            stopAssumed={contractData?.stop_assumed}
            fills={replayFills}
            mode={{ kind: "review", exitSec }}
            noDataAfterSec={view.noDataAfterSec}
          />
        )}
      </div>
    </section>
  );
}
