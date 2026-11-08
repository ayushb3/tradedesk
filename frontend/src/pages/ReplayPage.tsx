import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ServerCrash } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ReplayToolbar } from "@/components/replay/ReplayToolbar";
import { RuleSummary } from "@/components/replay/RuleBar";
import { TradeReplayBar, TradeScrubber } from "@/components/replay/TradeReplayBar";
import { DayReplay } from "@/components/replay/DayReplay";
import { ReplayControlBar } from "@/components/replay/ReplayControlBar";
import { PriceChart } from "@/components/chart/PriceChart";
import { RealTradeCard } from "@/components/replay/RealTradeCard";
import { SimPanel } from "@/components/replay/SimPanel";
import { TradePanel } from "@/components/replay/TradePanel";
import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import {
  useReplayDaySoFar,
  useReplayContract,
  useReplaySession,
  useReplaySecondsProbe,
  useReplayTrade,
  useReplayTradeFills,
  useTrade,
} from "@/lib/queries";
import { ruleBarState } from "@/lib/ruleBar";
import { DEFAULT_STACK_INDICATORS, type StackIndicators } from "@/lib/stackIndicators";
import { emaLines, paneLevels, smaLines, vwapMap } from "@/lib/indicatorLines";
import { engineFor, FAST_DEFAULT_SPEED, FAST_MAX_SPEED, loadSpeed, saveSpeed, secondsRes } from "@/lib/replaySpeed";
import { exitTarget, isDay, safeFrom } from "@/lib/replayNav";
import { useReplay, type ReplayState } from "@/lib/useReplay";
import { isInputFocused } from "@/lib/replayKeys";
import { sessionChart, type ChartVisibility } from "@/lib/sessionChart";
import { useSecondsReplay } from "@/lib/useSecondsReplay";
import {
  finishedOnly,
  formatClockSeconds,
  formatClockSecondsPt,
  liveCandle,
  minuteIndexAt,
  replaySeries,
} from "@/lib/secondsReplay";
import { useSimSession } from "@/lib/useSimSession";
import { isoToUtcSeconds } from "@/lib/chart";
import {
  candlesInWindow,
  floorMinute,
  INITIAL_WINDOW_MIN,
  revealedFillTime,
  spyPaneLabel,
} from "@/lib/stackedReplay";
import { formatClock } from "@/lib/format";
import {
  DEFAULT_REPLAY_CONFIG,
  type ReplayConfig,
} from "@/lib/replayConfig";
import { api, ApiError, type ReplaySession, type SimTrade } from "@/lib/api";

/** Free replay starts on SPY and the most recent weekday; there is no stored default. */
const FREE_SYMBOL = "SPY";
function previousWeekday(): string {
  const d = new Date();
  do d.setDate(d.getDate() - 1);
  while (d.getDay() === 0 || d.getDay() === 6);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * /replay: a trade (?trade=<id>&lead=<min>), a whole day (?day=YYYY-MM-DD) or (by URL
 * only) free symbol + date (?mode=symbol). `from=<path>` is where Exit goes. Replay is an
 * action on a trade or a day, not a page: bare /replay goes to the Days calendar.
 */
export function ReplayPage() {
  const [params] = useSearchParams();
  const day = params.get("day");
  if (isDay(day) && !params.get("trade")) return <DayReplay day={day} />;
  const trade = Number(params.get("trade"));
  const hasTrade = Number.isInteger(trade) && trade > 0;
  if (!hasTrade && params.get("mode") !== "symbol") return <Navigate to="/days" replace />;
  return <TradeOrFreeReplay />;
}

/**
 * The trade player, embedded on the trade page (/trades/:id): no URL params, its own lead,
 * Exit closes it, and prev/next go to the neighbouring trade page (still replaying).
 */
export function InlineTradeReplay({
  tradeId,
  onExit,
  onGoTrade,
}: {
  tradeId: number;
  onExit: () => void;
  onGoTrade: (id: number) => void;
}) {
  return <TradeOrFreeReplay embed={{ tradeId, onExit, onGoTrade }} />;
}

interface Embed {
  tradeId: number;
  onExit: () => void;
  onGoTrade: (id: number) => void;
}

function TradeOrFreeReplay({ embed }: { embed?: Embed } = {}) {
  const navigate = useNavigate();
  const [speed, setSpeedState] = useState<number>(() => loadSpeed());
  // Free replay plays whole bars with no trade to study: its own speed, up to 1024x.
  const [freeSpeed, setFreeSpeed] = useState<number>(FAST_DEFAULT_SPEED);
  const setSpeed = (s: number) => {
    setSpeedState(s);
    saveSpeed(s);
  };
  const [symbol, setSymbol] = useState<string>(FREE_SYMBOL);
  const [date, setDate] = useState<string>(previousWeekday);
  const [stackInd, setStackInd] = useState<StackIndicators>(DEFAULT_STACK_INDICATORS);
  const [config, setConfig] = useState<ReplayConfig>(DEFAULT_REPLAY_CONFIG);
  const [trades, setTrades] = useState<SimTrade[]>([]);

  // 'Replay my trade' mode: ?trade=<id>&lead=<minutes>
  const [params, setParams] = useSearchParams();
  const tradeParam = Number(params.get("trade"));
  const tradeId = embed
    ? embed.tradeId
    : Number.isInteger(tradeParam) && tradeParam > 0
      ? tradeParam
      : null;
  const [embedLead, setEmbedLead] = useState(15);
  const lead = embed ? embedLead : Math.min(240, Math.max(0, Number(params.get("lead") ?? 15) || 0));
  const from = embed ? null : safeFrom(params.get("from"));
  // No trade: (by URL only) free symbol + date. Bare /replay never gets here.
  const freeMode = tradeId === null;

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
  const daySoFar = useReplayDaySoFar(tradeId);

  // Trade mode: SPY on top (the main pane), contract premium stacked below it. The old
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
  // The chart holds the whole session from its first bar (premarket), so it can zoom out to
  // it; it opens on the lead window. The replay engine still plays from the lead start.
  const sessionStartSec = spyAll && spyAll.length > 0 ? floorMinute(isoToUtcSeconds(spyAll[0].ts)) : startSec;
  const [revealedEnd, setRevealedEnd] = useState<{ key: string; sec: number } | null>(null);
  const endKey = `${tradeId}:${lead}`;
  const revealedEndSec = revealedEnd?.key === endKey ? revealedEnd.sec : 0;
  const endSec = Math.max(initialEndSec, Math.min(revealedEndSec, sessionEndSec));

  // Below 60x the replay plays second bars when the trade has them (the speed slider is the
  // only switch); otherwise minute bars, with a "minute bars" tag saying why.
  const secondsProbe = useReplaySecondsProbe(tradeId, lead, tradeReplay.data?.start_ts ?? null);
  const secondsAvailable = secondsProbe.data?.available === true;
  const useSeconds = tradeId !== null && engineFor(speed, secondsAvailable) === "seconds";
  const secs = useSecondsReplay({
    enabled: useSeconds,
    tradeId,
    lead,
    minSec: startSec,
    maxSec: endSec + 59,
    speed,
    res: secondsRes(speed),
  });
  const minuteTag =
    tradeId !== null && !useSeconds && engineFor(speed, true) === "seconds"
      ? {
          reason: secondsProbe.isLoading
            ? "checking for second-level prints"
            : (secondsProbe.data?.reason ?? (secondsProbe.isError ? "request failed" : "no second-level prints")),
        }
      : null;

  const windowedAll = useMemo(() => {
    if (!tradeSession) return null;
    const spyCandles = candlesInWindow(tradeSession.candles, startSec, endSec);
    const vwap = vwapMap(tradeSession);
    const contractCandles = hasContract
      ? candlesInWindow(contractData!.candles, startSec, endSec)
      : null;
    // What the chart draws: the same bars back to the session's first bar (history before the
    // lead start is never after the playhead, so it is safe to show when zoomed out).
    const chartSpy = candlesInWindow(tradeSession.candles, sessionStartSec, endSec);
    const chartContract = hasContract ? candlesInWindow(contractData!.candles, sessionStartSec, endSec) : null;
    // EMAs 9/21/50 and SMAs 200/400 (looks from INDICATOR_LOOK), minute-keyed; toggled in spyPane.
    const emas = emaLines(tradeSession);
    const smas = smaLines(tradeSession);
    return { spyCandles, vwap, emas, smas, contractCandles, chartSpy, chartContract };
  }, [tradeSession, startSec, endSec, sessionStartSec, hasContract, contractData]);

  // What the panes may show. In Seconds mode the current minute is the candle formed from the
  // seconds played so far, later minutes are dropped, and the causal minute-keyed lines stop
  // at the last FINISHED minute (the playhead minute's values would include later seconds).
  const windowed = useMemo(() => {
    if (!windowedAll || !useSeconds) return windowedAll;
    const ph = secs.playSec;
    return {
      ...windowedAll,
      spyCandles: replaySeries(windowedAll.spyCandles, secs.underlying, ph),
      contractCandles: windowedAll.contractCandles
        ? replaySeries(windowedAll.contractCandles, secs.contract, ph)
        : null,
      chartSpy: replaySeries(windowedAll.chartSpy, secs.underlying, ph),
      chartContract: windowedAll.chartContract ? replaySeries(windowedAll.chartContract, secs.contract, ph) : null,
      vwap: finishedOnly(windowedAll.vwap, ph),
      emas: windowedAll.emas.map((e) => ({ ...e, values: finishedOnly(e.values, ph) })),
      smas: windowedAll.smas.map((e) => ({ ...e, values: finishedOnly(e.values, ph) })),
    };
  }, [windowedAll, useSeconds, secs.playSec, secs.underlying, secs.contract]);

  const spyLabel = spyPaneLabel(
    tradeSession?.symbol ?? "SPY",
    tradeSession?.meta.source_label,
  );

  // The replay is driven by the contract bars, or by SPY when there are none.
  const stackSession: ReplaySession | undefined = useMemo(() => {
    if (!tradeSession || !windowedAll) return undefined;
    const meta = { ...tradeSession.meta };
    if (windowedAll.contractCandles && contractData) {
      return {
        symbol: contractData.symbol ?? "Contract",
        session_date: contractData.session_date ?? tradeSession.session_date,
        candles: windowedAll.contractCandles,
        indicators: {},
        levels: {},
        meta: { ...meta, source_label: contractData.estimated ? "estimated premium" : "option premium" },
      };
    }
    return {
      symbol: tradeSession.symbol,
      session_date: tradeSession.session_date,
      candles: windowedAll.spyCandles,
      indicators: {},
      levels: {},
      meta,
    };
  }, [tradeSession, windowedAll, contractData]);

  const inTrade = tradeId !== null;
  const active = inTrade ? tradeReplay : freeSession;
  const isLoading = inTrade ? tradeReplay.isLoading || contractReplay.isLoading : freeSession.isLoading;
  const isError = active.isError;
  const error = active.error;
  const data: ReplaySession | undefined = inTrade ? stackSession : freeSession.data;

  // The session is already cut to the trade window, so the replay starts at bar 0
  // (the engine resets to the first bar whenever the bar count changes).
  const minuteReplay = useReplay(data, {
    keyboardEnabled: !useSeconds,
    // The speed control drives the minute clock (64x ≈ one 1m bar per second).
    chartSpeed: inTrade ? speed : freeSpeed,
    maxSpeed: inTrade ? undefined : FAST_MAX_SPEED,
  });

  // In Seconds mode the seconds clock is the truth and the transport controls drive it; the
  // minute engine only supplies the bar count. Everything below reads `replay`.
  const secIndex = data ? minuteIndexAt(data.candles, secs.playSec) : 0;
  const replay: ReplayState = useSeconds
    ? {
        ...minuteReplay,
        currentIndex: secIndex,
        currentBar: secIndex,
        isPlaying: secs.isPlaying,
        atStart: secs.atStart,
        atEnd: secs.atEnd,
        play: secs.play,
        pause: secs.pause,
        toggle: secs.toggle,
        stepForward: secs.stepForward,
        stepBack: secs.stepBack,
        stepFine: secs.stepFine,
        restart: secs.restart,
        setIndex: (i: number) => {
          const c = data?.candles[i];
          if (c) secs.seek(isoToUtcSeconds(c.ts));
        },
        barLabel: formatClockSecondsPt(secs.playSec),
      }
    : minuteReplay;

  const playheadTs =
    inTrade && data
      ? useSeconds
        ? new Date(secs.playSec * 1000).toISOString()
        : (data.candles[replay.currentIndex]?.ts ?? null)
      : null;
  const playheadSec = useSeconds
    ? floorMinute(secs.playSec)
    : playheadTs
      ? floorMinute(isoToUtcSeconds(playheadTs))
      : startSec;
  // Crossing 60x (or the seconds probe landing) swaps the clock: carry the playhead and the
  // play state across. `lastClock` holds the previous render's position.
  const lastClock = useRef({
    seconds: useSeconds,
    sec: playheadSec,
    index: replay.currentIndex,
    playing: replay.isPlaying,
  });
  const { pause: secsPause, seek: secsSeek, play: secsPlay } = secs;
  useEffect(() => {
    const prev = lastClock.current;
    if (prev.seconds !== useSeconds) {
      if (useSeconds) {
        minuteReplay.pause();
        secsSeek(prev.sec);
        if (prev.playing) secsPlay();
      } else {
        secsPause();
        minuteReplay.setIndex(prev.index);
        if (prev.playing) minuteReplay.play();
      }
    }
    lastClock.current = {
      seconds: useSeconds,
      sec: playheadSec,
      index: replay.currentIndex,
      playing: replay.isPlaying,
    };
  });

  // SPY pane: VWAP plus one level ladder by default; EMAs opt-in. Levels are the
  // causal value at the playhead bar, never a later one.
  const spyPane = useMemo(() => {
    if (!tradeSession || !windowed) return null;
    let idx = -1;
    // Seconds mode: levels as of the last finished minute (day high/low would otherwise
    // include seconds that have not played yet).
    const levelSec = useSeconds ? playheadSec - 60 : playheadSec;
    for (let i = 0; i < tradeSession.candles.length; i++) {
      if (floorMinute(isoToUtcSeconds(tradeSession.candles[i].ts)) > levelSec) break;
      idx = i;
    }
    // Ladder levels (causal at idx) plus the caller's posted levels for the day.
    const levels = paneLevels(tradeSession, idx, stackInd);
    const lines = [...(stackInd.ema ? windowed.emas : []), ...(stackInd.sma ? windowed.smas : [])];
    return {
      label: spyLabel,
      candles: windowed.chartSpy,
      vwap: stackInd.vwap ? windowed.vwap : undefined,
      levels,
      emas: lines.length > 0 ? lines : undefined,
      quoteSymbol: tradeSession.symbol,
    };
  }, [tradeSession, windowed, playheadSec, stackInd, spyLabel, useSeconds]);
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
  const { setIndex, play } = minuteReplay;
  useEffect(() => {
    const prev = lastRun.current;
    const m = minuteReplay;
    if (inTrade && !useSeconds && prev.total > 0 && m.total !== prev.total && m.total > prev.total) {
      setIndex(prev.index);
      if (prev.playing) play();
    }
    lastRun.current = {
      total: m.total,
      index: m.total !== prev.total ? prev.index : m.currentIndex,
      playing: m.total !== prev.total ? prev.playing : m.isPlaying,
    };
  }, [inTrade, useSeconds, minuteReplay.total, minuteReplay.currentIndex, minuteReplay.isPlaying, setIndex, play]);

  const revealedFills = useMemo(
    // Fills reveal at their exact second in Seconds mode, at the end of the playhead minute otherwise.
    () =>
      (fillsQuery.data?.fills ?? []).filter(
        (f) => isoToUtcSeconds(f.ts) <= (useSeconds ? secs.playSec : playheadSec + 59),
      ),
    [fillsQuery.data, playheadSec, useSeconds, secs.playSec],
  );

  // Fills list: clicking a row puts the playhead where that fill is first revealed, so the
  // clicked fill stays in view: its second in Seconds mode, otherwise the first bar at or
  // after it (the server reveals a fill once the playhead is at or past its second, so a
  // 06:47:06 fill shows from the 06:48 bar). Only revealed fills are listed: this moves back.
  const seekToFill = (f: { ts: string }) => {
    if (!data) return;
    const sec = Math.ceil(isoToUtcSeconds(f.ts));
    replay.pause();
    if (useSeconds) {
      secs.seek(sec);
      return;
    }
    const i = data.candles.findIndex((c) => isoToUtcSeconds(c.ts) >= sec);
    if (i >= 0) replay.setIndex(i);
  };

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
  // Seconds mode: the latest print so far (never the minute's final close).
  const playCandle =
    inTrade && data
      ? useSeconds
        ? liveCandle(windowed?.contractCandles ?? [], secs.playSec)
        : (data.candles[replay.currentIndex] ?? null)
      : null;
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

  const visibility: ChartVisibility = useMemo(
    () => ({ hiddenOverlays: config.hiddenOverlays, hiddenLevels: config.hiddenLevels }),
    [config.hiddenOverlays, config.hiddenLevels],
  );

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
  const bar = useMemo(() => ruleBarState(daySoFar.data), [daySoFar.data]);
  const tradeParams = (id: number, l: number) => ({
    trade: String(id),
    lead: String(l),
    ...(from ? { from } : {}),
  });
  const setLead = (l: number) =>
    embed ? setEmbedLead(l) : setParams(tradeParams(tradeId as number, l));
  const neighbors = useQuery({
    queryKey: ["replay-neighbors", tradeId],
    queryFn: () => api.replayTradeNeighbors(tradeId as number),
    enabled: tradeId !== null,
    staleTime: 60_000,
  });
  const goTrade = (id: number | null | undefined) =>
    id ? () => (embed ? embed.onGoTrade(id) : setParams(tradeParams(id, lead))) : null;
  const exit = () => {
    if (embed) return embed.onExit();
    navigate(exitTarget(from, tradeId) ?? "/days");
  };

  return (
    <div className="flex h-full flex-col">
      {inTrade ? (
        <>
          <TradeReplayBar
            contract={tradeContract}
            date={tradeDate}
            lead={lead}
            onLeadChange={setLead}
            clock={
              playheadTs ? (useSeconds ? formatClockSeconds(secs.playSec) : formatClock(playheadTs)) : null
            }
            replay={replay}
            disabled={!ready}
            onExit={exit}
            speed={speed}
            onSpeedChange={setSpeed}
            minuteTag={minuteTag}
            nav={{ onPrev: goTrade(neighbors.data?.prev_id), onNext: goTrade(neighbors.data?.next_id) }}
            daySoFar={<RuleSummary state={bar} loading={daySoFar.isLoading} />}
          />
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
              onClick={() => navigate("/days")}
            >
              Back to Days
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
          {useSeconds && secs.error && (
            <div className="absolute left-3 top-3 z-20 rounded-md bg-card/90 px-3 py-1.5 text-label" role="status">
              Seconds: {secs.error}
            </div>
          )}
          {isLoading && <ChartSkeleton />}
          {isError && <ChartError error={error} />}
          {!isLoading && !isError && data && replay.total === 0 && (
            <EmptyState symbol={symbol} date={date} />
          )}
          {ready && inTrade && windowed && spyPane && (
            <div className="absolute inset-x-0 bottom-10 top-0">
              <PriceChart
                top={
                  windowed.chartContract
                    ? {
                        label: `${contractData?.symbol ?? "Contract"} premium`,
                        candles: windowed.chartContract,
                        badge: contractData?.estimated ? <EstimatedBadge /> : undefined,
                      }
                    : null
                }
                bottom={spyPane}
                startSec={sessionStartSec}
                endSec={endSec}
                defaultView={{ fromSec: startSec }}
                playheadSec={playheadSec}
                revealSec={useSeconds ? secs.playSec : playheadSec + 59}
                mode={{ kind: "replay" }}
                stopPrice={windowed.contractCandles ? simPos.stop : null}
                stopAssumed={simPos.stopAssumed}
                avgPrice={windowed.contractCandles && simPos.qty > 0 ? simPos.avg : null}
                simFills={windowed.contractCandles ? simFills : []}
                onStopChange={sim.onSetStop}
                fills={windowed.contractCandles ? revealedFills : []}
                playheadLabel={useSeconds ? formatClockSecondsPt(secs.playSec) : undefined}
                mainPane="bottom"
                contractBelow
                fillLabels="arrows"
              />
            </div>
          )}
          {ready && !inTrade && data && (
            <FreeReplayChart session={data} index={replay.currentIndex} visibility={visibility} trades={trades} />
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
              speed={freeSpeed}
              onSpeedChange={setFreeSpeed}
              maxSpeed={FAST_MAX_SPEED}
              disabled={!ready}
              config={config}
              onConfigChange={setConfig}
              indicators={data?.indicators}
              marks={marks}
            />
          )}
        </div>

        <aside
          className={`hidden shrink-0 overflow-y-auto border-l border-border md:block ${inTrade ? "w-72" : "w-80"}`}
        >
          {tradeId !== null && (
            <RealTradeCard
              fills={revealedFills}
              writtenStop={contractData?.stop_price ?? null}
              stopAssumed={contractData?.stop_assumed ?? false}
              exitReached={windowEndTs !== null}
              realResult={fillsQuery.data?.real_result ?? null}
              bars={windowed?.contractCandles ?? null}
              sourceLabel={
                windowed?.contractCandles
                  ? contractData?.estimated
                    ? "estimated premium"
                    : "option premium"
                  : spyLabel
              }
              onSeekFill={ready ? seekToFill : undefined}
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

/** Free replay on the shared chart: the session's underlying, levels and practice trades. */
function FreeReplayChart({
  session,
  index,
  visibility,
  trades,
}: {
  session: ReplaySession;
  index: number;
  visibility: ChartVisibility;
  trades: SimTrade[];
}) {
  // Memoized: a new pane object makes PriceChart refeed every series.
  const c = useMemo(
    () =>
      sessionChart(session, index, {
        visibility,
        trades,
        label: session.meta.source_label ? `${session.symbol} · ${session.meta.source_label}` : session.symbol,
      }),
    [session, index, visibility, trades],
  );
  if (!c) return null;
  return (
    <div className="absolute inset-0 pb-24">
      <PriceChart
        top={null}
        bottom={c.pane}
        startSec={c.startSec}
        endSec={c.endSec}
        playheadSec={c.playheadSec}
        barSec={c.barSec}
        mode={{ kind: "replay" }}
        stopPrice={null}
        fills={[]}
      />
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
