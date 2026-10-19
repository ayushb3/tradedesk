import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertCircle, ServerCrash } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ReplayToolbar } from "@/components/replay/ReplayToolbar";
import { RuleBar } from "@/components/replay/RuleBar";
import { TradePicker } from "@/components/replay/TradePicker";
import { TradeReplayBar, TradeScrubber } from "@/components/replay/TradeReplayBar";
import {
  ReplayChart,
  overlaySpecs,
  rsiKey,
  type ChartVisibility,
} from "@/components/replay/ReplayChart";
import { ReplayControlBar } from "@/components/replay/ReplayControlBar";
import { StackedReplayChart } from "@/components/replay/StackedReplayChart";
import { RealTradeCard } from "@/components/replay/RealTradeCard";
import { SimPanel } from "@/components/replay/SimPanel";
import { TradePanel } from "@/components/replay/TradePanel";
import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import {
  useDeskDay,
  useDeskRules,
  useReplayContract,
  useReplaySession,
  useReplayTrade,
  useReplayTradeFills,
  useTrade,
} from "@/lib/queries";
import { ruleBarState } from "@/lib/ruleBar";
import {
  DEFAULT_STACK_INDICATORS,
  EMA_GREYS,
  LADDERS,
  type StackIndicators,
} from "@/lib/stackIndicators";
import { isInputFocused, useReplay } from "@/lib/useReplay";
import { useSimSession } from "@/lib/useSimSession";
import { formatEt, isoToUtcSeconds, valueAt } from "@/lib/chart";
import type { LevelKey } from "@/lib/api";
import {
  candlesInWindow,
  floorMinute,
  INITIAL_WINDOW_MIN,
  revealedFillTime,
  spyPaneLabel,
} from "@/lib/stackedReplay";
import { DISPLAY_TZ_LABEL, formatClock } from "@/lib/format";
import {
  DEFAULT_REPLAY_CONFIG,
  type ReplayConfig,
} from "@/lib/replayConfig";
import { ApiError, type IndicatorKey, type ReplaySession, type SimTrade } from "@/lib/api";

/** Free replay starts on SPY and the most recent weekday; there is no stored default. */
const FREE_SYMBOL = "SPY";
function previousWeekday(): string {
  const d = new Date();
  do d.setDate(d.getDate() - 1);
  while (d.getDay() === 0 || d.getDay() === 6);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function ReplayPage() {
  const [symbol, setSymbol] = useState<string>(FREE_SYMBOL);
  const [date, setDate] = useState<string>(previousWeekday);
  const [stackInd, setStackInd] = useState<StackIndicators>(DEFAULT_STACK_INDICATORS);
  const [config, setConfig] = useState<ReplayConfig>(DEFAULT_REPLAY_CONFIG);
  const [trades, setTrades] = useState<SimTrade[]>([]);

  // 'Replay my trade' mode: ?trade=<id>&lead=<minutes>
  const [params, setParams] = useSearchParams();
  const tradeParam = Number(params.get("trade"));
  const tradeId = Number.isInteger(tradeParam) && tradeParam > 0 ? tradeParam : null;
  const lead = Math.min(240, Math.max(0, Number(params.get("lead") ?? 15) || 0));
  // No trade: a picker of recent trades, or (behind a link) symbol + date.
  const freeMode = tradeId === null && params.get("mode") === "symbol";
  const picking = tradeId === null && !freeMode;

  const freeSession = useReplaySession({
    symbol,
    date,
    timeframe: config.timeframe,
    emaPeriods: config.emaPeriods,
    rsiPeriod: config.rsiPeriod,
    orMinutes: config.orMinutes,
    enabled: freeMode,
  });
  const tradeReplay = useReplayTrade(tradeId, lead);
  const tradeRow = useTrade(tradeId);
  const deskRules = useDeskRules();
  const deskDay = useDeskDay(tradeRow.data?.session_date ?? null);

  // Trade mode: contract premium (top) stacked over SPY (bottom). The old
  // Underlying|Contract toggle and its `view` param are gone; a stale `view`
  // in the URL is ignored.
  const contractReplay = useReplayContract(tradeId, lead, tradeId !== null);
  const contractData = contractReplay.data;
  const hasContract =
    contractData?.available === true && (contractData.candles?.length ?? 0) > 0;
  const contractNote: string | null =
    tradeId !== null && !contractReplay.isLoading && !hasContract
      ? (contractData?.reason ??
        (contractReplay.isError ? "option bars could not be loaded" : "option bars unavailable"))
      : null;

  const tradeSession = tradeReplay.data?.session;
  // x-range: lead start + a fixed 90 min (clamped to the session). That depends
  // only on the entry. The real end (exit + 30 min) arrives with /fills only once
  // the playhead reaches the exit; the range then extends if it is later.
  const startSec = tradeReplay.data ? floorMinute(isoToUtcSeconds(tradeReplay.data.start_ts)) : 0;
  const spyAll = tradeSession?.candles;
  const sessionEndSec =
    spyAll && spyAll.length > 0 ? isoToUtcSeconds(spyAll[spyAll.length - 1].ts) : startSec;
  const initialEndSec = Math.min(startSec + INITIAL_WINDOW_MIN * 60, sessionEndSec);
  const [revealedEnd, setRevealedEnd] = useState<{ key: string; sec: number } | null>(null);
  const endKey = `${tradeId}:${lead}`;
  const revealedEndSec = revealedEnd?.key === endKey ? revealedEnd.sec : 0;
  const endSec = Math.max(initialEndSec, Math.min(revealedEndSec, sessionEndSec));

  const windowed = useMemo(() => {
    if (!tradeSession) return null;
    const spyCandles = candlesInWindow(tradeSession.candles, startSec, endSec);
    const vwapSeries = tradeSession.indicators.vwap;
    const vwap = new Map<number, number>();
    tradeSession.candles.forEach((c, i) => {
      const v = vwapSeries?.[i];
      if (v != null && !Number.isNaN(v)) vwap.set(floorMinute(isoToUtcSeconds(c.ts)), v);
    });
    const contractCandles = hasContract
      ? candlesInWindow(contractData!.candles, startSec, endSec)
      : null;
    // EMAs (opt-in, grey): one minute-keyed map per period present.
    const emas = Object.keys(tradeSession.indicators)
      .filter((k) => /^ema\d+$/.test(k))
      .sort((a, b) => Number(a.slice(3)) - Number(b.slice(3)))
      .slice(0, EMA_GREYS.length)
      .map((k) => {
        const series = tradeSession.indicators[k as keyof typeof tradeSession.indicators];
        const values = new Map<number, number>();
        tradeSession.candles.forEach((c, i) => {
          const v = series?.[i];
          if (v != null && !Number.isNaN(v)) values.set(floorMinute(isoToUtcSeconds(c.ts)), v);
        });
        return { label: k.toUpperCase(), values };
      });
    return { spyCandles, vwap, emas, contractCandles };
  }, [tradeSession, startSec, endSec, hasContract, contractData]);

  const spyLabel = spyPaneLabel(
    tradeSession?.symbol ?? "SPY",
    tradeSession?.meta.source_label,
  );

  // The replay is driven by the contract bars, or by SPY when there are none.
  const stackSession: ReplaySession | undefined = useMemo(() => {
    if (!tradeSession || !windowed) return undefined;
    const meta = { ...tradeSession.meta };
    if (windowed.contractCandles && contractData) {
      return {
        symbol: contractData.symbol ?? "Contract",
        session_date: contractData.session_date ?? tradeSession.session_date,
        candles: windowed.contractCandles,
        indicators: {},
        levels: {},
        meta: { ...meta, source_label: contractData.estimated ? "estimated premium" : "option premium" },
      };
    }
    return {
      symbol: tradeSession.symbol,
      session_date: tradeSession.session_date,
      candles: windowed.spyCandles,
      indicators: {},
      levels: {},
      meta,
    };
  }, [tradeSession, windowed, contractData]);

  const inTrade = tradeId !== null;
  const active = inTrade ? tradeReplay : freeSession;
  const isLoading = inTrade ? tradeReplay.isLoading || contractReplay.isLoading : freeSession.isLoading;
  const isError = active.isError;
  const error = active.error;
  const data: ReplaySession | undefined = inTrade ? stackSession : freeSession.data;

  // The session is already cut to the trade window, so the replay starts at bar 0
  // (the engine resets to the first bar whenever the bar count changes).
  const replay = useReplay(data);

  const playheadTs = inTrade && data ? (data.candles[replay.currentIndex]?.ts ?? null) : null;
  const playheadSec = playheadTs ? floorMinute(isoToUtcSeconds(playheadTs)) : startSec;

  // SPY pane: VWAP plus one level ladder by default; EMAs opt-in. Levels are the
  // causal value at the playhead bar, never a later one.
  const spyPane = useMemo(() => {
    if (!tradeSession || !windowed) return null;
    let idx = -1;
    for (let i = 0; i < tradeSession.candles.length; i++) {
      if (floorMinute(isoToUtcSeconds(tradeSession.candles[i].ts)) > playheadSec) break;
      idx = i;
    }
    const levels = LADDERS[stackInd.ladder].levels.flatMap((l: { key: LevelKey; label: string }) => {
      const v = valueAt(tradeSession.levels[l.key], idx);
      return v == null ? [] : [{ label: l.label, price: v }];
    });
    return {
      label: spyLabel,
      candles: windowed.spyCandles,
      vwap: stackInd.vwap ? windowed.vwap : undefined,
      levels,
      emas: stackInd.ema ? windowed.emas : undefined,
    };
  }, [tradeSession, windowed, playheadSec, stackInd, spyLabel]);
  const fillsQuery = useReplayTradeFills(tradeId, playheadTs);
  const windowEndTs = fillsQuery.data?.window_end_ts ?? null;
  useEffect(() => {
    if (!windowEndTs) return;
    const sec = isoToUtcSeconds(windowEndTs);
    setRevealedEnd((prev) => (prev?.key === endKey && prev.sec >= sec ? prev : { key: endKey, sec }));
  }, [windowEndTs, endKey]);

  // Extending the window changes the bar count, which resets the engine to bar 0;
  // put the playhead (and playback) back where it was.
  const lastRun = useRef({ total: 0, index: 0, playing: false });
  const { setIndex, play } = replay;
  useEffect(() => {
    const prev = lastRun.current;
    if (inTrade && prev.total > 0 && replay.total !== prev.total && replay.total > prev.total) {
      setIndex(prev.index);
      if (prev.playing) play();
    }
    lastRun.current = {
      total: replay.total,
      index: replay.total !== prev.total ? prev.index : replay.currentIndex,
      playing: replay.total !== prev.total ? prev.playing : replay.isPlaying,
    };
  }, [inTrade, replay.total, replay.currentIndex, replay.isPlaying, setIndex, play]);

  const revealedFills = useMemo(
    () => (fillsQuery.data?.fills ?? []).filter((f) => isoToUtcSeconds(f.ts) <= playheadSec + 59),
    [fillsQuery.data, playheadSec],
  );

  // Scrubber marks: only what the playhead has reached.
  const marks = useMemo(() => {
    if (!inTrade || !data) return undefined;
    const idxOf = (sec: number | null) => {
      if (sec === null) return null;
      const i = data.candles.findIndex((c) => floorMinute(isoToUtcSeconds(c.ts)) >= sec);
      return i < 0 ? null : i;
    };
    return {
      entryIndex: idxOf(revealedFillTime(revealedFills, "entry", playheadSec)),
      exitIndex: idxOf(revealedFillTime(revealedFills, "exit", playheadSec)),
    };
  }, [inTrade, data, revealedFills, playheadSec]);

  // Trade-mode sim: fills at the playhead bar's close on the premium pane.
  const playCandle = inTrade && data ? (data.candles[replay.currentIndex] ?? null) : null;
  const sim = useSimSession({
    resetKey: endKey,
    symbol: contractData?.symbol ?? tradeSession?.symbol ?? "",
    sessionDate: tradeSession?.session_date ?? date,
    writtenStop: contractData?.stop_price ?? null,
  });
  const simPos = sim.state.position;
  // Stop moves are stamped with the playhead bar they were made on.
  const { setPlayTs } = sim;
  useEffect(() => setPlayTs(playCandle?.ts ?? null), [setPlayTs, playCandle?.ts]);

  // B = buy 1, S = sell 1 (Space / arrows live in useReplay). Ignored while focus
  // is in an input, with a modifier held, or when there is no premium to trade.
  const canTrade = inTrade && windowed?.contractCandles != null;
  const keyActs = useRef({ buy: () => {}, sell: () => {} });
  keyActs.current = { buy: () => sim.onBuy(playCandle), sell: () => sim.onSell(playCandle) };
  useEffect(() => {
    if (!canTrade) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || isInputFocused(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "b") {
        e.preventDefault();
        keyActs.current.buy();
      } else if (k === "s") {
        e.preventDefault();
        keyActs.current.sell();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canTrade]);

  const simFills = useMemo(
    () => sim.state.trips.flatMap((t) => t.fills).concat(simPos.fills),
    [sim.state.trips, simPos.fills],
  );

  const ready = !isLoading && !isError && data && replay.total > 0;

  const visibility: ChartVisibility = {
    hiddenOverlays: config.hiddenOverlays,
    hiddenLevels: config.hiddenLevels,
    rsiHidden: config.rsiHidden,
  };

  // Reset the trade log whenever the session changes.
  const sessionKey = `${symbol}:${date}`;
  const [loggedKey, setLoggedKey] = useState(sessionKey);
  if (sessionKey !== loggedKey) {
    setLoggedKey(sessionKey);
    setTrades([]);
  }

  // Trade mode header: the contract and the trade's session date.
  const tradeContract =
    tradeRow.data?.contract_label ?? contractData?.symbol ?? tradeReplay.data?.session.symbol ?? "…";
  const tradeDate = tradeRow.data?.session_date ?? tradeReplay.data?.session.session_date ?? "";
  const bar = useMemo(
    () =>
      ruleBarState({
        rules: deskRules.data,
        day: deskDay.data,
        tradeId: tradeId ?? 0,
        entryTs: tradeRow.data?.open_ts ?? null,
      }),
    [deskRules.data, deskDay.data, tradeId, tradeRow.data?.open_ts],
  );
  const setLead = (l: number) => setParams({ trade: String(tradeId), lead: String(l) });

  if (picking) {
    return (
      <div className="h-full overflow-y-auto">
        <TradePicker onSymbolMode={() => setParams({ mode: "symbol" })} />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {inTrade ? (
        <>
          <TradeReplayBar
            contract={tradeContract}
            date={tradeDate}
            lead={lead}
            onLeadChange={setLead}
            clock={playheadTs ? formatClock(playheadTs) : null}
            replay={replay}
            disabled={!ready}
            onExit={() => setParams({})}
          />
          <RuleBar state={bar} loading={deskRules.isLoading || deskDay.isLoading || tradeRow.isLoading} />
        </>
      ) : (
        <ReplayToolbar
          symbol={symbol}
          onSymbolChange={setSymbol}
          date={date}
          onDateChange={setDate}
          controls={
            <button
              type="button"
              className="text-label text-muted-foreground underline-offset-4 hover:underline"
              onClick={() => setParams({})}
            >
              Replay a trade instead
            </button>
          }
        />
      )}

      <div className="flex min-h-0 flex-1">
        <div className="relative min-h-0 flex-1">
          {contractNote && (
            <div className="absolute right-3 top-3 z-20 max-w-sm" role="status">
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Contract bars unavailable</AlertTitle>
                <AlertDescription>{contractNote}. Showing SPY only.</AlertDescription>
              </Alert>
            </div>
          )}
          {isLoading && <ChartSkeleton />}
          {isError && <ChartError error={error} />}
          {!isLoading && !isError && data && replay.total === 0 && (
            <EmptyState symbol={symbol} date={date} />
          )}
          {ready && inTrade && windowed && spyPane && (
            <div className="absolute inset-x-0 bottom-16 top-0">
              <StackedReplayChart
                top={
                  windowed.contractCandles
                    ? {
                        label: `${contractData?.symbol ?? "Contract"} premium`,
                        candles: windowed.contractCandles,
                        badge: contractData?.estimated ? <EstimatedBadge /> : undefined,
                      }
                    : null
                }
                bottom={spyPane}
                startSec={startSec}
                endSec={endSec}
                playheadSec={playheadSec}
                stopPrice={windowed.contractCandles ? simPos.stop : null}
                stopAssumed={simPos.stopAssumed}
                avgPrice={windowed.contractCandles && simPos.qty > 0 ? simPos.avg : null}
                simFills={windowed.contractCandles ? simFills : []}
                onStopChange={sim.onSetStop}
                fills={windowed.contractCandles ? revealedFills : []}
              />
            </div>
          )}
          {ready && !inTrade && (
            <>
              <ChartLegend
                session={data}
                currentIndex={replay.currentIndex}
                visibility={visibility}
              />
              <ReplayChart
                session={data}
                currentIndex={replay.currentIndex}
                trades={trades}
                visibility={visibility}
              />
            </>
          )}
          {inTrade ? (
            <TradeScrubber
              replay={replay}
              disabled={!ready}
              marks={marks}
              indicators={stackInd}
              onIndicatorsChange={setStackInd}
            />
          ) : (
            <ReplayControlBar
              replay={replay}
              disabled={!ready}
              config={config}
              onConfigChange={setConfig}
              indicators={data?.indicators}
              marks={marks}
            />
          )}
        </div>

        <aside className="hidden w-80 shrink-0 overflow-y-auto border-l border-border md:block">
          {tradeId !== null && (
            <RealTradeCard
              fills={revealedFills}
              writtenStop={contractData?.stop_price ?? null}
              stopAssumed={contractData?.stop_assumed ?? false}
              exitReached={windowEndTs !== null}
              bars={windowed?.contractCandles ?? null}
              sourceLabel={
                windowed?.contractCandles
                  ? contractData?.estimated
                    ? "estimated premium"
                    : "option premium"
                  : spyLabel
              }
            />
          )}
          {inTrade ? (
            <SimPanel
              candle={playCandle}
              bars={windowed?.contractCandles ?? []}
              state={sim.state}
              enabled={windowed?.contractCandles != null}
              onBuy={() => sim.onBuy(playCandle)}
              onSell={() => sim.onSell(playCandle)}
              onCloseAll={() => sim.onCloseAll(playCandle)}
              logError={sim.logError}
            />
          ) : (
            <TradePanel
              symbol={symbol}
              sessionDate={date}
              tz={data?.meta.tz ?? "America/New_York"}
              currentCandle={data ? data.candles[replay.currentIndex] ?? null : null}
              trades={trades}
              onTradeCreated={(t) => setTrades((prev) => [...prev, t])}
            />
          )}
        </aside>
      </div>
    </div>
  );
}

function ChartLegend({
  session,
  currentIndex,
  visibility,
}: {
  session: ReplaySession;
  currentIndex: number;
  visibility: ChartVisibility;
}) {
  const candle = session.candles[currentIndex];
  const barTime = candle ? formatEt(candle.ts, session.meta.tz) : "—";

  const specs = overlaySpecs(session.indicators).filter(
    (o) => !visibility.hiddenOverlays.has(o.key),
  );
  const rKey = rsiKey(session.indicators);
  const showRsi = !visibility.rsiHidden && rKey != null;

  return (
    <div className="pointer-events-none absolute left-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-4 gap-y-1 rounded-md bg-card/80 px-3 py-1.5 text-label backdrop-blur">
      <span className="font-semibold tracking-tight">{session.symbol}</span>
      {session.meta.source_label && (
        <span className="text-muted-foreground">{session.meta.source_label}</span>
      )}
      <span className="num text-muted-foreground">{barTime} {DISPLAY_TZ_LABEL}</span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {specs.map((o) => {
          const v = valueAt(session.indicators[o.key as IndicatorKey], currentIndex);
          return (
            <span key={o.key} className="flex items-center gap-1.5">
              <span
                className="inline-block h-0.5 w-4"
                style={{ background: o.color }}
              />
              <span className="text-muted-foreground">
                {o.label}
                {v != null ? ` ${v.toFixed(2)}` : ""}
              </span>
            </span>
          );
        })}
        {showRsi && (
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block h-0.5 w-4"
              style={{ background: "#eab308" }}
            />
            <span className="text-muted-foreground">
              {rKey!.toUpperCase()}
              {(() => {
                const v = valueAt(session.indicators[rKey! as IndicatorKey], currentIndex);
                return v != null ? ` ${v.toFixed(1)}` : "";
              })()}
            </span>
          </span>
        )}
      </span>
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="absolute inset-0 flex flex-col gap-2 p-4">
      <Skeleton className="h-6 w-40" />
      <Skeleton className="min-h-0 flex-1" />
    </div>
  );
}

function EmptyState({ symbol, date }: { symbol: string; date: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <Alert className="max-w-md">
        <AlertCircle className="h-4 w-4" />
        <AlertTitle>No session data</AlertTitle>
        <AlertDescription>
          The backend returned no bars for{" "}
          <span className="tabular">{symbol}</span> on{" "}
          <span className="tabular">{date}</span>. Try another date or symbol.
        </AlertDescription>
      </Alert>
    </div>
  );
}

function ChartError({ error }: { error: unknown }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const isBackendDown = status === 0;
  const message =
    error instanceof ApiError
      ? error.detail || error.message
      : error instanceof Error
        ? error.message
        : "Something went wrong loading the replay.";

  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <Alert variant="destructive" className="max-w-md">
        {isBackendDown ? (
          <ServerCrash className="h-4 w-4" />
        ) : (
          <AlertCircle className="h-4 w-4" />
        )}
        <AlertTitle>{isBackendDown ? "Cannot reach the server" : "Could not load the replay"}</AlertTitle>
        <AlertDescription>
          {isBackendDown ? "Check that the app server is running, then reload." : message}
        </AlertDescription>
      </Alert>
    </div>
  );
}
