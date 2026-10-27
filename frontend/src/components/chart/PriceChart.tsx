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
import { Minus, Plus } from "lucide-react";

import type { Candle, ReplayFill } from "@/lib/api";
import type { SimFill } from "@/lib/simPosition";
import { isoToUtcSeconds, makeDisplayFormatters } from "@/lib/chart";
import { DISPLAY_TZ, DISPLAY_TZ_LABEL } from "@/lib/format";
import {
  chipWidth,
  clampRange,
  defaultRange,
  fillChipText,
  indexOfSec,
  minVisibleBars,
  paddedRange,
  placeChips,
  rightLimit,
  type IndexRange,
  type PriceDomain,
} from "@/lib/priceChart";
import {
  alignedCandles,
  buildTimeline,
  candleAt,
  floorMinute,
  revealedFillTime,
  spreadLabels,
} from "@/lib/stackedReplay";
import { EMA_GREYS } from "@/lib/stackIndicators";
import { greyAfterExit, isZoomWheel, zoomRange } from "@/lib/reviewChart";

// Keep in sync with the tokens in index.css (lightweight-charts needs literal colors).
const THEME = {
  text: "#a1a1aa",
  grid: "#26262b",
  up: "#26a69a",
  down: "#ef5350",
  play: "#3b82f6", // playhead / crosshair / sim fills
  stop: "#f59e0b", // the stop
  vwap: "#22d3ee",
  avg: "#a1a1aa", // blended average entry
};
const PRICE_SCALE_WIDTH = 72;
const CHIP_LANE_PX = 22;
const WHEEL_ZOOM = 1.2;
/** lightweight-charts' logical range reaches a bar's centre; show half a bar past each end so edge bars are whole. */
const EDGE = 0.5;
const readRange = (r: { from: number; to: number } | null): IndexRange | null =>
  r ? { from: r.from + EDGE, to: r.to - EDGE } : null;
const BUTTON_ZOOM = 1.5;
/** Level lines are neutral slate; indicators never use the meaning hues. */
const LEVEL_COLOR = "#64748b";
const LEVEL_LABEL_GAP = 16;

export interface PaneSpec {
  /** Header, e.g. "SPY, proxy for SPX". */
  label: string;
  /** Bars inside the window (lead start to window end). */
  candles: Candle[];
  /** Optional causal VWAP keyed by minute (epoch seconds). */
  vwap?: Map<number, number>;
  /** Causal level values at the playhead, drawn as thin lines with in-pane labels. */
  levels?: { label: string; price: number }[];
  /** Optional EMAs (grey), each keyed by minute. */
  emas?: { label: string; values: Map<number, number> }[];
  /** Labelled solid price lines the page draws itself (Learn drills: your level, stop, target). */
  marks?: { label: string; price: number; color: string }[];
  badge?: React.ReactNode;
}

/**
 * Replay: a live playhead. The view can never reach past the playhead bar, and nothing
 * after it is drawn or scaled to.
 * Review: a post-trade view. There is no playhead; `playheadSec` is the last minute whose
 * bar is drawn, and bars after `exitSec` are grey.
 */
export type ChartMode = { kind: "replay" } | { kind: "review"; exitSec: number | null };

interface Props {
  /** Contract premium pane; null when the option bars are unavailable. */
  top: PaneSpec | null;
  /** Underlying pane, always present. */
  bottom: PaneSpec;
  /** Window start .. end, epoch seconds (the shared minute grid). */
  startSec: number;
  endSec: number;
  /** Bar-open time of the playhead (replay) or last drawn bar (review), epoch seconds. */
  playheadSec: number;
  mode: ChartMode;
  /** The stop (amber; draggable when `onStopChange` is given); null while flat. */
  stopPrice: number | null;
  stopAssumed?: boolean;
  /** Blended average entry of the open sim position; null while flat. */
  avgPrice?: number | null;
  /** Fills of the user's sim buys/sells on the contract pane. */
  simFills?: SimFill[];
  /** Called while dragging the stop (final = false) and on release (final = true). */
  onStopChange?: (price: number, final: boolean) => void;
  /** Real fills already revealed (replay gates them server-side; review shows all). */
  fills: ReplayFill[];
  /** Set when the bars end before the wanted window does: the last bar's minute. */
  noDataAfterSec?: number | null;
  /** Replay, Seconds mode: the playhead readout with seconds, e.g. "06:47:12 PT", and fill chips with seconds. */
  playheadLabel?: string;
  /** Called with the clicked price on the underlying pane (not after a drag-pan). */
  onPriceClick?: (price: number) => void;
}

interface Layout {
  width: number;
  playX: number | null;
  entryX: number | null;
  exitX: number | null;
  stopY: number | null;
  height: number;
  levelYs: { label: string; price: number; y: number }[];
  chips: { x: number; text: string; side: "buy" | "sell" }[];
  /** Price at the bottom/top edge of the pane and the visible bar indices (also exposed as data-* for checks). */
  yMin: number | null;
  yMax: number | null;
  from: number | null;
  to: number | null;
}

const EMPTY_LAYOUT: Layout = {
  width: 0,
  playX: null,
  entryX: null,
  exitX: null,
  stopY: null,
  height: 0,
  levelYs: [],
  chips: [],
  yMin: null,
  yMax: null,
  from: null,
  to: null,
};

const hm = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const ptClock = (sec: number) => `${hm.format(sec * 1000)} ${DISPLAY_TZ_LABEL}`;
const hms = new Intl.DateTimeFormat("en-US", {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const ptClockSec = (sec: number) => `${hms.format(sec * 1000)} ${DISPLAY_TZ_LABEL}`;

interface PaneRuntime {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  vwap: ISeriesApi<"Line">;
  emas: ISeriesApi<"Line">[];
  levelLines: IPriceLine[];
}

interface ViewState {
  /** The user's (or default) visible range, timeline indices. Null until first applied. */
  view: IndexRange | null;
  /** Replay: the right edge sits on the playhead and moves with it. */
  follow: boolean;
  /** Replay, following: keep this many bars (slide); null grows from the lead start. */
  slideWidth: number | null;
}

/**
 * The one price chart: a contract-premium pane over the underlying on ONE shared minute
 * grid, used by both Replay and Trade review so they cannot drift apart.
 *
 * Interaction (identical everywhere): drag pans, Ctrl/Cmd/Alt + wheel (or the +/- buttons)
 * zooms the time axis, a plain wheel scrolls the page, double-click or the Fit button
 * resets. Pan and zoom stay synced across the panes. Each pane autoscales to its VISIBLE
 * played bars with a few percent of padding. In replay the right edge of the view is
 * clamped to the playhead bar.
 */
const STOP_PREF_KEY = "td.chart.stopLine";

export function PriceChart({
  top,
  bottom,
  startSec,
  endSec,
  playheadSec,
  mode,
  stopPrice: stopPriceProp,
  stopAssumed = false,
  avgPrice = null,
  simFills = [],
  onStopChange,
  fills,
  noDataAfterSec = null,
  playheadLabel,
  onPriceClick,
}: Props) {
  const isReplay = mode.kind === "replay";
  // Stop line on/off (remembered per browser); hidden means not drawn and not in the autoscale.
  const [showStop, setShowStop] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(STOP_PREF_KEY) !== "off";
    } catch {
      return true;
    }
  });
  const toggleStop = () =>
    setShowStop((v) => {
      try {
        window.localStorage.setItem(STOP_PREF_KEY, v ? "off" : "on");
      } catch {
        /* storage blocked: the toggle still works for this view */
      }
      return !v;
    });
  const stopPrice = showStop ? stopPriceProp : null;
  const reviewExit = mode.kind === "review" ? mode.exitSec : null;
  const topEl = useRef<HTMLDivElement>(null);
  const bottomEl = useRef<HTMLDivElement>(null);
  const panes = useRef<{ top: PaneRuntime | null; bottom: PaneRuntime } | undefined>(undefined);
  const stopLine = useRef<IPriceLine | null>(null);
  const avgLine = useRef<IPriceLine | null>(null);
  const dragging = useRef<number | null>(null);
  /** Last autoscaled domain per pane; frozen while the stop is dragged so the pane cannot run away. */
  const lastDomain = useRef<{ top: PriceDomain | null; bottom: PriceDomain | null }>({ top: null, bottom: null });
  const frozen = useRef<PriceDomain | null>(null);
  const viewState = useRef<ViewState>({ view: null, follow: true, slideWidth: null });
  const controls = useRef<{ zoom: (factor: number) => void; reset: () => void } | null>(null);

  const [hoverSec, setHoverSec] = useState<number | null>(null);
  const [layoutTick, setLayoutTick] = useState(0);
  const [layouts, setLayouts] = useState<{ top: Layout; bottom: Layout }>({
    top: EMPTY_LAYOUT,
    bottom: EMPTY_LAYOUT,
  });

  const timeline = useMemo(() => buildTimeline(startSec, endSec), [startSec, endSec]);
  const hasTop = top !== null;
  const lastIdx = timeline.length - 1;
  const playIdx = indexOfSec(timeline[0] ?? startSec, playheadSec, Math.max(0, lastIdx));

  // Everything the imperative handlers need, refreshed every render.
  const live = useRef({
    isReplay,
    playIdx,
    lastIdx,
    extras: [] as (number | null)[],
    onPriceClick: undefined as ((price: number) => void) | undefined,
  });
  live.current = { isReplay, playIdx, lastIdx: Math.max(0, lastIdx), extras: [stopPrice, avgPrice], onPriceClick };

  const entrySec = revealedFillTime(fills, "entry", playheadSec);
  const exitSec = revealedFillTime(fills, "exit", playheadSec);

  // ---- create the charts once per layout (stacked / underlying only) ---------
  useEffect(() => {
    const bottomNode = bottomEl.current;
    if (!bottomNode) return;
    const fmt = makeDisplayFormatters();

    const make = (el: HTMLDivElement, which: "top" | "bottom", showAxis: boolean): PaneRuntime => {
      const chart = createChart(el, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: THEME.text,
          fontSize: 12,
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
        // Padding comes from our own autoscale; no extra margins (premium must not dip below 0).
        rightPriceScale: {
          borderColor: THEME.grid,
          minimumWidth: PRICE_SCALE_WIDTH,
          scaleMargins: { top: 0, bottom: 0 },
        },
        localization: { timeFormatter: fmt.timeFormatter },
        timeScale: {
          borderColor: THEME.grid,
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 0,
          tickMarkFormatter: fmt.tickMarkFormatter,
          visible: showAxis,
        },
        // Drag pans; the wheel is handled below (modifier + wheel zooms, a plain wheel scrolls the page).
        handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
        handleScale: { mouseWheel: false, pinch: false, axisPressedMouseMove: false },
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
        // The visible played bars (whitespace is never counted), padded a few percent.
        autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
          if (frozen.current && which === "top") return { priceRange: frozen.current };
          const b = base();
          if (!b || !b.priceRange) return null;
          const d = paddedRange(
            { min: b.priceRange.minValue, max: b.priceRange.maxValue },
            which === "top" ? live.current.extras : [],
            { floorZero: which === "top" },
          );
          if (!d) return b;
          lastDomain.current[which] = d;
          return { priceRange: d };
        },
      });
      const vwap = chart.addLineSeries({
        color: THEME.vwap,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null, // never part of the scale
      });
      const emas = EMA_GREYS.map((color) =>
        chart.addLineSeries({
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null,
        }),
      );
      return { chart, series, vwap, emas, levelLines: [] };
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

    // A click on the underlying pane reports its price (lightweight-charts does not fire it after a drag).
    bottomPane.chart.subscribeClick((param) => {
      const cb = live.current.onPriceClick;
      if (!cb || !param.point) return;
      const raw = bottomPane.series.coordinateToPrice(param.point.y);
      if (raw == null) return;
      const price = Math.round(raw * 100) / 100;
      if (price > 0) cb(price);
    });

    // ---- the visible range: one source of truth, clamped, synced ----------------
    const limits = () => {
      const minBars = minVisibleBars(bottomPane.chart.timeScale().width());
      const l = live.current;
      return {
        minBars,
        rightLimit: rightLimit({ replay: l.isReplay, playIdx: l.playIdx, lastIdx: l.lastIdx, minBars }),
      };
    };
    let applying = false;
    const gesture = { active: false, timer: 0 as unknown as ReturnType<typeof setTimeout> };
    const gestureStart = () => {
      clearTimeout(gesture.timer);
      gesture.active = true;
    };
    const gestureEnd = () => {
      clearTimeout(gesture.timer);
      gesture.timer = setTimeout(() => (gesture.active = false), 800); // kinetic scroll tail
    };
    window.addEventListener("pointerup", gestureEnd);
    window.addEventListener("pointercancel", gestureEnd);
    const apply = (r: IndexRange) => {
      applying = true;
      try {
        for (const p of all) p.chart.timeScale().setVisibleLogicalRange({ from: r.from - EDGE, to: r.to + EDGE });
      } finally {
        applying = false;
      }
      setLayoutTick((n) => n + 1);
      requestAnimationFrame(() => requestAnimationFrame(() => setLayoutTick((n) => n + 1))); // re-measure once lightweight-charts has settled
    };
    /** Record a range; in replay, note whether it is pinned to the playhead (so it follows). */
    const commit = (r: IndexRange) => {
      const vs = viewState.current;
      vs.view = r;
      if (live.current.isReplay) {
        const lim = limits();
        const pinned = r.to >= lim.rightLimit - 0.6;
        vs.follow = pinned;
        vs.slideWidth = pinned && r.from > 0.6 ? r.to - r.from : null;
      }
    };
    /** Put the view where it belongs now (default, following the playhead, or the user's). */
    const refit = () => {
      const vs = viewState.current;
      const lim = limits();
      let target: IndexRange;
      if (vs.view === null) target = defaultRange(lim);
      else if (live.current.isReplay && vs.follow) {
        target = vs.slideWidth !== null ? { from: lim.rightLimit - vs.slideWidth, to: lim.rightLimit } : { from: 0, to: lim.rightLimit };
      } else target = vs.view;
      target = clampRange(target, lim);
      const wasFollowing = vs.follow;
      const wasSlide = vs.slideWidth;
      apply(target);
      vs.view = target;
      if (live.current.isReplay && wasFollowing) {
        vs.follow = true;
        vs.slideWidth = wasSlide;
      } else commit(target);
    };
    const zoomBy = (factor: number, anchor?: number) => {
      const r = readRange(bottomPane.chart.timeScale().getVisibleLogicalRange());
      if (!r) return;
      const lim = limits();
      const at = anchor ?? (live.current.isReplay ? r.to : (r.from + r.to) / 2);
      const next = clampRange(zoomRange(r, at, factor, lim.rightLimit, lim.minBars), lim);
      apply(next);
      commit(next);
    };
    controls.current = {
      zoom: (f) => zoomBy(f),
      reset: () => {
        viewState.current = { view: null, follow: true, slideWidth: null };
        refit();
      },
    };
    refitRef.current = refit;

    // Pan (drag) and any other range change: clamp it, then mirror it to every pane.
    for (const src of all) {
      src.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        setLayoutTick((n) => n + 1);
        requestAnimationFrame(() => setLayoutTick((n) => n + 1)); // the range is only settled a frame later
        // Only a drag (or its kinetic tail) is user input here. lightweight-charts also moves the
        // range by itself when data changes; that is ignored (refit puts the view back).
        if (applying || !range || !gesture.active) return;
        const c = clampRange(readRange(range)!, limits());
        commit(c);
        const off = all.some((p) => {
          const r = readRange(p.chart.timeScale().getVisibleLogicalRange());
          return !r || Math.abs(r.from - c.from) > 0.01 || Math.abs(r.to - c.to) > 0.01;
        });
        if (off) apply(c);
      });
    }

    const wheelCleanups: (() => void)[] = [];
    const attach = (el: HTMLDivElement | null, rt: PaneRuntime | null) => {
      if (!el || !rt) return;
      const onWheel = (e: WheelEvent) => {
        if (!isZoomWheel(e)) return; // plain wheel: the page scrolls
        e.preventDefault();
        const ts = rt.chart.timeScale();
        const r = readRange(ts.getVisibleLogicalRange());
        if (!r) return;
        const x = e.clientX - el.getBoundingClientRect().left;
        const anchor = ts.coordinateToLogical(x) ?? (r.from + r.to) / 2;
        zoomBy(e.deltaY > 0 ? WHEEL_ZOOM : 1 / WHEEL_ZOOM, anchor);
      };
      const onDbl = () => controls.current?.reset();
      el.addEventListener("pointerdown", gestureStart, true);
      el.addEventListener("wheel", onWheel, { passive: false });
      el.addEventListener("dblclick", onDbl);
      wheelCleanups.push(() => {
        el.removeEventListener("pointerdown", gestureStart, true);
        el.removeEventListener("wheel", onWheel);
        el.removeEventListener("dblclick", onDbl);
      });
    };
    attach(topEl.current, topPane);
    attach(bottomNode, bottomPane);

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
      refit();
    };
    resize();
    const ro = new ResizeObserver(resize);
    if (topEl.current) ro.observe(topEl.current);
    ro.observe(bottomNode);

    return () => {
      wheelCleanups.forEach((fn) => fn());
      window.removeEventListener("pointerup", gestureEnd);
      window.removeEventListener("pointercancel", gestureEnd);
      clearTimeout(gesture.timer);
      controls.current = null;
      refitRef.current = null;
      viewState.current = { view: null, follow: true, slideWidth: null };
      ro.disconnect();
      for (const p of all) p.chart.remove();
      panes.current = undefined;
      stopLine.current = null;
      avgLine.current = null;
    };
  }, [hasTop]);
  const refitRef = useRef<(() => void) | null>(null);

  // ---- data: aligned on the shared grid, revealed up to the playhead ---------
  // A new window resets the view; replay keeps the user's pan/zoom while the window grows.
  const windowKey = isReplay ? `${startSec}:${hasTop}` : `${startSec}:${endSec}:${hasTop}`;
  const lastWindowKey = useRef<string | null>(null);
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const feed = (rt: PaneRuntime, spec: PaneSpec) => {
      const aligned = alignedCandles(timeline, spec.candles, playheadSec);
      rt.series.setData(!isReplay ? greyAfterExit(aligned, reviewExit) : aligned);
      const line = spec.vwap
        ? timeline
            .filter((t) => t <= playheadSec && spec.vwap!.has(t))
            .map((t) => ({ time: t, value: spec.vwap!.get(t) as number }))
        : [];
      rt.vwap.setData(line);
      rt.emas.forEach((s, i) => {
        const e = spec.emas?.[i];
        s.setData(
          e
            ? timeline
                .filter((t) => t <= playheadSec && e.values.has(t))
                .map((t) => ({ time: t, value: e.values.get(t) as number }))
            : [],
        );
      });
      // Level lines: thin dashed, no axis tag (labels are drawn inside the pane).
      for (const l of rt.levelLines) rt.series.removePriceLine(l);
      rt.levelLines = (spec.levels ?? []).map((lv) =>
        rt.series.createPriceLine({
          price: lv.price,
          color: LEVEL_COLOR,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "",
        }),
      );
      for (const m of spec.marks ?? [])
        rt.levelLines.push(
          rt.series.createPriceLine({
            price: m.price,
            color: m.color,
            lineWidth: 2,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: m.label,
          }),
        );
    };
    if (lastWindowKey.current !== windowKey) {
      lastWindowKey.current = windowKey;
      viewState.current = { view: null, follow: true, slideWidth: null };
    }
    if (p.top && top) feed(p.top, top);
    feed(p.bottom, bottom);
    refitRef.current?.();
    setLayoutTick((n) => n + 1);
    // lightweight-charts folds new data into its time scale a frame later, so a range set
    // right after setData can land against the old points; set it again once it has settled.
    const id = requestAnimationFrame(() => refitRef.current?.());
    return () => cancelAnimationFrame(id);
  }, [timeline, top, bottom, playheadSec, hasTop, isReplay, reviewExit, windowKey]);

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
    rt.chart.priceScale("right").applyOptions({ autoScale: true }); // re-run the autoscale with the new stop
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
    rt.chart.priceScale("right").applyOptions({ autoScale: true });
  }, [avgPrice, hasTop]);

  // ---- fill markers: short arrows only (details go to the hover readout) -----
  // Real fills mark the fill minute on BOTH panes (arrow at that bar, no option price on the
  // underlying axis). Sim fills mark the contract pane. Nothing after the playhead.
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const realMarkers = (color: (f: ReplayFill) => string): SeriesMarker<UTCTimestamp>[] =>
      fills.flatMap((f) => {
        const time = floorMinute(isoToUtcSeconds(f.ts));
        if (time > playheadSec) return []; // second guard; replay gates fills server-side
        const isBuy = f.side === "buy";
        return [
          {
            time: time as UTCTimestamp,
            position: isBuy ? ("belowBar" as const) : ("aboveBar" as const),
            color: color(f),
            shape: isBuy ? ("arrowUp" as const) : ("arrowDown" as const),
            size: 1,
          },
        ];
      });
    const side = (f: ReplayFill) => (f.side === "buy" ? THEME.up : THEME.down);
    const sim: SeriesMarker<UTCTimestamp>[] = simFills.flatMap((f) => {
      const time = floorMinute(isoToUtcSeconds(f.ts));
      if (time > playheadSec) return [];
      const isBuy = f.side === "buy";
      return [
        {
          time: time as UTCTimestamp,
          position: isBuy ? ("belowBar" as const) : ("aboveBar" as const),
          color: THEME.play,
          shape: isBuy ? ("arrowUp" as const) : ("arrowDown" as const),
          size: 1,
        },
      ];
    });
    const byTime = (a: SeriesMarker<UTCTimestamp>, b: SeriesMarker<UTCTimestamp>) =>
      (a.time as number) - (b.time as number);
    p.top?.series.setMarkers([...realMarkers(side), ...sim].sort(byTime));
    p.bottom.series.setMarkers(realMarkers(side).sort(byTime));
  }, [fills, simFills, playheadSec, hasTop, timeline]);

  // ---- overlay geometry (px) for the shaded future / hold / chips ------------
  const chipSources = useMemo(
    () =>
      fills
        .map((f) => ({ sec: floorMinute(isoToUtcSeconds(f.ts)), exact: isoToUtcSeconds(f.ts), side: f.side }))
        .filter((f) => f.sec <= playheadSec)
        .map((f) => ({
          sec: f.sec,
          side: f.side,
          text: fillChipText(f.side, playheadLabel ? ptClockSec(f.exact) : ptClock(f.sec)),
        })),
    [fills, playheadSec, playheadLabel],
  );
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const measure = (rt: PaneRuntime | null, spec: PaneSpec | null): Layout => {
      if (!rt) return EMPTY_LAYOUT;
      const ts = rt.chart.timeScale();
      const x = (sec: number | null) =>
        sec === null ? null : (ts.timeToCoordinate(sec as UTCTimestamp) as number | null);
      const width = p.bottom.chart.timeScale().width(); // both panes share one plot width
      const h = (rt === p.top ? topEl.current : bottomEl.current)?.clientHeight ?? 0;
      const vr = readRange(ts.getVisibleLogicalRange());
      return {
        yMin: rt.series.coordinateToPrice(h) as number | null,
        yMax: rt.series.coordinateToPrice(0) as number | null,
        from: vr ? vr.from : null,
        to: vr ? vr.to : null,
        width,
        height: (rt === p.top ? topEl.current : bottomEl.current)?.clientHeight ?? 0,
        levelYs: (spec?.levels ?? []).flatMap((lv) => {
          const y = rt.series.priceToCoordinate(lv.price) as number | null;
          return y === null ? [] : [{ label: lv.label, price: lv.price, y }];
        }),
        playX: x(playheadSec),
        entryX: x(entrySec),
        exitX: x(exitSec),
        stopY:
          rt === p.top && stopPrice != null
            ? (rt.series.priceToCoordinate(stopPrice) as number | null)
            : null,
        chips: chipSources.flatMap((c) => {
          const cx = x(c.sec);
          return cx === null || cx < -4 || cx > width + 4 ? [] : [{ x: cx, text: c.text, side: c.side }];
        }),
      };
    };
    setLayouts({ top: measure(p.top, top), bottom: measure(p.bottom, bottom) });
  }, [layoutTick, playheadSec, entrySec, exitSec, stopPrice, hasTop, top, bottom, chipSources]);

  // ---- dragging the stop ---------------------------------------------------------
  const priceAtPointer = (e: React.PointerEvent): number | null => {
    const rt = panes.current?.top;
    const el = topEl.current;
    if (!rt || !el) return null;
    const y = e.clientY - el.getBoundingClientRect().top;
    const raw = rt.series.coordinateToPrice(y);
    if (raw == null) return null;
    // Stay inside the frozen domain so a drag never rescales the pane.
    const d = frozen.current;
    const clamped = d ? Math.min(d.maxValue, Math.max(d.minValue, raw)) : raw;
    const price = Math.round(clamped * 100) / 100;
    return price > 0 ? price : null;
  };
  const endDrag = () => {
    dragging.current = null;
    frozen.current = null;
    panes.current?.top?.chart.priceScale("right").applyOptions({ autoScale: true });
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
          frozen.current = lastDomain.current.top;
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
          endDrag();
          onStopChange(price, true);
        }}
        onPointerCancel={endDrag}
      />
    ) : null;

  // Replay reads out the playhead bar; the review reads out only what is hovered.
  const shown = hoverSec ?? (isReplay ? playheadSec : null);

  // Fill details for the hovered minute (the markers themselves stay short).
  const detailFor = (which: "top" | "bottom", minute: number | null): string[] => {
    if (minute === null) return [];
    const out: string[] = [];
    for (const f of fills) {
      const t = floorMinute(isoToUtcSeconds(f.ts));
      if (t !== minute || t > playheadSec) continue;
      out.push(
        which === "top"
          ? `${f.side === "buy" ? "B" : "S"} ${f.qty} @ ${f.price.toFixed(2)} ${ptClock(t)}`
          : fillChipText(f.side, ptClock(t)),
      );
    }
    if (which === "top") {
      for (const f of simFills) {
        const t = floorMinute(isoToUtcSeconds(f.ts));
        if (t !== minute || t > playheadSec) continue;
        out.push(`sim ${f.side === "buy" ? "B" : "S"} ${f.qty} @ ${f.price.toFixed(2)}`);
      }
    }
    return out;
  };

  const noDataFor = (spec: PaneSpec): string | null => {
    if (isReplay && playheadLabel) return null; // Seconds mode: a quiet minute is not missing data
    const last = spec.candles.length ? floorMinute(isoToUtcSeconds(spec.candles[spec.candles.length - 1].ts)) : null;
    let at: number | null = null;
    if (isReplay) {
      if (last !== null && playheadSec > last) at = last; // only once the playhead has passed it
    } else if (last !== null && last < endSec) at = last;
    else if (noDataAfterSec !== null && (last === null || last <= noDataAfterSec)) at = noDataAfterSec;
    return at === null ? null : `No data after ${ptClock(at)}`;
  };

  const controlsNode = (
    <div className="pointer-events-auto ml-auto flex items-center gap-1.5" data-testid="chart-controls">
      <span className="hidden text-label text-muted-foreground xl:inline">
        Drag to pan · Ctrl or Cmd + scroll to zoom · double-click to reset
      </span>
      {stopPriceProp != null && (
        <button
          type="button"
          aria-pressed={showStop}
          onClick={toggleStop}
          title={showStop ? "Hide the stop line" : "Show the stop line"}
          className={
            "flex items-center gap-1 rounded-md border px-2 py-0.5 text-label transition-colors " +
            (showStop ? "border-stop/60 text-foreground" : "border-border text-muted-foreground hover:bg-muted")
          }
        >
          <span className="inline-block h-0 w-3 border-t-2 border-dashed border-stop" />
          {stopAssumed ? "Stop (assumed)" : "Stop"}
        </button>
      )}
      <button
        type="button"
        aria-label="Zoom out"
        title="Zoom out"
        onClick={() => controls.current?.zoom(BUTTON_ZOOM)}
        className="rounded-md border border-border p-1 text-muted-foreground transition-colors hover:bg-muted"
      >
        <Minus className="h-4 w-4" />
      </button>
      <button
        type="button"
        aria-label="Zoom in"
        title="Zoom in"
        onClick={() => controls.current?.zoom(1 / BUTTON_ZOOM)}
        className="rounded-md border border-border p-1 text-muted-foreground transition-colors hover:bg-muted"
      >
        <Plus className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => controls.current?.reset()}
        className="rounded-md border border-border px-2 py-0.5 text-label text-muted-foreground transition-colors hover:bg-muted"
      >
        {isReplay ? "Reset" : "Fit trade"}
      </button>
    </div>
  );

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      {top && (
        <Pane
          className="flex-[6]"
          spec={top}
          layout={layouts.top}
          shown={shown}
          playheadSec={playheadSec}
          isReplay={isReplay}
          hovered={hoverSec !== null}
          playheadLabel={playheadLabel}
          elRef={topEl}
          showTicks
          overlay={stopHandle}
          details={detailFor("top", shown)}
          noData={noDataFor(top)}
          controls={controlsNode}
        />
      )}
      <Pane
        className={top ? "flex-[4] border-t border-border" : "flex-1"}
        spec={bottom}
        layout={layouts.bottom}
        shown={shown}
        playheadSec={playheadSec}
        isReplay={isReplay}
        hovered={hoverSec !== null}
        playheadLabel={playheadLabel}
        elRef={bottomEl}
        showTicks={!top}
        details={detailFor("bottom", shown)}
        noData={noDataFor(bottom)}
        controls={top ? undefined : controlsNode}
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
  isReplay,
  hovered,
  playheadLabel,
  elRef,
  showTicks,
  overlay,
  details,
  noData,
  controls,
}: {
  className: string;
  spec: PaneSpec;
  layout: Layout;
  shown: number | null;
  playheadSec: number;
  isReplay: boolean;
  hovered: boolean;
  playheadLabel?: string;
  elRef: React.RefObject<HTMLDivElement>;
  showTicks: boolean;
  overlay?: React.ReactNode;
  details: string[];
  noData: string | null;
  controls?: React.ReactNode;
}) {
  const bar = shown === null ? null : candleAt(spec.candles, shown, playheadSec);
  const { width, playX, entryX, exitX } = layout;
  const windowEnd = exitX ?? playX;
  const placed = useMemo(
    () => placeChips(layout.chips.map((c) => ({ x: c.x, w: chipWidth(c.text) })), width),
    [layout.chips, width],
  );
  const lanes = Math.max(1, ...placed.map((p) => p.lane + 1));
  return (
    <div
      className={`flex min-h-0 flex-col ${className}`}
      data-ymin={layout.yMin?.toFixed(3)}
      data-ymax={layout.yMax?.toFixed(3)}
      data-from={layout.from?.toFixed(2)}
      data-to={layout.to?.toFixed(2)}
    >
      {/* Header + OHLC readout for the hovered (or playhead) minute. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1">
        <span className="label-caps text-label font-semibold">{spec.label}</span>
        {spec.badge}
        <span className="num text-label text-muted-foreground">
          {shown === null
            ? "hover a bar for OHLC"
            : !hovered && isReplay && playheadLabel
              ? playheadLabel
              : ptClock(shown)}
          {hovered || !isReplay ? "" : " · playhead"}
        </span>
        {shown === null ? null : bar ? (
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
        {details.map((d) => (
          <span key={d} className="num rounded border border-border px-1.5 text-label text-foreground">
            {d}
          </span>
        ))}
        {controls}
      </div>
      {/* Fill time chips, above the candles so they never cover a bar. */}
      <div className="relative shrink-0 overflow-hidden" style={{ height: lanes * CHIP_LANE_PX }}>
        <div className="pointer-events-none absolute inset-y-0 left-0" style={{ width }}>
          {layout.chips.map((c, i) => (
            <span
              key={`${c.text}:${i}`}
              className={`num absolute whitespace-nowrap rounded border bg-card px-1 text-label ${
                c.side === "buy" ? "border-gain/60 text-gain" : "border-loss/60 text-loss"
              }`}
              style={{ left: placed[i]?.left ?? 0, top: (placed[i]?.lane ?? 0) * CHIP_LANE_PX + 1 }}
            >
              {c.text}
            </span>
          ))}
        </div>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={elRef} className="absolute inset-0" />
        {/* Overlays share the plot area's width (the price scale sits to the right). */}
        <div className="pointer-events-none absolute inset-y-0 left-0 overflow-hidden" style={{ width }}>
          {entryX !== null && windowEnd !== null && (
            <div
              className="absolute inset-y-0 bg-play/10"
              style={{ left: entryX, width: Math.max(0, windowEnd - entryX) }}
            />
          )}
          {isReplay && playX !== null && (
            <div
              className="absolute inset-y-0 bg-grey/15"
              style={{ left: playX, width: Math.max(0, width - playX) }}
            />
          )}
          {!isReplay && exitX !== null && (
            <div
              className="absolute inset-y-0 bg-grey/10"
              style={{ left: exitX, width: Math.max(0, width - exitX) }}
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
          {isReplay && playX !== null && <div className="absolute inset-y-0 w-px bg-play" style={{ left: playX }} />}
          {spreadLabels(layout.levelYs, LEVEL_LABEL_GAP, layout.height).map((lv) => (
            <span
              key={lv.label}
              className="num absolute right-1 -translate-y-1/2 rounded bg-card/80 px-1 text-label text-grey"
              style={{ top: lv.labelY }}
            >
              {lv.label} {lv.price.toFixed(2)}
            </span>
          ))}
          {noData && (
            <span className="num absolute right-1 top-1 rounded bg-card/90 px-1 text-label text-muted-foreground">
              {noData}
            </span>
          )}
          {spec.emas && spec.emas.length > 0 && (
            <span className="num absolute bottom-1 right-1 rounded bg-card/80 px-1 text-label text-grey">
              {spec.emas.map((e) => e.label).join(" ")}
            </span>
          )}
          {overlay}
        </div>
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
