import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertCircle, ServerCrash } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ReplayToolbar } from "@/components/replay/ReplayToolbar";
import {
  ReplayChart,
  overlaySpecs,
  rsiKey,
  type ChartVisibility,
} from "@/components/replay/ReplayChart";
import { ReplayControlBar } from "@/components/replay/ReplayControlBar";
import { StackedReplayChart } from "@/components/replay/StackedReplayChart";
import { TradePanel } from "@/components/replay/TradePanel";
import { EstimatedBadge } from "@/components/review/EstimatedBadge";
import {
  useReplayContract,
  useReplaySession,
  useReplayTrade,
  useReplayTradeFills,
} from "@/lib/queries";
import { useReplay } from "@/lib/useReplay";
import { formatEt, isoToUtcSeconds, valueAt } from "@/lib/chart";
import {
  candlesInWindow,
  floorMinute,
  INITIAL_WINDOW_MIN,
  revealedFillTime,
  spyPaneLabel,
} from "@/lib/stackedReplay";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import {
  DEFAULT_REPLAY_CONFIG,
  type ReplayConfig,
} from "@/lib/replayConfig";
import { ApiError, type ReplayFill, type IndicatorKey, type ReplaySession, type SimTrade } from "@/lib/api";

const DEFAULT_SYMBOL = "AAPL";
const DEFAULT_DATE = "2026-07-01";

export function ReplayPage() {
  const [symbol, setSymbol] = useState<string>(DEFAULT_SYMBOL);
  const [date, setDate] = useState<string>(DEFAULT_DATE);
  const [config, setConfig] = useState<ReplayConfig>(DEFAULT_REPLAY_CONFIG);
  const [trades, setTrades] = useState<SimTrade[]>([]);

  // 'Replay my trade' mode: ?trade=<id>&lead=<minutes>
  const [params, setParams] = useSearchParams();
  const tradeParam = Number(params.get("trade"));
  const tradeId = Number.isInteger(tradeParam) && tradeParam > 0 ? tradeParam : null;
  const lead = Math.min(240, Math.max(0, Number(params.get("lead") ?? 15) || 0));

  const freeSession = useReplaySession({
    symbol,
    date,
    timeframe: config.timeframe,
    emaPeriods: config.emaPeriods,
    rsiPeriod: config.rsiPeriod,
    orMinutes: config.orMinutes,
    enabled: tradeId === null,
  });
  const tradeReplay = useReplayTrade(tradeId, lead);

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
    return { spyCandles, vwap, contractCandles };
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

  // Trade mode: the header shows the trade's symbol and session date, not the
  // free-replay defaults.
  const shownSymbol = inTrade ? (tradeReplay.data?.session.symbol ?? "…") : symbol;
  const shownDate = inTrade ? (tradeReplay.data?.session.session_date ?? date) : date;

  return (
    <div className="flex h-full flex-col">
      <ReplayToolbar
        symbol={shownSymbol}
        onSymbolChange={inTrade ? () => {} : setSymbol}
        date={shownDate}
        onDateChange={inTrade ? () => {} : setDate}
        controls={
          <TradeReplayPicker
            tradeId={tradeId}
            lead={lead}
            onStart={(id, l) => setParams({ trade: String(id), lead: String(l) })}
            onClear={() => setParams({})}
          />
        }
      />

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
          {ready && inTrade && windowed && (
            <div className="absolute inset-x-0 bottom-20 top-0">
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
                bottom={{ label: spyLabel, candles: windowed.spyCandles, vwap: windowed.vwap }}
                startSec={startSec}
                endSec={endSec}
                playheadSec={playheadSec}
                stopPrice={windowed.contractCandles ? (contractData?.stop_price ?? null) : null}
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
          <ReplayControlBar
            replay={replay}
            disabled={!ready}
            config={config}
            onConfigChange={setConfig}
            indicators={data?.indicators}
            marks={marks}
          />
        </div>

        <aside className="hidden w-80 shrink-0 border-l border-border md:block">
          {tradeId !== null && (
            <FillsPanel
              fills={revealedFills}
              sourceLabel={
                windowed?.contractCandles
                  ? contractData?.estimated
                    ? "estimated premium"
                    : "option premium"
                  : spyLabel
              }
            />
          )}
          <TradePanel
            symbol={symbol}
            sessionDate={date}
            tz={data?.meta.tz ?? "America/New_York"}
            currentCandle={data ? data.candles[replay.currentIndex] ?? null : null}
            trades={trades}
            onTradeCreated={(t) => setTrades((prev) => [...prev, t])}
          />
        </aside>
      </div>
    </div>
  );
}

function TradeReplayPicker({
  tradeId,
  lead,
  onStart,
  onClear,
}: {
  tradeId: number | null;
  lead: number;
  onStart: (id: number, lead: number) => void;
  onClear: () => void;
}) {
  const [idText, setIdText] = useState(tradeId !== null ? String(tradeId) : "");
  const [leadText, setLeadText] = useState(String(lead));
  const id = Number(idText);
  const valid = Number.isInteger(id) && id > 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <label htmlFor="replay-trade-id">Replay my trade</label>
      <input
        id="replay-trade-id"
        className="tabular h-8 w-20 rounded-md border border-border bg-transparent px-2"
        placeholder="trade id"
        inputMode="numeric"
        value={idText}
        onChange={(e) => setIdText(e.target.value)}
      />
      <input
        aria-label="Lead minutes"
        className="tabular h-8 w-14 rounded-md border border-border bg-transparent px-2"
        inputMode="numeric"
        value={leadText}
        onChange={(e) => setLeadText(e.target.value)}
      />
      <span className="text-muted-foreground">min before</span>
      <button
        type="button"
        disabled={!valid}
        className="h-8 rounded-md border border-border px-2 disabled:opacity-50"
        onClick={() => onStart(id, Math.min(240, Math.max(0, Number(leadText) || 0)))}
      >
        Start
      </button>
      {tradeId !== null && (
        <button type="button" className="h-8 px-2 text-muted-foreground" onClick={onClear}>
          Exit
        </button>
      )}
    </div>
  );
}

function FillsPanel({
  fills,
  sourceLabel,
}: {
  fills: ReplayFill[];
  sourceLabel?: string;
}) {
  return (
    <div className="border-b border-border p-3 text-xs">
      <div className="mb-1 font-semibold">Your fills</div>
      <div className="mb-2 text-muted-foreground">
        Hidden until the playhead reaches them. Decide first, then compare.
        {sourceLabel ? ` Bars: ${sourceLabel}.` : ""}
      </div>
      {fills.length === 0 ? (
        <div className="text-muted-foreground">No fills yet.</div>
      ) : (
        <ul className="space-y-1">
          {fills.map((f) => (
            <li key={f.seq} className="tabular flex justify-between">
              <span>
                {f.time_pt} {f.role}
              </span>
              <span>
                {f.side} {f.qty} @ {f.price.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
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
    <div className="pointer-events-none absolute left-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-x-4 gap-y-1 rounded-md bg-card/80 px-3 py-1.5 text-xs backdrop-blur">
      <span className="font-semibold tracking-tight">{session.symbol}</span>
      {session.meta.source_label && (
        <span className="text-muted-foreground">{session.meta.source_label}</span>
      )}
      <span className="tabular text-muted-foreground">{barTime} {DISPLAY_TZ_LABEL}</span>
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
  const notImplemented = status === 501 || status === 404;
  const message =
    error instanceof ApiError
      ? error.detail || error.message
      : error instanceof Error
        ? error.message
        : "Something went wrong loading the replay session.";

  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <Alert variant={notImplemented ? "default" : "destructive"} className="max-w-md">
        {isBackendDown ? (
          <ServerCrash className="h-4 w-4" />
        ) : (
          <AlertCircle className="h-4 w-4" />
        )}
        <AlertTitle>
          {isBackendDown
            ? "Backend not running"
            : notImplemented
              ? "Replay endpoint not ready yet"
              : "Couldn't load session"}
        </AlertTitle>
        <AlertDescription>
          {isBackendDown ? (
            <>
              Start it with{" "}
              <code className="tabular rounded bg-muted px-1 py-0.5">
                make backend
              </code>{" "}
              (expects <span className="tabular">http://localhost:9000</span>).
            </>
          ) : notImplemented ? (
            <>
              <code className="tabular rounded bg-muted px-1 py-0.5">
                /api/replay/session
              </code>{" "}
              isn't implemented on the backend yet. The chart will light up once
              the quant service lands this endpoint.
            </>
          ) : (
            message
          )}
        </AlertDescription>
      </Alert>
    </div>
  );
}
