import { useCallback, useEffect, useMemo, useRef, useState, type ComponentProps, type ReactNode } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ServerCrash } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ReplayToolbar } from "@/components/replay/ReplayToolbar";
import { RuleSummary } from "@/components/replay/RuleBar";
import {
  TradeReplayBar,
  TradeScrubber,
} from "@/components/replay/TradeReplayBar";
import { DayReplay } from "@/components/replay/DayReplay";
import { ReplayControlBar } from "@/components/replay/ReplayControlBar";
import { PriceChart, type ChartPosition } from "@/components/chart/PriceChart";
import { RealTradeCard } from "@/components/replay/RealTradeCard";
import { OrderPanel } from "@/components/replay/OrderPanel";
import { ContractPicker, type OptionSide } from "@/components/replay/ContractPicker";
import { TradeScreen } from "@/components/screen/TradeScreen";
import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import {
  useDeskRules,
  useReplayDaySoFar,
  useReplayContract,
  useReplayOptionBars,
  useReplaySession,
  useReplaySecondsProbe,
  useReplayTrade,
  useReplayTradeFills,
  useTrade,
} from "@/lib/queries";
import { ruleBarState } from "@/lib/ruleBar";
import {
  DEFAULT_STACK_INDICATORS,
  type StackIndicators,
} from "@/lib/stackIndicators";
import { emaLines, paneLevels, smaLines, vwapMap } from "@/lib/indicatorLines";
import {
  DEFAULT_SPEED,
  engineFor,
  loadSpeed,
  saveSpeed,
  secondsRes,
} from "@/lib/replaySpeed";
import { exitTarget, isDay, safeFrom } from "@/lib/replayNav";
import { useReplay, type ReplayState } from "@/lib/useReplay";
import { isInputFocused } from "@/lib/replayKeys";
import { barSecOf, sessionChart, type ChartVisibility } from "@/lib/sessionChart";
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
import { optionTagFromLabel } from "@/lib/chartLook";
import type { SecondsRes } from "@/lib/secondsChart";
import { useSecondsMode } from "@/lib/useSecondsMode";
import { useReplaySkip } from "@/lib/useReplaySkip";
import { clampSkipSec, sizeLabel, skipIndex } from "@/lib/replaySkip";
import {
  ruleCloseFill,
  sessionRealized,
  totalPnl,
  type SimFill,
} from "@/lib/simPosition";
import {
  atmStrike,
  contractBarAt,
  contractLabel,
  dayLimitHit,
  inTradingWindow,
  isOptionRoot,
  OPTION_ROOTS,
  pricing,
  ptMinutesOf,
  RULE_CLOSE_FRAC,
  underlyingScale,
  type OptionRoot,
} from "@/lib/freeOptions";
import { openPnl, positionAt } from "@/lib/positionAt";
import { isoToUtcSeconds } from "@/lib/chart";
import {
  candlesInWindow,
  floorMinute,
  INITIAL_WINDOW_MIN,
  revealedFillTime,
  spyPaneLabel,
} from "@/lib/stackedReplay";
import { formatClock } from "@/lib/format";
import { DEFAULT_REPLAY_CONFIG, type ReplayConfig } from "@/lib/replayConfig";
import {
  api,
  ApiError,
  REPLAY_TIMEFRAMES,
  type Candle,
  type ReplaySession,
} from "@/lib/api";

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
  if (!hasTrade && params.get("mode") !== "symbol")
    return <Navigate to="/days" replace />;
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
  const [freeSpeed, setFreeSpeed] = useState<number>(DEFAULT_SPEED);
  const setSpeed = (s: number) => {
    setSpeedState(s);
    saveSpeed(s);
  };
  // Practice (?mode=symbol&date=...&symbol=...) opens on that day and ticker.
  const [initParams] = useSearchParams();
  const [symbol, setSymbol] = useState<string>(() => {
    const s = initParams.get("symbol")?.trim().toUpperCase();
    return s && /^[A-Z.]{1,8}$/.test(s) ? s : FREE_SYMBOL;
  });
  const [date, setDate] = useState<string>(() => {
    const d = initParams.get("date");
    return isDay(d) ? d : previousWeekday();
  });
  const [stackInd, setStackInd] = useState<StackIndicators>(
    DEFAULT_STACK_INDICATORS,
  );
  const [config, setConfig] = useState<ReplayConfig>(DEFAULT_REPLAY_CONFIG);

  // 'Replay my trade' mode: ?trade=<id>&lead=<minutes>
  const [params, setParams] = useSearchParams();
  const tradeParam = Number(params.get("trade"));
  const tradeId = embed
    ? embed.tradeId
    : Number.isInteger(tradeParam) && tradeParam > 0
      ? tradeParam
      : null;
  const [embedLead, setEmbedLead] = useState(15);
  const lead = embed
    ? embedLead
    : Math.min(240, Math.max(0, Number(params.get("lead") ?? 15) || 0));
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
        (contractReplay.isError
          ? "option bars could not be loaded"
          : "option bars unavailable"))
      : null;

  const tradeSession = tradeReplay.data?.session;
  // x-range: lead start + a fixed 90 min (clamped to the session). That depends
  // only on the entry. The real end (exit + 30 min) arrives with /fills only once
  // the playhead reaches the exit; the range then extends if it is later.
  const startSec = tradeReplay.data
    ? floorMinute(isoToUtcSeconds(tradeReplay.data.start_ts))
    : 0;
  const spyAll = tradeSession?.candles;
  const sessionEndSec =
    spyAll && spyAll.length > 0
      ? isoToUtcSeconds(spyAll[spyAll.length - 1].ts)
      : startSec;
  const initialEndSec = Math.min(
    startSec + INITIAL_WINDOW_MIN * 60,
    sessionEndSec,
  );
  // The chart holds the whole session from its first bar (premarket), so it can zoom out to
  // it; it opens on the lead window. The replay engine still plays from the lead start.
  const sessionStartSec =
    spyAll && spyAll.length > 0
      ? floorMinute(isoToUtcSeconds(spyAll[0].ts))
      : startSec;
  const [revealedEnd, setRevealedEnd] = useState<{
    key: string;
    sec: number;
  } | null>(null);
  const endKey = `${tradeId}:${lead}`;
  const revealedEndSec = revealedEnd?.key === endKey ? revealedEnd.sec : 0;
  const endSec = Math.max(
    initialEndSec,
    Math.min(revealedEndSec, sessionEndSec),
  );

  // Below 60x the replay plays second bars when the trade has them (the speed slider is the
  // only switch); otherwise minute bars, with a "minute bars" tag saying why.
  const secondsProbe = useReplaySecondsProbe(
    tradeId,
    lead,
    tradeReplay.data?.start_ts ?? null,
  );
  const secondsAvailable = secondsProbe.data?.available === true;
  const useSeconds =
    tradeId !== null && engineFor(speed, secondsAvailable) === "seconds";
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
            : (secondsProbe.data?.reason ??
              (secondsProbe.isError
                ? "request failed"
                : "no second-level prints")),
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
    const chartSpy = candlesInWindow(
      tradeSession.candles,
      sessionStartSec,
      endSec,
    );
    const chartContract = hasContract
      ? candlesInWindow(contractData!.candles, sessionStartSec, endSec)
      : null;
    // EMAs 9/21/50 and SMAs 200/400 (looks from INDICATOR_LOOK), minute-keyed; toggled in spyPane.
    const emas = emaLines(tradeSession);
    const smas = smaLines(tradeSession);
    return {
      spyCandles,
      vwap,
      emas,
      smas,
      contractCandles,
      chartSpy,
      chartContract,
    };
  }, [
    tradeSession,
    startSec,
    endSec,
    sessionStartSec,
    hasContract,
    contractData,
  ]);

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
      chartContract: windowedAll.chartContract
        ? replaySeries(windowedAll.chartContract, secs.contract, ph)
        : null,
      vwap: finishedOnly(windowedAll.vwap, ph),
      emas: windowedAll.emas.map((e) => ({
        ...e,
        values: finishedOnly(e.values, ph),
      })),
      smas: windowedAll.smas.map((e) => ({
        ...e,
        values: finishedOnly(e.values, ph),
      })),
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
        meta: {
          ...meta,
          source_label: contractData.estimated
            ? "estimated premium"
            : "option premium",
        },
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
  const isLoading = inTrade
    ? tradeReplay.isLoading || contractReplay.isLoading
    : freeSession.isLoading;
  const isError = active.isError;
  const error = active.error;
  const data: ReplaySession | undefined = inTrade
    ? stackSession
    : freeSession.data;

  // The session is already cut to the trade window, so the replay starts at bar 0
  // (the engine resets to the first bar whenever the bar count changes).
  // Free replay, 5s / 15s / 30s: second bars on a second-level clock (lib/useSecondsMode).
  const [freeSecRes, setFreeSecRes] = useState<SecondsRes | null>(null);
  const freeSecOn = !inTrade && freeSecRes !== null;
  const minuteReplay = useReplay(data, {
    keyboardEnabled: !useSeconds && !freeSecOn,
    // The speed control drives the minute clock (64x ≈ one 1m bar per second).
    chartSpeed: inTrade ? speed : freeSpeed,
  });

  // In Seconds mode the seconds clock is the truth and the transport controls drive it; the
  // minute engine only supplies the bar count. Everything below reads `replay`.
  const secIndex = data ? minuteIndexAt(data.candles, secs.playSec) : 0;
  const tradeReplayState: ReplayState = useSeconds
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
  const freeMinuteSec =
    !inTrade && data?.candles[minuteReplay.currentIndex]
      ? isoToUtcSeconds(data.candles[minuteReplay.currentIndex].ts)
      : null;
  const freeIndexOfMinute = useCallback(
    (m: number) => {
      if (!data) return 0;
      const i = data.candles.findIndex((c) => isoToUtcSeconds(c.ts) >= m);
      return i < 0 ? data.candles.length - 1 : i; // past the end: the last bar
    },
    [data],
  );
  const freeSm = useSecondsMode({
    replay: tradeReplayState,
    // Second bars ride on 1-minute bars only (the clock counts minutes by bar index).
    res: inTrade || !data || barSecOf(data.candles) !== 60 ? null : freeSecRes,
    symbol: inTrade ? null : symbol,
    minuteSec: freeMinuteSec,
    speed: freeSpeed,
    indexOfMinute: freeIndexOfMinute,
  });
  // Picking a seconds interval from 5m reloads 1m bars, and the engine restarts at bar 0 when
  // the bar count changes: seek back to where the playhead was.
  const pendingSeek = useRef<number | null>(null);
  const { setIndex: minuteSetIndex } = minuteReplay;
  useEffect(() => {
    const at = pendingSeek.current;
    if (at == null || !data || data.candles.length === 0 || barSecOf(data.candles) !== 60) return;
    pendingSeek.current = null;
    minuteSetIndex(freeIndexOfMinute(Math.floor(at / 60) * 60));
  }, [data, minuteSetIndex, freeIndexOfMinute]);
  const replayBase: ReplayState = freeSm.active
    ? {
        ...freeSm.replay,
        barLabel: freeSm.playSec != null ? formatClockSecondsPt(freeSm.playSec) : minuteReplay.barLabel,
      }
    : tradeReplayState;

  // Skip ([ / ]): one whole candle of the chart's interval, to its close. The playhead moves
  // in seconds on a seconds clock (trade replay ≥60x, free replay 5s/15s/30s), else by bar.
  const [candleSec, setCandleSec] = useState(60);
  const dataBarSec = data && data.candles.length > 1 ? barSecOf(data.candles) : 60;
  const onSecondsClock = useSeconds || freeSm.active;
  const barTs = data?.candles[replayBase.currentIndex]?.ts;
  const skipPos = useSeconds ? secs.playSec : freeSm.active ? freeSm.playSec : barTs ? isoToUtcSeconds(barTs) : null;
  const { seek: skipSecsSeek } = secs;
  const { setPlaySec: freeSetPlaySec } = freeSm.clock;
  const { pause: basePause, setIndex: baseSetIndex, currentIndex: baseIndex } = replayBase;
  const skipSeek = useCallback(
    (sec: number, dir: 1 | -1) => {
      if (!data || data.candles.length === 0) return;
      basePause();
      const opens = data.candles.map((c) => isoToUtcSeconds(c.ts));
      if (useSeconds) skipSecsSeek(sec); // clamps to the trade window itself
      else if (freeSm.active) freeSetPlaySec(clampSkipSec(sec, opens[0], opens[opens.length - 1]));
      else baseSetIndex(skipIndex(opens, baseIndex, sec, dir));
    },
    [basePause, useSeconds, skipSecsSeek, freeSm.active, freeSetPlaySec, data, baseSetIndex, baseIndex],
  );
  const skip = useReplaySkip({
    posSec: skipPos,
    candleSec,
    unitSec: onSecondsClock ? 1 : dataBarSec,
    seek: skipSeek,
    enabled: Boolean(data && data.candles.length > 0),
  });
  const replay: ReplayState = {
    ...replayBase,
    ...(skip ?? {}),
    // Free replay on server 5m/15m bars steps one of those bars.
    stepLabel: !onSecondsClock && dataBarSec !== 60 ? sizeLabel(dataBarSec) : undefined,
  };

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
      if (floorMinute(isoToUtcSeconds(tradeSession.candles[i].ts)) > levelSec)
        break;
      idx = i;
    }
    // Ladder levels (causal at idx) plus the caller's posted levels for the day.
    const levels = paneLevels(tradeSession, idx, stackInd);
    const lines = [
      ...(stackInd.ema ? windowed.emas : []),
      ...(stackInd.sma ? windowed.smas : []),
    ];
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
    setRevealedEnd((prev) =>
      prev?.key === endKey && prev.sec >= sec ? prev : { key: endKey, sec },
    );
  }, [windowEndTs, endKey]);

  // Extending the window changes the bar count, which resets the engine to bar 0;
  // put the playhead (and playback) back where it was.
  const lastRun = useRef({ total: 0, index: 0, playing: false });
  const { setIndex, play } = minuteReplay;
  useEffect(() => {
    const prev = lastRun.current;
    const m = minuteReplay;
    if (
      inTrade &&
      !useSeconds &&
      prev.total > 0 &&
      m.total !== prev.total &&
      m.total > prev.total
    ) {
      setIndex(prev.index);
      if (prev.playing) play();
    }
    lastRun.current = {
      total: m.total,
      index: m.total !== prev.total ? prev.index : m.currentIndex,
      playing: m.total !== prev.total ? prev.playing : m.isPlaying,
    };
  }, [
    inTrade,
    useSeconds,
    minuteReplay.total,
    minuteReplay.currentIndex,
    minuteReplay.isPlaying,
    setIndex,
    play,
  ]);

  const revealedFills = useMemo(
    // Fills reveal at their exact second in Seconds mode, at the end of the playhead minute otherwise.
    () =>
      (fillsQuery.data?.fills ?? []).filter(
        (f) =>
          isoToUtcSeconds(f.ts) <=
          (useSeconds ? secs.playSec : playheadSec + 59),
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
      const i = data.candles.findIndex(
        (c) => floorMinute(isoToUtcSeconds(c.ts)) >= sec,
      );
      return i < 0 ? null : i;
    };
    return {
      entryIndex: idxOf(revealedFillTime(revealedFills, "entry", playheadSec)),
      exitIndex: idxOf(revealedFillTime(revealedFills, "exit", playheadSec)),
    };
  }, [inTrade, data, revealedFills, playheadSec]);

  // Free replay trades 0DTE option contracts (SPY, QQQ, SPXW): the underlying on top, the
  // picked contract below. A new symbol or date starts a fresh practice day.
  const sessionKey = `${symbol}:${date}`;
  const root: OptionRoot | null =
    !inTrade && isOptionRoot(symbol.toUpperCase())
      ? (symbol.toUpperCase() as OptionRoot)
      : null;
  const underBar =
    !inTrade && data ? (data.candles[replay.currentIndex] ?? null) : null;
  const underSec = underBar ? isoToUtcSeconds(underBar.ts) : null;
  // SPXW's underlying is the labelled SPY proxy on Alpaca (no SPX index bars), so its strikes
  // sit about 10x the chart; an unproxied index series is already on the strike scale.
  const proxied = Boolean(data?.meta.source_label);
  const atm =
    root && underBar
      ? atmStrike(root, proxied ? underBar.close : underBar.close / underlyingScale(root))
      : null;
  const [pick, setPick] = useState<{
    key: string;
    side: OptionSide;
    strike: number | null;
  }>({ key: sessionKey, side: "call", strike: null });
  const side: OptionSide = pick.key === sessionKey ? pick.side : "call";
  const pickedStrike = pick.key === sessionKey ? pick.strike : null;
  // Until a strike is picked, start at the money (as of the first bar the page shows).
  useEffect(() => {
    if (root && atm != null && pickedStrike == null)
      setPick({ key: sessionKey, side, strike: atm });
  }, [root, atm, pickedStrike, sessionKey, side]);
  const strike = pickedStrike ?? atm;
  const optionSpec =
    root && strike != null
      ? { underlying: root, type: side, strike, date, timeframe: config.timeframe }
      : null;
  const optQuery = useReplayOptionBars(optionSpec);
  const optCandles = optQuery.data?.available ? optQuery.data.candles : null;
  // Seconds mode, mid-minute: the option has only 1-minute bars, so its price is the last
  // finished minute's (the forming minute's close would be look-ahead).
  const midMinute = freeSm.active && freeSm.clock.offset < 59;
  const optSec = underSec != null && midMinute ? underSec - 60 : underSec;
  const optFound =
    optCandles && optSec != null ? contractBarAt(optCandles, optSec) : null;
  const optAt = optFound && midMinute ? { ...optFound, stale: true } : optFound;
  const optShown = useMemo(
    () =>
      optCandles && midMinute && optSec != null
        ? optCandles.filter((c) => isoToUtcSeconds(c.ts) <= optSec)
        : optCandles,
    [optCandles, midMinute, optSec],
  );
  const optLabel =
    root && strike != null ? contractLabel(root, side, strike, date) : "";

  // Trade-mode sim: fills at the playhead bar's close on the premium pane.
  // Seconds mode: the latest print so far (never the minute's final close).
  // Free replay: the picked contract's bar at the playhead (the last print when that minute
  // has none: "stale").
  const playCandle = data
    ? inTrade
      ? useSeconds
        ? liveCandle(windowed?.contractCandles ?? [], secs.playSec)
        : (data.candles[replay.currentIndex] ?? null)
      : optAt && underBar
        ? optAt.stale
          ? {
              ...optAt.bar,
              // last price, stamped at the playhead (its second in seconds mode)
              ts: freeSm.active && freeSm.playSec != null ? new Date(freeSm.playSec * 1000).toISOString() : underBar.ts,
            }
          : optAt.bar
        : null
    : null;
  const tradeSymbol = inTrade
    ? (contractData?.symbol ?? tradeSession?.symbol ?? "")
    : optLabel;
  const sim = useSimSession({
    resetKey: inTrade ? endKey : `free:${sessionKey}`,
    symbol: tradeSymbol,
    sessionDate: tradeSession?.session_date ?? date,
    writtenStop: inTrade ? (contractData?.stop_price ?? null) : null,
    // Free replay: the stop starts at the -50% close rule; fills pay a spread and fees.
    assumedStopFrac: inTrade ? undefined : RULE_CLOSE_FRAC,
    pricing: root ? pricing(root) : undefined,
  });
  const simPos = sim.state.position;
  // Positions on the chart, marked at the playhead bar's close (Seconds mode: the latest print):
  // the real trade from the fills revealed so far, and the practice position.
  const mark = playCandle?.close ?? null;
  const chartPositions = useMemo((): ChartPosition[] => {
    const out: ChartPosition[] = [];
    const real = positionAt(revealedFills, Number.POSITIVE_INFINITY); // already gated at the playhead
    if (real.qty > 0)
      out.push({
        key: "real",
        kind: "real",
        qty: real.qty,
        avg: real.avg,
        pnl: openPnl(real, mark),
      });
    if (simPos.qty > 0) {
      const p = { qty: simPos.qty, avg: simPos.avg, realized: 0 };
      const firstQty = simPos.fills[0]?.qty ?? 0;
      const riskPerShare =
        simPos.risk != null && firstQty > 0
          ? simPos.risk / (100 * firstQty)
          : null;
      out.push({
        key: "sim",
        kind: "sim",
        qty: simPos.qty,
        avg: simPos.avg,
        pnl: openPnl(p, mark),
        riskPerShare,
      });
    }
    return out;
  }, [revealedFills, simPos.qty, simPos.avg, simPos.risk, simPos.fills, mark]);
  // Stop moves are stamped with the playhead bar they were made on.
  const { setPlayTs } = sim;
  useEffect(
    () => setPlayTs(playCandle?.ts ?? null),
    [setPlayTs, playCandle?.ts],
  );

  // Shift+B = buy qty, Shift+S = sell qty, Shift+Alt+F = flatten (Space / arrows live in
  // useReplay). Ignored while focus is in an input, with Ctrl/Meta held, or with nothing to trade
  // (trade replay: no premium bars; free replay: no session loaded yet).
  const ready = !isLoading && !isError && data && replay.total > 0;

  // Free replay rules. Close at -50%: checked on every contract bar after the last fill up
  // to the playhead (scrubbing forward can't skip one). Rule 12 has no exceptions.
  const { onRuleClose, onCloseAll: simCloseAll } = sim;
  useEffect(() => {
    if (inTrade || !optCandles || underSec == null || simPos.qty === 0) return;
    if (sim.stateKey !== `free:${sessionKey}`) return; // a position from the previous day/symbol
    const last = simPos.fills[simPos.fills.length - 1];
    if (!last) return;
    const after = isoToUtcSeconds(last.ts);
    for (const b of optCandles) {
      const t = isoToUtcSeconds(b.ts);
      if (t <= after) continue;
      if (optSec == null || t > optSec) break;
      const px = ruleCloseFill(simPos, b, RULE_CLOSE_FRAC);
      if (px != null) {
        onRuleClose(px, b.ts);
        break;
      }
    }
  }, [inTrade, optCandles, underSec, optSec, simPos, onRuleClose, sim.stateKey, sessionKey]);

  // The practice day ends at the daily stop or target (from the rules file), like live:
  // the position is closed and new orders are off for this symbol and day.
  const deskRules = useDeskRules();
  const dayStop = deskRules.data?.daily_stop ?? null;
  const dayTarget = deskRules.data?.daily_target ?? null;
  const dayPnl =
    sessionRealized(sim.state) +
    (simPos.qty > 0 && playCandle ? totalPnl(simPos, playCandle.close) : 0);
  const [dayDone, setDayDone] = useState<{
    key: string;
    why: "stop" | "target";
  } | null>(null);
  const doneWhy = !inTrade && dayDone?.key === sessionKey ? dayDone.why : null;
  useEffect(() => {
    // The sim resets one commit after the session key changes: don't judge the old day's P&L.
    if (inTrade || doneWhy || sim.stateKey !== `free:${sessionKey}`) return;
    const hit = dayLimitHit(dayPnl, dayStop, dayTarget);
    if (!hit) return;
    if (simPos.qty > 0) {
      if (!playCandle) return; // no price to close at yet: lock once it can be closed
      simCloseAll(playCandle);
    }
    setDayDone({ key: sessionKey, why: hit });
  }, [inTrade, doneWhy, sim.stateKey, dayPnl, dayStop, dayTarget, sessionKey, simPos.qty, simCloseAll, playCandle]);

  // Outside the trading windows (rules file: the main window plus any extra ones) practice
  // still works; the panel says so.
  const windows = deskRules.data
    ? [deskRules.data.window, ...(deskRules.data.extra_windows ?? [])].filter(
        (w): w is { start: string; end: string } => Boolean(w?.start && w?.end),
      )
    : [];
  const outsideWindow =
    !inTrade &&
    underBar != null &&
    windows.length > 0 &&
    !inTradingWindow(ptMinutesOf(underBar.ts), windows);

  const freeNote: string | undefined = inTrade
    ? undefined
    : !root
      ? `Options trade on ${OPTION_ROOTS.join(", ")}. Pick one of those.`
      : doneWhy
        ? `Practice day done: the day hit its ${doneWhy === "stop" ? "daily stop" : "daily target"}. Pick another date to keep practicing.`
        : optQuery.isLoading
          ? `Loading ${optLabel}…`
          : optQuery.isError
            ? `${optLabel}: option bars could not be loaded.`
            : optQuery.data && !optQuery.data.available
              ? `${optLabel}: ${optQuery.data.reason ?? "no option bars"}.`
              : !optAt
                ? `${optLabel} has no prints yet. Play to the open.`
                : undefined;
  const canTrade = inTrade
    ? windowed?.contractCandles != null
    : Boolean(ready && optAt && !doneWhy);
  const [orderQty, setOrderQty] = useState(1);
  // One practice order of `orderQty` contracts (one fill row, 1R from the whole first buy).
  const buyQty = () => sim.onBuy(playCandle, orderQty);
  const sellQty = () => sim.onSell(playCandle, orderQty); // sell() caps at the open qty
  const flatten = () => sim.onCloseAll(playCandle);
  const keyActs = useRef({ buy: () => {}, sell: () => {}, flatten: () => {} });
  keyActs.current = { buy: buyQty, sell: sellQty, flatten };
  useEffect(() => {
    if (!canTrade) return;
    const onKey = (e: KeyboardEvent) => {
      if (
        e.ctrlKey ||
        e.metaKey ||
        !e.shiftKey ||
        e.repeat ||
        isInputFocused(e.target)
      )
        return;
      if (e.code === "KeyF" && e.altKey) {
        e.preventDefault();
        keyActs.current.flatten();
      } else if (e.altKey) {
        return;
      } else if (e.code === "KeyB") {
        e.preventDefault();
        keyActs.current.buy();
      } else if (e.code === "KeyS") {
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

  const visibility: ChartVisibility = useMemo(
    () => ({
      hiddenOverlays: config.hiddenOverlays,
      hiddenLevels: config.hiddenLevels,
    }),
    [config.hiddenOverlays, config.hiddenLevels],
  );

  // Trade mode header: the contract and the trade's session date.
  const tradeContract =
    tradeRow.data?.contract_label ??
    contractData?.symbol ??
    tradeReplay.data?.session.symbol ??
    "…";
  const tradeDate =
    tradeRow.data?.session_date ?? tradeReplay.data?.session.session_date ?? "";
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
    id
      ? () => (embed ? embed.onGoTrade(id) : setParams(tradeParams(id, lead)))
      : null;
  const exit = () => {
    if (embed) return embed.onExit();
    navigate(exitTarget(from, tradeId) ?? "/days");
  };

  // Buy to open / Sell to close in the traded pane's header (premium in trade replay, the
  // underlying in free replay); same as Shift+B / Shift+S.
  const orderButtons = (
    <>
      <button
        type="button"
        disabled={!canTrade}
        onClick={buyQty}
        className="rounded border border-up/60 px-1.5 text-label text-up disabled:opacity-40"
      >
        Buy to open
      </button>
      <button
        type="button"
        disabled={!canTrade || simPos.qty === 0}
        onClick={sellQty}
        className="rounded border border-down/60 px-1.5 text-label text-down disabled:opacity-40"
      >
        Sell to close
      </button>
    </>
  );

  return (
    <TradeScreen
      bar={
        inTrade ? (
          <>
            <TradeReplayBar
              contract={tradeContract}
              date={tradeDate}
              lead={lead}
              onLeadChange={setLead}
              clock={
                playheadTs
                  ? useSeconds
                    ? formatClockSeconds(secs.playSec)
                    : formatClock(playheadTs)
                  : null
              }
              replay={replay}
              disabled={!ready}
              onExit={exit}
              speed={speed}
              onSpeedChange={setSpeed}
              minuteTag={minuteTag}
              nav={{
                onPrev: goTrade(neighbors.data?.prev_id),
                onNext: goTrade(neighbors.data?.next_id),
              }}
              daySoFar={
                <RuleSummary state={bar} loading={daySoFar.isLoading} />
              }
            />
          </>
        ) : (
          <ReplayToolbar
            symbol={symbol}
            onSymbolChange={setSymbol}
            date={date}
            onDateChange={setDate}
            suggestions={OPTION_ROOTS}
            extra={
              root && strike != null ? (
                <ContractPicker
                  root={root}
                  side={side}
                  onSideChange={(s) =>
                    setPick({ key: sessionKey, side: s, strike })
                  }
                  strike={strike}
                  onStrikeChange={(k) =>
                    setPick({ key: sessionKey, side, strike: k })
                  }
                  atm={atm}
                  date={date}
                  locked={simPos.qty > 0}
                />
              ) : null
            }
            controls={
              <button
                type="button"
                className="text-label text-muted-foreground underline-offset-4 hover:underline"
                onClick={() => navigate(from ?? "/days")}
              >
                {from?.startsWith("/days/") ? "Back to the day" : "Back to Days"}
              </button>
            }
          />
        )
      }
      chart={
        <div className="relative min-h-0 flex-1">
          {contractNote && (
            <div className="absolute right-3 top-3 z-20 max-w-sm" role="status">
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Contract bars unavailable</AlertTitle>
                <AlertDescription>
                  {contractNote}. Showing SPY only.
                </AlertDescription>
              </Alert>
            </div>
          )}
          {useSeconds && secs.error && (
            <div
              className="absolute left-3 top-3 z-20 rounded-md bg-card/90 px-3 py-1.5 text-label"
              role="status"
            >
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
                        badge: contractData?.estimated ? (
                          <EstimatedBadge />
                        ) : undefined,
                      }
                    : null
                }
                bottom={spyPane}
                startSec={sessionStartSec}
                endSec={endSec}
                defaultView={{ fromSec: startSec }}
                playheadSec={playheadSec}
                revealSec={useSeconds ? secs.playSec : playheadSec + 59}
                onCandleSec={setCandleSec}
                mode={{ kind: "replay" }}
                stopPrice={windowed.contractCandles ? simPos.stop : null}
                stopAssumed={simPos.stopAssumed}
                positions={windowed.contractCandles ? chartPositions : []}
                targetPrice={windowed.contractCandles ? simPos.target : null}
                onTargetChange={(price) => sim.onSetTarget(price)}
                simFills={windowed.contractCandles ? simFills : []}
                onStopChange={sim.onSetStop}
                fills={windowed.contractCandles ? revealedFills : []}
                playheadLabel={
                  useSeconds ? formatClockSecondsPt(secs.playSec) : undefined
                }
                mainPane="bottom"
                contractBelow
                fillLabels="arrows"
                topActions={orderButtons}
                positionTag={optionTagFromLabel(tradeContract) ?? undefined}
              />
            </div>
          )}
          {ready && !inTrade && data && (
            <FreeReplayChart
              session={data}
              onCandleSec={setCandleSec}
              // Seconds clock inside a minute: the minute view and its levels stop at the last finished minute.
              index={midMinute ? Math.max(0, replay.currentIndex - 1) : replay.currentIndex}
              visibility={visibility}
              label={
                root === "SPXW" && data.meta.source_label
                  ? `SPXW · ${data.meta.source_label}: strikes ≈ SPY × ${underlyingScale(root)}`
                  : undefined
              }
              contract={
                optShown && optLabel
                  ? {
                      label: optLabel,
                      candles: optShown,
                      stale: optAt?.stale ?? false,
                    }
                  : null
              }
              trade={{
                positions: chartPositions,
                simFills,
                stopPrice: simPos.stop,
                stopAssumed: simPos.stopAssumed,
                onStopChange: sim.onSetStop,
                targetPrice: simPos.target,
                onTargetChange: sim.onSetTarget,
                actions: orderButtons,
              }}
              interval={{
                value: config.timeframe,
                options: REPLAY_TIMEFRAMES,
                onChange: (tf) =>
                  setConfig({
                    ...config,
                    timeframe: tf as typeof config.timeframe,
                  }),
              }}
              seconds={{
                value: freeSecRes,
                onChange: (v: string | null) => {
                  // Second bars ride on the 1-minute player.
                  if (v && config.timeframe !== "1m") {
                    pendingSeek.current = freeMinuteSec;
                    setConfig({ ...config, timeframe: "1m" });
                  }
                  setFreeSecRes(v as SecondsRes | null);
                },
                note: freeSm.chart.sourceLabel,
              }}
              secondsView={
                freeSm.active && !freeSm.chart.reason && freeSm.chart.candles.length > 0 && freeSm.playSec != null && freeSm.barOpenSec != null
                  ? {
                      candles: freeSm.chart.candles,
                      startSec: freeSm.chart.startSec,
                      endSec: freeSm.chart.endSec,
                      barSec: freeSm.resSec,
                      barOpenSec: freeSm.barOpenSec,
                      playSec: freeSm.playSec,
                      note: `${freeSecRes}${freeSm.chart.sourceLabel ? ` from ${freeSm.chart.sourceLabel}` : ""}`,
                    }
                  : null
              }
              secondsStatus={
                freeSm.active && (freeSm.chart.loading || freeSm.chart.reason)
                  ? freeSm.chart.reason
                    ? `No second bars here (${freeSm.chart.reason}). Showing ${config.timeframe} bars.`
                    : "Loading second bars…"
                  : null
              }
            />
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
              disabled={!ready}
              config={config}
              onConfigChange={setConfig}
              indicators={data?.indicators}
              marks={marks}
            />
          )}
        </div>
      }
      panelLabel="Practice orders"
      panelWidth="w-80"
      panel={
        <>
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
          {!inTrade && (outsideWindow || optAt?.stale) && (
            <div className="space-y-1 border-b border-border px-3 py-2 text-label text-muted-foreground" role="status">
              {optAt?.stale && (
                <p>
                  <StaleTag /> No print this minute: fills use the last price.
                </p>
              )}
              {outsideWindow && (
                <p>
                  Outside your trading window (
                  {windows.map((w) => `${w.start}–${w.end}`).join(", ")} PT).
                  Practice still works.
                </p>
              )}
            </div>
          )}
          <OrderPanel
            candle={playCandle}
            bars={
              inTrade ? (windowed?.contractCandles ?? []) : (optCandles ?? [])
            }
            state={sim.state}
            symbol={tradeSymbol}
            enabled={canTrade}
            qty={orderQty}
            onQtyChange={setOrderQty}
            onBuy={buyQty}
            onSell={sellQty}
            onCloseAll={flatten}
            logError={sim.logError}
            disabledNote={freeNote}
          />
        </>
      }
    />
  );
}

const NO_FILLS: never[] = [];

/** The contract has no print in the playhead minute: its last price stands in. */
function StaleTag() {
  return (
    <span className="rounded border border-border px-1 text-label text-muted-foreground">
      stale
    </span>
  );
}

/**
 * Free replay on the shared chart: the session's underlying (levels, indicators) on top and
 * the picked option contract below it, where the practice orders, pills, stop and target go.
 */
function FreeReplayChart({
  session,
  index,
  visibility,
  label,
  contract,
  trade,
  interval,
  seconds,
  secondsView,
  secondsStatus,
  onCandleSec,
}: {
  /** Told the chart's candle size (for Skip). */
  onCandleSec: (sec: number) => void;
  /** The 5s/15s/30s buttons (free replay draws second bars). */
  seconds: NonNullable<ComponentProps<typeof PriceChart>["seconds"]>;
  /** While a seconds interval is on: the second bars and their grid (replaces the minute view). */
  secondsView: {
    candles: Candle[];
    startSec: number;
    endSec: number;
    barSec: number;
    barOpenSec: number;
    playSec: number;
    note: string;
  } | null;
  secondsStatus: string | null;
  session: ReplaySession;
  index: number;
  visibility: ChartVisibility;
  /** Underlying pane header; default "SYMBOL · source". */
  label?: string;
  /** The picked contract's bars (the whole day; nothing after the playhead is drawn). */
  contract: { label: string; candles: Candle[]; stale: boolean } | null;
  /** The practice position on the contract: pills, stop, target, fill tags, order buttons. */
  trade: {
    positions: ChartPosition[];
    simFills: SimFill[];
    stopPrice: number | null;
    stopAssumed: boolean;
    onStopChange: (price: number, final: boolean) => void;
    targetPrice: number | null;
    onTargetChange: (price: number | null) => void;
    actions: ReactNode;
  };
  /** The chart's Interval buttons pick the bar size this page loads. */
  interval: {
    value: string;
    options: readonly string[];
    onChange: (label: string) => void;
  };
}) {
  // Memoized: a new pane object makes PriceChart refeed every series.
  const c = useMemo(
    () =>
      sessionChart(session, index, {
        visibility,
        label:
          label ??
          (session.meta.source_label
            ? `${session.symbol} · ${session.meta.source_label}`
            : session.symbol),
      }),
    [session, index, visibility, label],
  );
  const top = useMemo(
    () =>
      contract
        ? {
            label: contract.label,
            candles: contract.candles,
            badge: contract.stale ? <StaleTag /> : undefined,
          }
        : null,
    [contract?.label, contract?.candles, contract?.stale],
  );
  const sv = secondsView;
  const bottom = useMemo(
    () => (c && sv ? { ...c.pane, label: `${c.pane.label} · ${sv.note}`, candles: sv.candles } : c?.pane),
    [c, sv],
  );
  if (!c || !bottom) return null;
  return (
    <div className="absolute inset-0 pb-24">
      {secondsStatus && (
        <div
          className="absolute left-1/2 top-10 z-40 -translate-x-1/2 rounded-md border border-border bg-card/95 px-2 py-1 text-label text-muted-foreground"
          data-testid="seconds-status"
        >
          {secondsStatus}
        </div>
      )}
      <PriceChart
        top={top}
        bottom={bottom}
        startSec={sv ? sv.startSec : c.startSec}
        endSec={sv ? sv.endSec : c.endSec}
        playheadSec={sv ? sv.barOpenSec : c.playheadSec}
        barSec={sv ? sv.barSec : c.barSec}
        revealSec={sv ? sv.playSec : undefined}
        playheadLabel={sv ? formatClockSecondsPt(sv.playSec) : undefined}
        seconds={seconds}
        onCandleSec={onCandleSec}
        mode={{ kind: "replay" }}
        stopPrice={top ? trade.stopPrice : null}
        stopAssumed={trade.stopAssumed}
        onStopChange={trade.onStopChange}
        targetPrice={top ? trade.targetPrice : null}
        onTargetChange={trade.onTargetChange}
        positions={top ? trade.positions : []}
        simFills={top ? trade.simFills : []}
        fills={NO_FILLS}
        fillLabels="arrows"
        topActions={trade.actions}
        positionTag={contract ? (optionTagFromLabel(contract.label) ?? undefined) : undefined}
        mainPane="bottom"
        contractBelow
        interval={interval}
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
        <AlertTitle>
          {isBackendDown
            ? "Cannot reach the server"
            : "Could not load the replay"}
        </AlertTitle>
        <AlertDescription>
          {isBackendDown
            ? "Check that the app server is running, then reload."
            : message}
        </AlertDescription>
      </Alert>
    </div>
  );
}
