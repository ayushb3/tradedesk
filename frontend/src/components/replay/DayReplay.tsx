import { useCallback, useMemo, useState } from "react";
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
import { formatClock, formatPnl } from "@/lib/format";
import { emaLines, paneLevels, smaLines, vwapMap } from "@/lib/indicatorLines";
import { exitTarget, rthWindow, safeFrom, tradeReplayHref } from "@/lib/replayNav";
import { loadSpeed, saveSpeed } from "@/lib/replaySpeed";
import { candlesInWindow, floorMinute, spyPaneLabel } from "@/lib/stackedReplay";
import { DEFAULT_STACK_INDICATORS, type StackIndicators } from "@/lib/stackIndicators";
import { useReplay } from "@/lib/useReplay";
import {
  adjacentEntry,
  groupEntries,
  groupPnl,
  groupWindow,
  parseTradeIds,
} from "@/lib/groupReview";
import { useDeskDay } from "@/lib/queries";
import { formatClockSeconds, formatClockSecondsPt } from "@/lib/secondsReplay";
import { clampSkipSec, skipIndex } from "@/lib/replaySkip";
import { useReplaySkip } from "@/lib/useReplaySkip";
import type { SecondsRes } from "@/lib/secondsChart";
import { useSecondsMode } from "@/lib/useSecondsMode";

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
  // Group review (?trades=1,2,3): only these trades, from 5 min before the first entry to
  // 10 min after the last exit, with one combined P&L (lib/groupReview).
  const pickedIds = useMemo(() => parseTradeIds(params.get("trades")), [params]);
  const deskDay = useDeskDay(pickedIds ? day : null);
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
  // The chart plays one underlying: the group is the picked trades on it (the rest are
  // on the other underlyings' tabs).
  const groupPositions = useMemo(
    () =>
      pickedIds
        ? (deskDay.data?.positions ?? []).filter(
            (p) => pickedIds.includes(p.trade_id) && p.ticker.toUpperCase() === symbol.toUpperCase(),
          )
        : [],
    [pickedIds, deskDay.data, symbol],
  );
  const groupIds = useMemo(
    () => (pickedIds ? groupPositions.map((p) => p.trade_id) : null),
    [pickedIds, groupPositions],
  );
  const otherTickers = pickedIds ? pickedIds.length - (groupIds?.length ?? 0) : 0;

  const win = useMemo(() => {
    if (!full) return null;
    const bars = full.candles.map((c) => floorMinute(isoToUtcSeconds(c.ts)));
    if (!pickedIds || !groupIds) return rthWindow(bars);
    // A group waits for its trades' times; then the window is cut to the session's bars.
    const g = groupWindow(groupPositions, groupIds);
    if (!g || bars.length === 0) return null;
    const startSec = Math.max(g.startSec, bars[0]);
    const endSec = Math.min(g.endSec, bars[bars.length - 1]);
    return endSec > startSec ? { startSec, endSec } : null;
  }, [full, pickedIds, groupIds, groupPositions]);
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

  // 5s / 15s / 30s: second bars from trade prints, on a second-level clock (lib/useSecondsMode).
  const [secRes, setSecRes] = useState<SecondsRes | null>(null);
  const minuteReplay = useReplay(played, { chartSpeed: speed, keyboardEnabled: secRes === null });
  const playTs = played?.candles[minuteReplay.currentIndex]?.ts ?? null;
  const playheadSec = playTs ? floorMinute(isoToUtcSeconds(playTs)) : (win?.startSec ?? 0);
  const indexOfMinute = useCallback(
    (m: number) => {
      if (!played) return 0;
      const i = played.candles.findIndex((c) => isoToUtcSeconds(c.ts) >= m);
      return i < 0 ? played.candles.length - 1 : i; // past the end: the last bar
    },
    [played],
  );
  const sm = useSecondsMode({
    replay: minuteReplay,
    res: secRes,
    symbol: full?.symbol,
    minuteSec: playTs ? playheadSec : null,
    speed,
    indexOfMinute,
  });
  const replay = sm.replay;
  // The last second shown: the playhead second in seconds mode, else the end of the minute.
  const lastSec = sm.active && sm.playSec != null ? sm.playSec : playheadSec + 59;
  // Second bars drawn once they have landed; while loading or unavailable the minute chart
  // stands in, cut at the last finished minute when the clock is inside a minute.
  const secShown = sm.active && !sm.chart.reason && sm.chart.candles.length > 0;
  const minuteCut = sm.midMinute ? playheadSec - 60 : playheadSec;
  // Fills: the server gates them at the playhead, so in seconds mode ask at the second.
  const fillsAt = sm.active && sm.playSec != null ? new Date(sm.playSec * 1000).toISOString() : playTs;

  // Same rule as a trade replay on minute bars: the playhead is the bar open.
  const fillsQ = useQuery({
    queryKey: ["replay-day-fills", day, symbol, fillsAt],
    queryFn: () => api.replayDayFills(day, fillsAt as string, symbol),
    enabled: Boolean(q.data && fillsAt),
    placeholderData: (prev) => prev,
  });
  const inGroup = useCallback((id: number) => !groupIds || groupIds.includes(id), [groupIds]);
  const fills = useMemo(
    () => (fillsQ.data?.fills ?? []).filter((f) => isoToUtcSeconds(f.ts) <= lastSec && inGroup(f.trade_id)),
    [fillsQ.data, lastSec, inGroup],
  );
  const trades = (fillsQ.data?.trades ?? []).filter((t) => inGroup(t.trade_id));

  const pane: PaneSpec | null = useMemo(() => {
    if (!full || !played || !lines) return null;
    let idx = -1;
    for (let i = 0; i < full.candles.length; i++) {
      // Levels as of the last finished minute while the seconds clock is inside one.
      if (floorMinute(isoToUtcSeconds(full.candles[i].ts)) > minuteCut) break;
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
  }, [full, played, lines, minuteCut, ind, day]);

  const seekToFill = (f: { ts: string }) => {
    if (!played) return;
    const sec = Math.ceil(isoToUtcSeconds(f.ts));
    replay.pause();
    if (sm.active) {
      sm.clock.setPlaySec(sec);
      return;
    }
    const i = played.candles.findIndex((c) => isoToUtcSeconds(c.ts) >= sec);
    if (i >= 0) replay.setIndex(i);
  };

  // Skip ([ / ]): one whole candle of the chart's interval, to its close.
  const [candleSec, setCandleSec] = useState(60);
  const skipSeek = useCallback(
    (sec: number, dir: 1 | -1) => {
      if (!played || played.candles.length === 0) return;
      replay.pause();
      const opens = played.candles.map((c) => isoToUtcSeconds(c.ts));
      if (sm.active) sm.clock.setPlaySec(clampSkipSec(sec, opens[0], opens[opens.length - 1]));
      else replay.setIndex(skipIndex(opens, replay.currentIndex, sec, dir));
    },
    [replay, sm.active, sm.clock, played],
  );
  const skip = useReplaySkip({
    posSec: sm.active ? sm.playSec : playTs ? playheadSec : null,
    candleSec,
    unitSec: sm.active ? 1 : 60,
    seek: skipSeek,
    enabled: played !== undefined && replay.total > 0,
  });
  const transport = useMemo(() => (skip ? { ...replay, ...skip } : replay), [replay, skip]);

  const here = `/replay?${params.toString()}`;
  const exit = () => navigate(exitTarget(from, null) ?? `/days/${day}`);
  const ready = !q.isLoading && !q.isError && played !== undefined && replay.total > 0;
  const count = groupIds ? groupIds.length : (q.data?.trade_count ?? 0);

  // Group: one combined P&L (closed trades only, as of the playhead) and Next/Prev entry.
  const gPnl = groupIds ? groupPnl(groupPositions, groupIds, lastSec) : null;
  const entries = useMemo(() => (groupIds ? groupEntries(groupPositions, groupIds) : []), [groupIds, groupPositions]);
  const seekToSec = (sec: number) => {
    if (!played) return;
    replay.pause();
    if (sm.active) {
      sm.clock.setPlaySec(sec);
      return;
    }
    const i = played.candles.findIndex((c) => isoToUtcSeconds(c.ts) >= floorMinute(sec));
    if (i >= 0) replay.setIndex(i);
  };
  const prevEntry = adjacentEntry(entries, lastSec, -1);
  const nextEntry = adjacentEntry(entries, lastSec, 1);

  return (
    <div className="flex h-full flex-col">
      <TradeReplayBar
        contract={`${symbol || "Day"} · ${groupIds ? "group of " : ""}${count} trade${count === 1 ? "" : "s"}`}
        nav={
          groupIds
            ? {
                onPrev: ready && prevEntry != null ? () => seekToSec(prevEntry) : null,
                onNext: ready && nextEntry != null ? () => seekToSec(nextEntry) : null,
                prevLabel: "Previous entry in the group",
                nextLabel: "Next entry in the group",
              }
            : undefined
        }
        daySoFar={
          gPnl ? (
            <span
              className="num text-label text-muted-foreground"
              title={`The group's P&L from the trades closed so far (results show only once played)${
                otherTickers > 0 ? `. ${otherTickers} picked trade${otherTickers === 1 ? " is" : "s are"} on another underlying.` : ""
              }`}
              data-testid="group-pnl"
            >
              Group{" "}
              <span className={gPnl.closed === 0 ? "" : gPnl.pnl >= 0 ? "text-gain" : "text-loss"}>
                {gPnl.closed === 0 ? "--" : formatPnl(gPnl.pnl)}
              </span>{" "}
              · {gPnl.closed} of {gPnl.total} closed
            </span>
          ) : undefined
        }
        date={day}
        clock={sm.active && sm.playSec != null ? formatClockSeconds(sm.playSec) : playTs ? formatClock(playTs) : null}
        replay={transport}
        disabled={!ready}
        onExit={exit}
        speed={speed}
        onSpeedChange={setSpeed}
        minuteTag={sm.active ? null : { reason: "pick 5s, 15s or 30s under the chart to play second bars" }}
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
          {pickedIds && full && deskDay.isSuccess && !win && (
            <div className="absolute inset-0 flex items-center justify-center p-6 text-body text-muted-foreground" data-testid="group-empty">
              None of those trades are on this day&apos;s {symbol} chart.
            </div>
          )}
          {ready && sm.active && (sm.chart.loading || sm.chart.reason) && (
            <div
              className="absolute left-1/2 top-10 z-40 -translate-x-1/2 rounded-md border border-border bg-card/95 px-2 py-1 text-label text-muted-foreground"
              data-testid="seconds-status"
            >
              {sm.chart.reason ? `No second bars here (${sm.chart.reason}). Showing 1m bars.` : "Loading second bars…"}
            </div>
          )}
          {ready && pane && win && (
            <div className="absolute inset-x-0 bottom-10 top-0">
              <PriceChart
                top={null}
                bottom={
                  secShown
                    ? {
                        ...pane,
                        label: `${pane.label} · ${secRes}${sm.chart.sourceLabel ? ` from ${sm.chart.sourceLabel}` : ""}`,
                        candles: sm.chart.candles,
                      }
                    : pane
                }
                startSec={secShown ? sm.chart.startSec : win.startSec}
                endSec={secShown ? sm.chart.endSec : win.endSec}
                playheadSec={secShown ? (sm.barOpenSec as number) : minuteCut}
                barSec={secShown ? sm.resSec : 60}
                revealSec={secShown ? (sm.playSec as number) : undefined}
                playheadLabel={secShown && sm.playSec != null ? formatClockSecondsPt(sm.playSec) : undefined}
                seconds={{
                  value: secRes,
                  onChange: (v) => setSecRes(v as SecondsRes | null),
                  note: sm.chart.sourceLabel,
                }}
                onCandleSec={setCandleSec}
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
