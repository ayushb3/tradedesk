import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Candle, ReplayFill } from "@/lib/api";
import type { SimFill } from "@/lib/simPosition";
import { isoToUtcSeconds, makeDisplayFormatters } from "@/lib/chart";
import { DISPLAY_TZ, DISPLAY_TZ_LABEL } from "@/lib/format";
import {
  alignedCandles,
  buildTimeline,
  candleAt,
  growDomain,
  type Domain,
  floorMinute,
  revealedFillTime,
} from "@/lib/stackedReplay";

// Keep in sync with the tokens in index.css (lightweight-charts needs literal colors).
const THEME = {
  text: "#a1a1aa",
  grid: "#26262b",
  up: "#26a69a",
  down: "#ef5350",
  play: "#3b82f6", // playhead / crosshair
  stop: "#f59e0b", // the stop
  vwap: "#22d3ee",
  avg: "#a1a1aa", // blended average entry
};
const PRICE_SCALE_WIDTH = 72;

export interface PaneSpec {
  /** Header, e.g. "SPY, proxy for SPX". */
  label: string;
  /** Bars inside the trade window (lead start to exit + 30 min). */
  candles: Candle[];
  /** Optional causal VWAP keyed by minute (epoch seconds). */
  vwap?: Map<number, number>;
  badge?: React.ReactNode;
}

interface Props {
  /** Contract premium pane; null when the option bars are unavailable. */
  top: PaneSpec | null;
  /** SPY pane, always present. */
  bottom: PaneSpec;
  /** Lead start .. window end, epoch seconds. */
  startSec: number;
  endSec: number;
  /** Bar-open time of the playhead, epoch seconds. */
  playheadSec: number;
  /** The sim's stop (amber, draggable); null while flat. */
  stopPrice: number | null;
  stopAssumed?: boolean;
  /** Blended average entry of the open sim position; null while flat. */
  avgPrice?: number | null;
  /** Fills of the user's sim buys/sells on the contract pane. */
  simFills?: SimFill[];
  /** Called while dragging the stop (final = false) and on release (final = true). */
  onStopChange?: (price: number, final: boolean) => void;
  /** Fills the server has revealed so far (gated server-side). */
  fills: ReplayFill[];
}

interface Layout {
  width: number;
  playX: number | null;
  entryX: number | null;
  exitX: number | null;
  stopY: number | null;
}

const EMPTY_LAYOUT: Layout = { width: 0, playX: null, entryX: null, exitX: null, stopY: null };

const hm = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const ptClock = (sec: number) => `${hm.format(sec * 1000)} ${DISPLAY_TZ_LABEL}`;

interface PaneRuntime {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  vwap: ISeriesApi<"Line">;
}

/**
 * Two stacked panes on one shared minute grid: contract premium (~60%) over SPY
 * (~40%). Both series carry every minute of the window (whitespace where there is
 * no bar or the playhead has not arrived), so a minute lines up vertically; the
 * time scales, playhead and crosshair are synced. The y-domain is fixed over the
 * whole window, so nothing rescales while the playhead moves.
 */
export function StackedReplayChart({
  top,
  bottom,
  startSec,
  endSec,
  playheadSec,
  stopPrice,
  stopAssumed = false,
  avgPrice = null,
  simFills = [],
  onStopChange,
  fills,
}: Props) {
  const topEl = useRef<HTMLDivElement>(null);
  const bottomEl = useRef<HTMLDivElement>(null);
  const panes = useRef<{ top: PaneRuntime | null; bottom: PaneRuntime } | undefined>(undefined);
  const stopLine = useRef<IPriceLine | null>(null);
  const avgLine = useRef<IPriceLine | null>(null);
  const dragging = useRef<number | null>(null);
  const domains = useRef<{ top: Domain | null; bottom: Domain | null }>({ top: null, bottom: null });

  const [hoverSec, setHoverSec] = useState<number | null>(null);
  const [layoutTick, setLayoutTick] = useState(0);
  const [layouts, setLayouts] = useState<{ top: Layout; bottom: Layout }>({
    top: EMPTY_LAYOUT,
    bottom: EMPTY_LAYOUT,
  });

  const timeline = useMemo(() => buildTimeline(startSec, endSec), [startSec, endSec]);
  const hasTop = top !== null;

  const entrySec = revealedFillTime(fills, "entry", playheadSec);
  const exitSec = revealedFillTime(fills, "exit", playheadSec);

  // y-domains grow with the revealed bars only (never shrink, never see the
  // future). Reset when the window changes.
  const domainKey = `${startSec}:${hasTop}`;
  const domainKeyRef = useRef(domainKey);
  if (domainKeyRef.current !== domainKey) {
    domainKeyRef.current = domainKey;
    domains.current = { top: null, bottom: null };
  }
  domains.current = {
    top: top
      ? growDomain(domains.current.top, top.candles, playheadSec, [stopPrice, avgPrice])
      : null,
    bottom: growDomain(domains.current.bottom, bottom.candles, playheadSec),
  };

  // ---- create the charts once per layout (stacked / SPY only) ---------------
  useEffect(() => {
    const bottomNode = bottomEl.current;
    if (!bottomNode) return;
    const fmt = makeDisplayFormatters();

    const make = (el: HTMLDivElement, which: "top" | "bottom", showAxis: boolean): PaneRuntime => {
      const chart = createChart(el, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: THEME.text,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        },
        grid: {
          vertLines: { color: THEME.grid },
          horzLines: { color: THEME.grid },
        },
        crosshair: {
          mode: CrosshairMode.Magnet,
          vertLine: { color: THEME.play, labelBackgroundColor: THEME.play, style: LineStyle.Solid },
          horzLine: { visible: false, labelVisible: false },
        },
        rightPriceScale: { borderColor: THEME.grid, minimumWidth: PRICE_SCALE_WIDTH },
        localization: { timeFormatter: fmt.timeFormatter },
        timeScale: {
          borderColor: THEME.grid,
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 0,
          tickMarkFormatter: fmt.tickMarkFormatter,
          visible: showAxis,
        },
        // The window is fixed: no wheel zoom or drag pan.
        handleScroll: false,
        handleScale: false,
        autoSize: false,
      });
      const series = chart.addCandlestickSeries({
        upColor: THEME.up,
        downColor: THEME.down,
        borderVisible: false,
        wickUpColor: THEME.up,
        wickDownColor: THEME.down,
        priceLineVisible: false,
        lastValueVisible: false,
        autoscaleInfoProvider: () => {
          const d = domains.current[which];
          return d ? { priceRange: d } : null;
        },
      });
      const vwap = chart.addLineSeries({
        color: THEME.vwap,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null, // never part of the fixed domain
      });
      return { chart, series, vwap };
    };

    const topPane = topEl.current ? make(topEl.current, "top", false) : null;
    const bottomPane = make(bottomNode, "bottom", true);
    panes.current = { top: topPane, bottom: bottomPane };
    const all = [topPane, bottomPane].filter((p): p is PaneRuntime => p !== null);

    // Sync the crosshair across panes and report the hovered minute.
    let syncing = false;
    for (const src of all) {
      src.chart.subscribeCrosshairMove((param) => {
        if (syncing) return;
        syncing = true;
        try {
          const t = typeof param.time === "number" ? (param.time as number) : null;
          setHoverSec(t);
          for (const dst of all) {
            if (dst === src) continue;
            if (t === null) dst.chart.clearCrosshairPosition();
            else dst.chart.setCrosshairPosition(0, t as UTCTimestamp, dst.series);
          }
        } finally {
          syncing = false;
        }
      });
    }

    const resize = () => {
      if (topPane && topEl.current)
        topPane.chart.applyOptions({
          width: topEl.current.clientWidth,
          height: topEl.current.clientHeight,
        });
      bottomPane.chart.applyOptions({
        width: bottomNode.clientWidth,
        height: bottomNode.clientHeight,
      });
      for (const p of all) p.chart.timeScale().fitContent();
      setLayoutTick((n) => n + 1);
    };
    resize();
    const ro = new ResizeObserver(resize);
    if (topEl.current) ro.observe(topEl.current);
    ro.observe(bottomNode);

    return () => {
      ro.disconnect();
      for (const p of all) p.chart.remove();
      panes.current = undefined;
      stopLine.current = null;
      avgLine.current = null;
    };
  }, [hasTop]);

  // ---- data: aligned on the shared grid, revealed up to the playhead ---------
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const feed = (rt: PaneRuntime, spec: PaneSpec) => {
      rt.series.setData(alignedCandles(timeline, spec.candles, playheadSec));
      const line = spec.vwap
        ? timeline
            .filter((t) => t <= playheadSec && spec.vwap!.has(t))
            .map((t) => ({ time: t, value: spec.vwap!.get(t) as number }))
        : [];
      rt.vwap.setData(line);
    };
    if (p.top && top) feed(p.top, top);
    feed(p.bottom, bottom);
    for (const rt of [p.top, p.bottom]) rt?.chart.timeScale().fitContent();
    setLayoutTick((n) => n + 1);
  }, [timeline, top, bottom, playheadSec, hasTop]);

  // ---- stop line (amber), contract pane only ---------------------------------
  useEffect(() => {
    const rt = panes.current?.top;
    if (!rt) return;
    if (stopLine.current) {
      rt.series.removePriceLine(stopLine.current);
      stopLine.current = null;
    }
    if (stopPrice != null) {
      stopLine.current = rt.series.createPriceLine({
        price: stopPrice,
        color: THEME.stop,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: stopAssumed ? "STOP (assumed)" : "STOP",
      });
    }
  }, [stopPrice, stopAssumed, hasTop]);

  // ---- blended average entry (grey dotted), contract pane only ----------------
  useEffect(() => {
    const rt = panes.current?.top;
    if (!rt) return;
    if (avgLine.current) {
      rt.series.removePriceLine(avgLine.current);
      avgLine.current = null;
    }
    if (avgPrice != null) {
      avgLine.current = rt.series.createPriceLine({
        price: avgPrice,
        color: THEME.avg,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: "AVG",
      });
    }
  }, [avgPrice, hasTop]);

  // ---- fill markers (contract pane only; prices are premium) -----------------
  useEffect(() => {
    const rt = panes.current?.top;
    if (!rt) return;
    const markers: SeriesMarker<UTCTimestamp>[] = [];
    for (const f of fills) {
      const time = floorMinute(isoToUtcSeconds(f.ts));
      if (time > playheadSec) continue; // second guard; the server already gates
      const isBuy = f.side === "buy";
      markers.push({
        time: time as UTCTimestamp,
        position: isBuy ? "belowBar" : "aboveBar",
        color: isBuy ? THEME.up : THEME.down,
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: `${f.role} ${f.qty} @ ${f.price.toFixed(2)} ${f.time_pt}`,
      });
    }
    for (const f of simFills) {
      const time = floorMinute(isoToUtcSeconds(f.ts));
      if (time > playheadSec) continue;
      const isBuy = f.side === "buy";
      markers.push({
        time: time as UTCTimestamp,
        position: isBuy ? "belowBar" : "aboveBar",
        color: THEME.play,
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: `${isBuy ? "B" : "S"} ${f.price.toFixed(2)}`,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    rt.series.setMarkers(markers);
  }, [fills, simFills, playheadSec, hasTop, timeline]);

  // ---- overlay geometry (px) for the shaded future / window / ticks ----------
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const measure = (rt: PaneRuntime | null): Layout => {
      if (!rt) return EMPTY_LAYOUT;
      const ts = rt.chart.timeScale();
      const x = (sec: number | null) =>
        sec === null ? null : (ts.timeToCoordinate(sec as UTCTimestamp) as number | null);
      return {
        width: ts.width(),
        playX: x(playheadSec),
        entryX: x(entrySec),
        exitX: x(exitSec),
        stopY:
          rt === p.top && stopPrice != null
            ? (rt.series.priceToCoordinate(stopPrice) as number | null)
            : null,
      };
    };
    setLayouts({ top: measure(p.top), bottom: measure(p.bottom) });
  }, [layoutTick, playheadSec, entrySec, exitSec, stopPrice, hasTop]);

  // ---- dragging the stop ---------------------------------------------------------
  const priceAtPointer = (e: React.PointerEvent): number | null => {
    const rt = panes.current?.top;
    const el = topEl.current;
    if (!rt || !el) return null;
    const y = e.clientY - el.getBoundingClientRect().top;
    const raw = rt.series.coordinateToPrice(y);
    if (raw == null) return null;
    const d = domains.current.top;
    // Stay inside the fixed y-domain so a drag never rescales the pane.
    const clamped = d ? Math.min(d.maxValue, Math.max(d.minValue, raw)) : raw;
    const price = Math.round(clamped * 100) / 100;
    return price > 0 ? price : null;
  };
  const stopHandle =
    stopPrice != null && layouts.top.stopY != null && onStopChange ? (
      <div
        role="slider"
        aria-label="Stop price"
        aria-valuenow={stopPrice}
        className="pointer-events-auto absolute inset-x-0 z-10 h-4 cursor-row-resize touch-none"
        style={{ top: layouts.top.stopY - 8 }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          dragging.current = stopPrice;
        }}
        onPointerMove={(e) => {
          if (dragging.current === null) return;
          const price = priceAtPointer(e);
          if (price === null) return;
          dragging.current = price;
          onStopChange(price, false);
        }}
        onPointerUp={() => {
          if (dragging.current === null) return;
          const price = dragging.current;
          dragging.current = null;
          onStopChange(price, true);
        }}
        onPointerCancel={() => {
          dragging.current = null;
        }}
      />
    ) : null;

  const shown = hoverSec ?? playheadSec;

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      {top && (
        <Pane
          className="flex-[6]"
          spec={top}
          layout={layouts.top}
          shown={shown}
          playheadSec={playheadSec}
          hovered={hoverSec !== null}
          elRef={topEl}
          showTicks
          overlay={stopHandle}
        />
      )}
      <Pane
        className={top ? "flex-[4] border-t border-border" : "flex-1"}
        spec={bottom}
        layout={layouts.bottom}
        shown={shown}
        playheadSec={playheadSec}
        hovered={hoverSec !== null}
        elRef={bottomEl}
        showTicks={!top}
      />
    </div>
  );
}

function Pane({
  className,
  spec,
  layout,
  shown,
  playheadSec,
  hovered,
  elRef,
  showTicks,
  overlay,
}: {
  className: string;
  spec: PaneSpec;
  layout: Layout;
  shown: number;
  playheadSec: number;
  hovered: boolean;
  elRef: React.RefObject<HTMLDivElement>;
  showTicks: boolean;
  overlay?: React.ReactNode;
}) {
  const bar = candleAt(spec.candles, shown, playheadSec);
  const { width, playX, entryX, exitX } = layout;
  const windowEnd = exitX ?? playX;
  return (
    <div className={`relative min-h-0 ${className}`}>
      <div ref={elRef} className="absolute inset-0" />
      {/* Overlays share the plot area's width (the price scale sits to the right). */}
      <div className="pointer-events-none absolute inset-y-0 left-0 overflow-hidden" style={{ width }}>
        {entryX !== null && windowEnd !== null && (
          <div
            className="absolute inset-y-0 bg-play/10"
            style={{ left: entryX, width: Math.max(0, windowEnd - entryX) }}
          />
        )}
        {playX !== null && (
          <div
            className="absolute inset-y-0 bg-grey/15"
            style={{ left: playX, width: Math.max(0, width - playX) }}
          />
        )}
        {entryX !== null && (
          <div className="absolute inset-y-0 w-px bg-foreground/50" style={{ left: entryX }}>
            {showTicks && <TickLabel>IN</TickLabel>}
          </div>
        )}
        {exitX !== null && (
          <div className="absolute inset-y-0 w-px bg-foreground/50" style={{ left: exitX }}>
            {showTicks && <TickLabel>OUT</TickLabel>}
          </div>
        )}
        {playX !== null && <div className="absolute inset-y-0 w-px bg-play" style={{ left: playX }} />}
        {overlay}
      </div>
      {/* Header + OHLC readout for the hovered (or playhead) minute. */}
      <div className="pointer-events-none absolute left-3 top-2 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-card/80 px-2 py-1 backdrop-blur">
        <span className="label-caps text-label font-semibold">{spec.label}</span>
        {spec.badge}
        <span className="num text-label text-muted-foreground">
          {ptClock(shown)}
          {hovered ? "" : " · playhead"}
        </span>
        {bar ? (
          <span className="num flex gap-2 text-label">
            <span>O {bar.open.toFixed(2)}</span>
            <span>H {bar.high.toFixed(2)}</span>
            <span>L {bar.low.toFixed(2)}</span>
            <span className={bar.close >= bar.open ? "text-gain" : "text-loss"}>
              C {bar.close.toFixed(2)}
            </span>
          </span>
        ) : (
          <span className="num text-label text-muted-foreground">no bar</span>
        )}
      </div>
    </div>
  );
}

function TickLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="num absolute bottom-1 left-1 rounded bg-card/80 px-1 text-label text-foreground">
      {children}
    </span>
  );
}
