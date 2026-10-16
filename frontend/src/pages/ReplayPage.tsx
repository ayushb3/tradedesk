import { useState } from "react";
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
import { TradePanel } from "@/components/replay/TradePanel";
import { useReplaySession } from "@/lib/queries";
import { useReplay } from "@/lib/useReplay";
import { formatEt, valueAt } from "@/lib/chart";
import { DISPLAY_TZ_LABEL } from "@/lib/format";
import {
  DEFAULT_REPLAY_CONFIG,
  type ReplayConfig,
} from "@/lib/replayConfig";
import { ApiError, type IndicatorKey, type ReplaySession, type SimTrade } from "@/lib/api";

const DEFAULT_SYMBOL = "AAPL";
const DEFAULT_DATE = "2026-07-01";

export function ReplayPage() {
  const [symbol, setSymbol] = useState<string>(DEFAULT_SYMBOL);
  const [date, setDate] = useState<string>(DEFAULT_DATE);
  const [config, setConfig] = useState<ReplayConfig>(DEFAULT_REPLAY_CONFIG);
  const [trades, setTrades] = useState<SimTrade[]>([]);

  const { data, isLoading, isError, error } = useReplaySession({
    symbol,
    date,
    timeframe: config.timeframe,
    emaPeriods: config.emaPeriods,
    rsiPeriod: config.rsiPeriod,
    orMinutes: config.orMinutes,
  });

  const replay = useReplay(data);

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

  return (
    <div className="flex h-full flex-col">
      <ReplayToolbar
        symbol={symbol}
        onSymbolChange={setSymbol}
        date={date}
        onDateChange={setDate}
      />

      <div className="flex min-h-0 flex-1">
        <div className="relative min-h-0 flex-1">
          {isLoading && <ChartSkeleton />}
          {isError && <ChartError error={error} />}
          {!isLoading && !isError && data && replay.total === 0 && (
            <EmptyState symbol={symbol} date={date} />
          )}
          {ready && (
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
          />
        </div>

        <aside className="hidden w-80 shrink-0 border-l border-border md:block">
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
