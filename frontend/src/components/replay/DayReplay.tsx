import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { PriceChart, type PaneSpec } from "@/components/chart/PriceChart";
import { FillsList } from "@/components/replay/FillsList";
import { TradeReplayBar, TradeScrubber } from "@/components/replay/TradeReplayBar";
import { api, ApiError, type ReplayFill, type ReplaySession } from "@/lib/api";
import { isoToUtcSeconds } from "@/lib/chart";
import { formatClock } from "@/lib/format";
import { emaLines, paneLevels, smaLines, vwapMap } from "@/lib/indicatorLines";
import { exitTarget, rthWindow, safeFrom, tradeReplayHref } from "@/lib/replayNav";
import { loadSpeed, saveSpeed } from "@/lib/replaySpeed";
import { candlesInWindow, floorMinute, spyPaneLabel } from "@/lib/stackedReplay";
import { DEFAULT_STACK_INDICATORS, type StackIndicators } from "@/lib/stackIndicators";
import { useReplay } from "@/lib/useReplay";

/**
 * /replay?day=YYYY-MM-DD: the day's trades on one underlying, in order, as one session.
 * Minute bars from 15 min before the open to the close. Trades and fills come from a
 * playhead-gated poll, so nothing later than the playhead ever reaches the browser; each
 * fill is marked on the chart and listed with its contract. A trade can be opened in its
 * own replay from the rail.
 */
export function DayReplay({ day }: { day: string }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const symbolParam = params.get("symbol");
  const from = safeFrom(params.get("from"));
  const [speed, setSpeedState] = useState<number>(() => loadSpeed());
  const setSpeed = (s: number) => {
    setSpeedState(s);
    saveSpeed(s);
  };
  const [ind, setInd] = useState<StackIndicators>(DEFAULT_STACK_INDICATORS);

  const q = useQuery({
    queryKey: ["replay-day", day, symbolParam],
    queryFn: () => api.replayDay(day, symbolParam),
    retry: false,
    staleTime: 5 * 60_000,
  });
  const full = q.data?.session;
  const symbol = q.data?.symbol ?? symbolParam ?? "";

  const win = useMemo(
    () => (full ? rthWindow(full.candles.map((c) => floorMinute(isoToUtcSeconds(c.ts)))) : null),
    [full],
  );
  // The engine plays the window's bars; indicators stay on the full session (causal maps).
  const played: ReplaySession | undefined = useMemo(
    () =>
      full && win
        ? { ...full, candles: candlesInWindow(full.candles, win.startSec, win.endSec), indicators: {}, levels: {} }
        : undefined,
    [full, win],
  );
  const lines = useMemo(
    () => (full ? { vwap: vwapMap(full), emas: emaLines(full), smas: smaLines(full) } : null),
    [full],
  );

  const replay = useReplay(played, { chartSpeed: speed });
  const playTs = played?.candles[replay.currentIndex]?.ts ?? null;
  const playheadSec = playTs ? floorMinute(isoToUtcSeconds(playTs)) : (win?.startSec ?? 0);

  // Same rule as a trade replay on minute bars: the playhead is the bar open.
  const fillsQ = useQuery({
    queryKey: ["replay-day-fills", day, symbol, playTs],
    queryFn: () => api.replayDayFills(day, playTs as string, symbol),
    enabled: Boolean(q.data && playTs),
    placeholderData: (prev) => prev,
  });
  const fills = useMemo(
    () => (fillsQ.data?.fills ?? []).filter((f) => isoToUtcSeconds(f.ts) <= playheadSec + 59),
    [fillsQ.data, playheadSec],
  );
  const trades = fillsQ.data?.trades ?? [];

  const pane: PaneSpec | null = useMemo(() => {
    if (!full || !played || !lines) return null;
    let idx = -1;
    for (let i = 0; i < full.candles.length; i++) {
      if (floorMinute(isoToUtcSeconds(full.candles[i].ts)) > playheadSec) break;
      idx = i;
    }
    const ls = [...(ind.ema ? lines.emas : []), ...(ind.sma ? lines.smas : [])];
    return {
      label: `${spyPaneLabel(full.symbol, full.meta.source_label)} · ${day}`,
      candles: played.candles,
      vwap: ind.vwap ? lines.vwap : undefined,
      levels: paneLevels(full, idx, ind),
      emas: ls.length > 0 ? ls : undefined,
    };
  }, [full, played, lines, playheadSec, ind, day]);

  const seekToFill = (f: { ts: string }) => {
    if (!played) return;
    const sec = Math.ceil(isoToUtcSeconds(f.ts));
    replay.pause();
    const i = played.candles.findIndex((c) => isoToUtcSeconds(c.ts) >= sec);
    if (i >= 0) replay.setIndex(i);
  };

  const here = `/replay?${params.toString()}`;
  const exit = () => navigate(exitTarget(from, null) ?? `/days/${day}`);
  const ready = !q.isLoading && !q.isError && played !== undefined && replay.total > 0;
  const count = q.data?.trade_count ?? 0;

  return (
    <div className="flex h-full flex-col">
      <TradeReplayBar
        contract={`${symbol || "Day"} · ${count} trade${count === 1 ? "" : "s"}`}
        date={day}
        clock={playTs ? formatClock(playTs) : null}
        replay={replay}
        disabled={!ready}
        onExit={exit}
        speed={speed}
        onSpeedChange={setSpeed}
        minuteTag={{ reason: "a day replay plays minute bars (second bars are per trade)" }}
      />
      <div className="flex min-h-0 flex-1">
        <div className="relative min-h-0 flex-1">
          {q.isLoading && (
            <div className="absolute inset-0 flex flex-col gap-2 p-4">
              <Skeleton className="h-6 w-40" />
              <Skeleton className="min-h-0 flex-1" />
            </div>
          )}
          {q.isError && (
            <div className="absolute inset-0 flex items-center justify-center p-6">
              <Alert variant="destructive" className="max-w-md">
                <AlertCircle className="h-4 w-4" />
                <AlertTitle>Could not load the day</AlertTitle>
                <AlertDescription>
                  {q.error instanceof ApiError ? q.error.detail || q.error.message : "Something went wrong."}
                </AlertDescription>
              </Alert>
            </div>
          )}
          {ready && pane && win && (
            <div className="absolute inset-x-0 bottom-10 top-0">
              <PriceChart
                top={null}
                bottom={pane}
                startSec={win.startSec}
                endSec={win.endSec}
                playheadSec={playheadSec}
                mode={{ kind: "replay" }}
                stopPrice={null}
                fills={fills as ReplayFill[]}
                mainPane="bottom"
                fillLabels="arrows"
              />
            </div>
          )}
          <TradeScrubber replay={replay} disabled={!ready} indicators={ind} onIndicatorsChange={setInd} />
        </div>

        <aside className="hidden w-72 shrink-0 overflow-y-auto border-l border-border md:block">
          {(q.data?.symbols.length ?? 0) > 1 && (
            <div className="flex flex-wrap gap-1 px-3 pt-2" role="group" aria-label="Underlying">
              {q.data!.symbols.map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={s === symbol}
                  className={`rounded-md border px-2 py-0.5 text-label ${
                    s === symbol ? "border-play/50 bg-play/15 text-play" : "border-border text-muted-foreground hover:bg-accent"
                  }`}
                  onClick={() => {
                    const next = new URLSearchParams(params);
                    next.set("symbol", s);
                    setParams(next);
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <section className="space-y-1 px-3 pb-2 pt-2" aria-label="Trades so far">
            <span className="label-caps text-label font-semibold text-muted-foreground">
              Trades so far ({trades.length} of {count})
            </span>
            {trades.length === 0 ? (
              <p className="text-label text-muted-foreground">None yet. Press play.</p>
            ) : (
              <ul className="space-y-0.5">
                {trades.map((t) => (
                  <li key={t.trade_id} className="flex items-baseline justify-between gap-2 text-body">
                    <span className="num truncate">
                      {formatClock(t.open_ts)} {t.contract_label ?? `#${t.trade_id}`}
                    </span>
                    <Link
                      className="shrink-0 text-label text-muted-foreground underline-offset-4 hover:underline"
                      to={tradeReplayHref(t.trade_id, here)}
                    >
                      Replay
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <FillsList fills={fills as ReplayFill[]} onSeek={ready ? seekToFill : undefined} />
        </aside>
      </div>
    </div>
  );
}
