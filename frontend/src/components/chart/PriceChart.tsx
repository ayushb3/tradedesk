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
import { useQuery } from "@tanstack/react-query";
import { ChevronsRight, HelpCircle, Lock, LockOpen, Minus, Plus, Search } from "lucide-react";

import { api, type Candle, type ReplayFill } from "@/lib/api";
import type { SimFill } from "@/lib/simPosition";
import { isoToUtcSeconds, makeDisplayFormatters } from "@/lib/chart";
import { DISPLAY_TZ, DISPLAY_TZ_LABEL } from "@/lib/format";
import {
  chipWidth,
  clampRange,
  formatChange,
  legendBar,
  timeframeLabel,
  defaultRange,
  fillChipText,
  fillDetailText,
  indexOfSec,
  minVisibleBars,
  paddedRange,
  placeChips,
  rightLimit,
  RIGHT_MARGIN_BARS,
  followShift,
  latestInView,
  valueAtOrBefore,
  quoteAtSec,
  formatBidAsk,
  type IndexRange,
  type PriceDomain,
} from "@/lib/priceChart";
import {
  alignedCandles,
  buildTimeline,
  revealedFillTime,
  spreadLabels,
} from "@/lib/stackedReplay";
import { LEVEL_SLATE, lineLook, type LineStyleName } from "@/lib/chartColors";
import { CHART_THEME } from "@/lib/chartTheme";
import { FillTag, LinePill, PositionPill } from "@/components/chart/ChartPills";
import { formatPnl } from "@/lib/positionAt";
import { greyAfterExit, isZoomWheel, zoomRange } from "@/lib/reviewChart";
import { bucketOf, INTERVALS, RANGES, rollUp, rollUpLine } from "@/lib/chartInterval";

const THEME = CHART_THEME;
const PRICE_SCALE_WIDTH = 72;
const CHIP_LANE_PX = 22;
const WHEEL_ZOOM = 1.2;
/** lightweight-charts' logical range reaches a bar's centre; show half a bar past each end so edge bars are whole. */
const EDGE = 0.5;
const readRange = (r: { from: number; to: number } | null): IndexRange | null =>
  r ? { from: r.from + EDGE, to: r.to - EDGE } : null;
const BUTTON_ZOOM = 1.5;
/** Level lines default to neutral slate; indicators never use the meaning hues. */
const LEVEL_COLOR = LEVEL_SLATE;
/** Line series kept per pane for EMAs/SMAs (each restyled from its spec on every feed). */
const MAX_LINES = 6;
const LINE_STYLE: Record<LineStyleName, LineStyle> = {
  solid: LineStyle.Solid,
  dashed: LineStyle.Dashed,
  dotted: LineStyle.Dotted,
  dots: LineStyle.SparseDotted,
};
const LEVEL_LABEL_GAP = 16;
/** How to use the chart, in the "?" tooltip (kept out of the header so the legend never clips). */
const HELP_TEXT = "Drag to pan · Ctrl or Cmd + scroll to zoom · double-click to reset";
const HELP_TEXT_TV = "Drag to pan · scroll to zoom · drag a line to move it · double-click to reset";

export interface PaneSpec {
  /** Header, e.g. "SPY, proxy for SPX". */
  label: string;
  /** Bars inside the window (lead start to window end). */
  candles: Candle[];
  /** Optional causal VWAP keyed by minute (epoch seconds). */
  vwap?: Map<number, number>;
  /** Causal level values at the playhead, drawn as thin lines with in-pane labels. Slate dashed unless styled. */
  levels?: { label: string; price: number; color?: string; style?: LineStyleName }[];
  /** Tag levels on the price axis (TradingView-style) instead of in-pane labels over the bars. */
  levelsOnAxis?: boolean;
  /** Optional moving-average lines (look from INDICATOR_LOOK by label, unless `color`/`style`/`width` say otherwise), each keyed by minute. */
  emas?: { label: string; values: Map<number, number>; color?: string; style?: LineStyleName; width?: number }[];
  /** Show this stock's bid/ask in the legend (historical quotes at the legend bar's last second). */
  quoteSymbol?: string;
  /** Labelled solid price lines the page draws itself (Learn drills: your level, stop, target). */
  marks?: { label: string; price: number; color: string; key?: string }[];
  /** Extra markers on this pane, at bar opens (free replay's practice trades); those after the playhead are not drawn. */
  markers?: { sec: number; side: "buy" | "sell" | "exit"; text?: string }[];
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
  /**
   * Open positions drawn on the contract pane: a dotted line at each blended average with a
   * pill showing the qty and live P&L at the playhead. The caller computes them from fills at
   * or before the playhead only.
   */
  positions?: ChartPosition[];
  /** The practice profit target (green); draggable when `onTargetChange` is given. */
  targetPrice?: number | null;
  /** Extra buttons in the contract pane's header (replay: Buy to open / Sell to open). */
  topActions?: React.ReactNode;
  /** Called while dragging the target (final = false), on release (true); null clears it. */
  onTargetChange?: (price: number | null, final: boolean) => void;
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
  /** Underlying-pane marks with a `key` get a drag handle; called while dragging (final = false) and on release. */
  onMarkDrag?: (key: string, price: number, final: boolean) => void;
  /**
   * TradingView-style interaction (Learn drills): a plain wheel zooms at the cursor, a sideways
   * wheel pans, and the crosshair shows both lines with a price label. Off: a plain wheel scrolls
   * the page. Read once when the chart is created.
   */
  tradingView?: boolean;
  /** Replay: the default view is this many bars wide and slides with the playhead (null: grow from the lead start). */
  initialBars?: number | null;
  /**
   * Which pane gets most of the height when both are shown. "top" (default): the contract pane
   * 60 / underlying 40. "bottom": underlying 70 / contract 30 (Replay: you trade the ticker).
   */
  mainPane?: "top" | "bottom";
  /**
   * Draw the contract (`top`) pane below the underlying instead of above it (Replay: the ticker
   * is what you trade, so it leads). The time axis moves to the contract pane, which is then lowest.
   */
  contractBelow?: boolean;
  /**
   * How real fills are labelled. "chips" (default): a time chip per fill above the candles.
   * "arrows": the arrows only; a fill's side, qty, price, second and order type show in a small
   * box over the plot while its minute is hovered (or under the playhead), one line per fill.
   */
  fillLabels?: "chips" | "arrows";
  /**
   * Bar size, seconds (default 60). The grid steps by this much; candles, `startSec`, `endSec`
   * and `playheadSec` must sit on bar opens (Learn aggregates 1m bars on the client).
   */
  barSec?: number;
  /**
   * Where the view opens inside a longer timeline: `startSec`..`endSec` can hold the whole
   * session (so the user can zoom out to it) while the chart opens on the trade window.
   */
  defaultView?: { fromSec: number; toSec?: number | null };
  /** Replay: the last second played (seconds precision), the latest time a bid/ask may come from. */
  revealSec?: number;
  /** The bottom bar (ranges, interval, underlying/option, auto-scale). Default on. */
  footer?: boolean;
  /**
   * A chart that loads its own bar size (free replay) drives the Interval buttons itself:
   * the buttons in `options` are enabled and pick that size; nothing is rolled up here.
   */
  interval?: { value: string; options: readonly string[]; onChange: (label: string) => void };
}

export interface ChartPosition {
  key: string;
  /** "real" draws grey; "sim" (practice) draws blue. */
  kind: "real" | "sim";
  qty: number;
  avg: number;
  /** Open P&L in dollars at the playhead; null when there is no mark. */
  pnl: number | null;
  /** 1R per share, fixed at the first buy (practice only): the "+ Target" button sets 2R off it. */
  riskPerShare?: number | null;
}

interface Layout {
  width: number;
  playX: number | null;
  entryX: number | null;
  exitX: number | null;
  stopY: number | null;
  targetY: number | null;
  /** Contract pane: the y of each position's average line. */
  posYs: { key: string; y: number }[];
  /** Contract pane, replay: one tag per fill drawn so far, at its bar and price. */
  fillTags: { key: string; x: number; y: number; side: "buy" | "sell"; sim: boolean; text: string; details: string[] }[];
  height: number;
  levelYs: { label: string; price: number; y: number; color?: string }[];
  markYs: { key: string; label: string; y: number }[];
  /** Session high and low (of the bars drawn so far), tagged "H:" / "L:" at their candles. */
  chips: { x: number; text: string; side: "buy" | "sell" }[];
  /** Price at the bottom/top edge of the pane and the visible bar indices (also exposed as data-* for checks). */
  yMin: number | null;
  yMax: number | null;
  from: number | null;
  to: number | null;
}

/** Pills stay below the legend rows at the top of the pane. */
const PILL_TOP_PX = 46;

const EMPTY_LAYOUT: Layout = {
  width: 0,
  playX: null,
  entryX: null,
  exitX: null,
  stopY: null,
  targetY: null,
  posYs: [],
  fillTags: [],
  height: 0,
  levelYs: [],
  markYs: [],
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

function PriceChartCore({
  top,
  bottom,
  startSec,
  endSec,
  playheadSec,
  mode,
  stopPrice: stopPriceProp,
  stopAssumed = false,
  positions = [],
  targetPrice = null,
  onTargetChange,
  topActions,
  simFills = [],
  onStopChange,
  fills,
  noDataAfterSec = null,
  playheadLabel,
  onPriceClick,
  onMarkDrag,
  tradingView = false,
  initialBars = null,
  barSec = 60,
  mainPane = "top",
  contractBelow = false,
  fillLabels = "chips",
  defaultView,
  revealSec,
  autoScale = true,
}: Props & { autoScale?: boolean }) {
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
  const avgLines = useRef<IPriceLine[]>([]);
  const targetLine = useRef<IPriceLine | null>(null);
  /** The plan line being dragged and its latest price. */
  const dragging = useRef<{ kind: "stop" | "target"; price: number; moved: boolean; grabDy: number } | null>(null);
  /** Last autoscaled domain per pane; frozen while the stop is dragged so the pane cannot run away. */
  const lastDomain = useRef<{ top: PriceDomain | null; bottom: PriceDomain | null }>({ top: null, bottom: null });
  const frozen = useRef<PriceDomain | null>(null);
  const viewState = useRef<ViewState>({ view: null, follow: true, slideWidth: null });
  const controls = useRef<{ zoom: (factor: number) => void; reset: () => void; jumpLatest: () => void } | null>(null);

  const [hoverSec, setHoverSec] = useState<number | null>(null);
  const [layoutTick, setLayoutTick] = useState(0);
  const [layouts, setLayouts] = useState<{ top: Layout; bottom: Layout }>({
    top: EMPTY_LAYOUT,
    bottom: EMPTY_LAYOUT,
  });

  const timeline = useMemo(() => buildTimeline(startSec, endSec, barSec), [startSec, endSec, barSec]);
  /** A time's bar open on this chart's grid (the minute on 1m bars; the 5m bar on 5m). */
  const onBar = (t: number) => startSec + Math.floor((t - startSec) / barSec) * barSec;
  const hasTop = top !== null;
  const lastIdx = timeline.length - 1;
  const playIdx = indexOfSec(timeline[0] ?? startSec, playheadSec, Math.max(0, lastIdx), barSec);
  const viewFromIdx = defaultView ? indexOfSec(timeline[0] ?? startSec, defaultView.fromSec, Math.max(0, lastIdx), barSec) : 0;
  const viewToIdx =
    defaultView?.toSec != null ? indexOfSec(timeline[0] ?? startSec, defaultView.toSec, Math.max(0, lastIdx), barSec) : null;
  // Replay: is the view following the latest candle? (false shows "Jump to latest").
  const [following, setFollowing] = useState(true);
  const followingRef = useRef(true);
  const noteFollow = (f: boolean) => {
    if (followingRef.current === f) return;
    followingRef.current = f;
    setFollowing(f);
  };
  // Replay crosshair: "latest" pins it (and the legend) to the latest candle; "mouse" follows
  // the pointer. Stepping or playing switches to "latest"; moving the mouse over a pane, to "mouse".
  const pointerMode = useRef<"mouse" | "latest">("latest");
  const crossProg = useRef(false);

  // Everything the imperative handlers need, refreshed every render.
  const live = useRef({
    isReplay,
    playIdx,
    lastIdx,
    extras: [] as (number | null)[],
    onPriceClick: undefined as ((price: number) => void) | undefined,
    tradingView: false,
    initialBars: null as number | null,
    viewFrom: 0,
    viewTo: null as number | null,
    noteFollow: (() => {}) as (f: boolean) => void,
  });
  live.current = {
    isReplay,
    playIdx,
    lastIdx: Math.max(0, lastIdx),
    extras: [stopPrice, targetPrice, ...positions.map((ps) => ps.avg)],
    onPriceClick,
    tradingView,
    initialBars,
    viewFrom: viewFromIdx,
    viewTo: viewToIdx,
    noteFollow,
  };

  // Snapped to the bar grid, so the hold shading lines up on 5m/15m bars too. A fill anywhere in
  // the playhead's bar counts (callers already gate fills at the real playhead).
  const snapFill = (t: number | null) => (t === null ? null : onBar(t));
  const entrySec = snapFill(revealedFillTime(fills, "entry", playheadSec + barSec - 1));
  const exitSec = snapFill(revealedFillTime(fills, "exit", playheadSec + barSec - 1));

  // ---- create the charts once per layout (stacked / underlying only) ---------
  useEffect(() => {
    const bottomNode = bottomEl.current;
    if (!bottomNode) return;
    const fmt = makeDisplayFormatters();

    const make = (el: HTMLDivElement, which: "top" | "bottom", showAxis: boolean): PaneRuntime => {
      const chart = createChart(el, {
        layout: {
          background: { type: ColorType.Solid, color: "transparent" }, // the container paints THEME.bg, so the overlays under the canvas show
          textColor: THEME.text,
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        },
        // Almost no grid: faint horizontal lines only.
        grid: {
          vertLines: { visible: false },
          horzLines: { color: THEME.grid },
        },
        // A free crosshair that follows the mouse, with its price tagged on the right scale and
        // its time on the time axis (the way a live trading chart reads).
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: THEME.crosshair, width: 1, labelBackgroundColor: THEME.crosshairLabel, style: LineStyle.Dashed },
          horzLine: { color: THEME.crosshair, width: 1, labelBackgroundColor: THEME.crosshairLabel, style: LineStyle.Dashed },
        },
        // Padding comes from our own autoscale; no extra margins (premium must not dip below 0).
        rightPriceScale: {
          borderColor: THEME.border,
          minimumWidth: PRICE_SCALE_WIDTH,
          scaleMargins: { top: 0, bottom: 0 },
        },
        localization: { timeFormatter: fmt.timeFormatter },
        timeScale: {
          borderColor: THEME.border,
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
        // The last drawn close, tagged on the price scale in the bar's color.
        lastValueVisible: true,
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
      // Every line gets a colored tag on the price scale at its last drawn value.
      const vwap = chart.addLineSeries({
        color: THEME.vwap,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null, // never part of the scale
      });
      const emas = Array.from({ length: MAX_LINES }, (_, i) =>
        chart.addLineSeries({
          color: lineLook("ema", i).color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null,
        }),
      );
      return { chart, series, vwap, emas, levelLines: [] };
    };

    const topPane = topEl.current ? make(topEl.current, "top", contractBelow) : null;
    const bottomPane = make(bottomNode, "bottom", !(contractBelow && topEl.current));
    panes.current = { top: topPane, bottom: bottomPane };
    const all = [topPane, bottomPane].filter((p): p is PaneRuntime => p !== null);

    // Sync the crosshair across panes and report the hovered minute.
    let syncing = false;
    for (const src of all) {
      src.chart.subscribeCrosshairMove((param) => {
        if (syncing || crossProg.current) return;
        // Replay, pinned to the latest candle: ignore the chart's own pointer tracking.
        if (live.current.isReplay && pointerMode.current === "latest") return;
        syncing = true;
        try {
          const t = typeof param.time === "number" ? (param.time as number) : null;
          setHoverSec(t);
          for (const dst of all) {
            if (dst === src) continue;
            if (t === null) {
              dst.chart.clearCrosshairPosition();
              continue;
            }
            // The other pane follows the time; its price line sits on that pane's own close.
            const row = dst.series.data().find((d) => d.time === t);
            const close = row && "close" in row ? (row.close as number) : null;
            if (close === null) dst.chart.clearCrosshairPosition();
            else dst.chart.setCrosshairPosition(close, t as UTCTimestamp, dst.series);
          }
        } finally {
          syncing = false;
        }
      });
    }

    // A click on the underlying pane reports its price (lightweight-charts does not fire it after a drag).
    // A click on the underlying pane reports its price. Our own handler, not subscribeClick:
    // lightweight-charts swallows a quick second click as a double-click. A click is held
    // 250 ms; a second click on the same spot is a double-click (view reset, no price), a
    // second click elsewhere is its own click. Drags and clicks on the price axis never count.
    const CLICK_HOLD_MS = 250;
    const SAME_SPOT_PX = 6;
    let downAt: { x: number; y: number } | null = null;
    let pending: { timer: ReturnType<typeof setTimeout>; x: number; y: number } | null = null;
    const firePrice = (y: number) => {
      const cb = live.current.onPriceClick;
      if (!cb) return;
      const raw = bottomPane.series.coordinateToPrice(y);
      if (raw == null) return;
      const price = Math.round(raw * 100) / 100;
      if (price > 0) cb(price);
    };
    const flush = () => {
      if (!pending) return;
      clearTimeout(pending.timer);
      const y = pending.y;
      pending = null;
      firePrice(y);
    };
    const onDown = (e: PointerEvent) => (downAt = { x: e.clientX, y: e.clientY });
    const onClick = (e: MouseEvent) => {
      if (!live.current.onPriceClick) return;
      if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 4) return; // a drag-pan
      const rect = bottomNode.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      if (x > bottomPane.chart.timeScale().width()) return; // the price axis
      if (pending) {
        const same = Math.hypot(pending.x - x, pending.y - y) < SAME_SPOT_PX;
        if (same) {
          clearTimeout(pending.timer);
          pending = null;
          return;
        }
        flush();
      }
      pending = { timer: setTimeout(flush, CLICK_HOLD_MS), x, y };
    };
    bottomNode.addEventListener("pointerdown", onDown);
    bottomNode.addEventListener("click", onClick);

    // ---- the visible range: one source of truth, clamped, synced ----------------
    const limits = () => {
      const minBars = minVisibleBars(bottomPane.chart.timeScale().width());
      const l = live.current;
      return {
        minBars,
        rightLimit: rightLimit({
          replay: l.isReplay,
          playIdx: l.playIdx,
          lastIdx: l.lastIdx,
          minBars,
          marginBars: l.isReplay ? RIGHT_MARGIN_BARS : 0,
        }),
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
    /**
     * Record a range. Replay: it follows the latest candle while that candle is in view (it
     * grows from the window start when the view starts there, else it slides at this width);
     * scrolled back into history it stays put and "Jump to latest" shows.
     */
    const commit = (r: IndexRange) => {
      const vs = viewState.current;
      vs.view = r;
      if (live.current.isReplay) {
        const inView = latestInView(r, live.current.playIdx);
        vs.follow = inView;
        vs.slideWidth = inView && Math.abs(r.from - live.current.viewFrom) > 0.6 ? r.to - r.from : null;
        live.current.noteFollow(inView);
      }
    };
    /** Put the view where it belongs now (default, following the playhead, or the user's). */
    const refit = () => {
      const vs = viewState.current;
      const lim = limits();
      const init = live.current.isReplay ? live.current.initialBars : null;
      if (vs.view === null && init) {
        // A fixed-width window on the playhead that follows it from the start.
        vs.view = { from: lim.rightLimit - init, to: lim.rightLimit };
        vs.follow = true;
        vs.slideWidth = init;
      }
      let target: IndexRange;
      const from0 = live.current.viewFrom;
      if (vs.view === null) target = defaultRange(lim, from0, live.current.isReplay ? null : live.current.viewTo);
      else if (live.current.isReplay && vs.follow) {
        // Slide only as far as needed to keep the latest candle in view, with the margin.
        target =
          vs.slideWidth !== null
            ? followShift(vs.view, live.current.playIdx, RIGHT_MARGIN_BARS)
            : { from: from0, to: lim.rightLimit };
      } else target = vs.view;
      target = clampRange(target, lim);
      const wasFollowing = vs.follow;
      const wasSlide = vs.slideWidth;
      apply(target);
      vs.view = target;
      if (live.current.isReplay && wasFollowing) {
        vs.follow = true;
        vs.slideWidth = wasSlide;
        live.current.noteFollow(true);
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
        live.current.noteFollow(true);
        refit();
      },
      jumpLatest: () => {
        const r = readRange(bottomPane.chart.timeScale().getVisibleLogicalRange()) ?? viewState.current.view;
        const lim = limits();
        const width = r ? r.to - r.from : lim.minBars;
        const next = clampRange({ from: lim.rightLimit - width, to: lim.rightLimit }, lim);
        apply(next);
        commit(next);
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
        if (!live.current.tradingView && !isZoomWheel(e)) return; // plain wheel: the page scrolls
        e.preventDefault();
        const ts = rt.chart.timeScale();
        const r = readRange(ts.getVisibleLogicalRange());
        if (!r) return;
        if (live.current.tradingView && Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
          // Sideways wheel (trackpad): pan by a share of the visible bars.
          const shift = (e.deltaX / Math.max(1, ts.width())) * (r.to - r.from);
          const next = clampRange({ from: r.from + shift, to: r.to + shift }, limits());
          apply(next);
          commit(next);
          return;
        }
        const x = e.clientX - el.getBoundingClientRect().left;
        // Replay, following the playhead: zoom about the right edge so the view stays pinned to it.
        const anchor =
          live.current.isReplay && viewState.current.follow ? r.to : (ts.coordinateToLogical(x) ?? (r.from + r.to) / 2);
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

    // Replay crosshair mode: a real mouse move over a pane hands the crosshair to the mouse
    // (capture phase, so it switches before lightweight-charts reports that same move);
    // leaving the panes pins it back on the latest candle.
    const paneEls = [topEl.current, bottomNode].filter((e): e is HTMLDivElement => e !== null);
    const onMove = () => {
      pointerMode.current = "mouse";
    };
    const onLeave = (e: PointerEvent) => {
      if (!live.current.isReplay) return;
      const to = e.relatedTarget as Node | null;
      if (to && paneEls.some((el) => el.contains(to))) return; // moved to the other pane
      pointerMode.current = "latest";
      setHoverSec(null);
      snapRef.current?.();
    };
    for (const el of paneEls) {
      el.addEventListener("pointermove", onMove, true);
      el.addEventListener("pointerleave", onLeave);
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
      refit();
      // Re-measure the overlays (plot width, playhead x) once the new size has settled.
      requestAnimationFrame(() => setLayoutTick((n) => n + 1));
    };
    resize();
    const ro = new ResizeObserver(resize);
    if (topEl.current) ro.observe(topEl.current);
    ro.observe(bottomNode);

    return () => {
      bottomNode.removeEventListener("pointerdown", onDown);
      bottomNode.removeEventListener("click", onClick);
      if (pending) clearTimeout(pending.timer);
      wheelCleanups.forEach((fn) => fn());
      for (const el of paneEls) {
        el.removeEventListener("pointermove", onMove, true);
        el.removeEventListener("pointerleave", onLeave);
      }
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
      avgLines.current = [];
      targetLine.current = null;
    };
  }, [hasTop, contractBelow]);
  const refitRef = useRef<(() => void) | null>(null);
  const snapRef = useRef<(() => void) | null>(null);

  // ---- data: aligned on the shared grid, revealed up to the playhead ---------
  // A new window resets the view; replay keeps the user's pan/zoom while the window grows.
  const viewKey = `${defaultView?.fromSec ?? ""}:${defaultView?.toSec ?? ""}`;
  const windowKey = isReplay
    ? `${startSec}:${hasTop}:${barSec}:${viewKey}`
    : `${startSec}:${endSec}:${hasTop}:${barSec}:${viewKey}`;
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
        if (e) {
          const look = lineLook(e.label, i);
          s.applyOptions({
            color: e.color ?? look.color,
            lineStyle: LINE_STYLE[e.style ?? look.style],
            lineWidth: (e.width ?? look.width) as 1 | 2 | 3 | 4,
          });
        }
        s.setData(
          e
            ? timeline
                .filter((t) => t <= playheadSec && e.values.has(t))
                .map((t) => ({ time: t, value: e.values.get(t) as number }))
            : [],
        );
      });
      // Level lines: thin (dashed unless styled), no axis tag (labels are drawn inside the pane).
      for (const l of rt.levelLines) rt.series.removePriceLine(l);
      rt.levelLines = (spec.levels ?? []).map((lv) =>
        rt.series.createPriceLine({
          price: lv.price,
          color: lv.color ?? LEVEL_COLOR,
          lineWidth: 1,
          lineStyle: LINE_STYLE[lv.style ?? "dashed"],
          axisLabelVisible: Boolean(spec.levelsOnAxis),
          axisLabelColor: lv.color ?? LEVEL_COLOR,
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
      noteFollow(true);
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

  // ---- auto-scale lock: on, the price axis fits the visible bars; off, drag the price axis ----
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    for (const rt of [p.top, p.bottom]) {
      if (!rt) continue;
      rt.chart.applyOptions({
        handleScale: { mouseWheel: false, pinch: false, axisPressedMouseMove: { time: false, price: !autoScale } },
      });
      rt.chart.priceScale("right").applyOptions({ autoScale });
    }
  }, [autoScale, hasTop, contractBelow]);

  // ---- replay crosshair: pinned to the latest candle unless the mouse is over the chart ----
  snapRef.current = () => {
    const p = panes.current;
    if (!p || !isReplay) return;
    crossProg.current = true;
    try {
      for (const rt of [p.top, p.bottom]) {
        if (!rt) continue;
        const rows = rt.series.data();
        let row: { time: unknown; close?: number } | null = null;
        for (let i = rows.length - 1; i >= 0; i--) {
          const r = rows[i] as { time: unknown; close?: number };
          if ((r.time as number) <= playheadSec && typeof r.close === "number") {
            row = r;
            break;
          }
        }
        if (row) rt.chart.setCrosshairPosition(row.close as number, row.time as UTCTimestamp, rt.series);
        else rt.chart.clearCrosshairPosition();
      }
    } finally {
      crossProg.current = false;
    }
  };
  // A step, Shift-step or play tick moves the playhead (or its second): pin to the latest candle.
  useEffect(() => {
    if (!isReplay) return;
    pointerMode.current = "latest";
    setHoverSec(null);
    snapRef.current?.();
    // the forming candle's data lands a frame later in lightweight-charts
    const id = requestAnimationFrame(() => {
      if (pointerMode.current === "latest") snapRef.current?.();
    });
    return () => cancelAnimationFrame(id);
  }, [isReplay, playheadSec, playheadLabel]);
  // Bars can land after the step (second bars are polled): re-pin once they are drawn, unless the mouse has the crosshair.
  const topCandles = top?.candles;
  const bottomCandles = bottom.candles;
  useEffect(() => {
    if (!isReplay || pointerMode.current !== "latest") return;
    const id = requestAnimationFrame(() => {
      if (pointerMode.current === "latest") snapRef.current?.();
    });
    return () => cancelAnimationFrame(id);
  }, [isReplay, topCandles, bottomCandles]);

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
        // A draggable stop gets a pill instead of the line title.
        title: onStopChange ? "" : stopAssumed ? "STOP (assumed)" : "STOP",
      });
    }
    rt.chart.priceScale("right").applyOptions({ autoScale: true }); // re-run the autoscale with the new stop
  }, [stopPrice, stopAssumed, hasTop, !!onStopChange]);

  // ---- practice target (green dashed), contract pane only ------------------------
  useEffect(() => {
    const rt = panes.current?.top;
    if (!rt) return;
    if (targetLine.current) {
      rt.series.removePriceLine(targetLine.current);
      targetLine.current = null;
    }
    if (targetPrice != null) {
      targetLine.current = rt.series.createPriceLine({
        price: targetPrice,
        color: THEME.up,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: onTargetChange ? "" : "TARGET",
      });
    }
    rt.chart.priceScale("right").applyOptions({ autoScale: true });
  }, [targetPrice, hasTop, !!onTargetChange]);

  // ---- blended average entry per open position (dotted), contract pane only -------
  const avgKey = positions.map((ps) => `${ps.kind}:${ps.avg}`).join("|");
  useEffect(() => {
    const rt = panes.current?.top;
    if (!rt) return;
    for (const l of avgLines.current) rt.series.removePriceLine(l);
    avgLines.current = positions.map((ps) =>
      rt.series.createPriceLine({
        price: ps.avg,
        color: ps.kind === "sim" ? THEME.play : THEME.avg,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
        title: "",
      }),
    );
    rt.chart.priceScale("right").applyOptions({ autoScale: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [avgKey, hasTop]);

  // ---- fill markers: short arrows only (details go to the hover readout) -----
  // Real fills mark the fill minute on BOTH panes (arrow at that bar, no option price on the
  // underlying axis). Sim fills mark the contract pane. Nothing after the playhead.
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const realMarkers = (color: (f: ReplayFill) => string): SeriesMarker<UTCTimestamp>[] =>
      fills.flatMap((f) => {
        const time = onBar(isoToUtcSeconds(f.ts));
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
      const time = onBar(isoToUtcSeconds(f.ts));
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
    const extra = (spec: PaneSpec | null): SeriesMarker<UTCTimestamp>[] =>
      (spec?.markers ?? []).flatMap((m) => {
        if (m.sec > playheadSec) return [];
        const up = m.side === "buy";
        return [
          {
            time: m.sec as UTCTimestamp,
            position: m.side === "sell" ? ("aboveBar" as const) : m.side === "exit" ? ("aboveBar" as const) : ("belowBar" as const),
            color: m.side === "exit" ? THEME.neutral : up ? THEME.up : THEME.down,
            shape: m.side === "exit" ? ("square" as const) : up ? ("arrowUp" as const) : ("arrowDown" as const),
            text: m.text,
            size: 1,
          },
        ];
      });
    // Session high / low so far: "H: x" over its candle, "L: x" under it (stacked with any fill
    // arrow on that bar by the chart, so they never overlap). Bars after the playhead don't count.
    const hiLo = (spec: PaneSpec | null): SeriesMarker<UTCTimestamp>[] => {
      if (!spec) return [];
      let hi: { t: number; p: number } | null = null;
      let lo: { t: number; p: number } | null = null;
      for (const c of spec.candles) {
        const t = onBar(isoToUtcSeconds(c.ts));
        if (t > playheadSec) continue;
        if (!hi || c.high > hi.p) hi = { t, p: c.high };
        if (!lo || c.low < lo.p) lo = { t, p: c.low };
      }
      const tag = (kind: "H" | "L", v: { t: number; p: number }): SeriesMarker<UTCTimestamp> => ({
        time: v.t as UTCTimestamp,
        position: kind === "H" ? "aboveBar" : "belowBar",
        color: THEME.text,
        shape: "circle",
        size: 0.5,
        text: `${kind}: ${v.p.toFixed(2)}`,
      });
      return [...(hi ? [tag("H", hi)] : []), ...(lo ? [tag("L", lo)] : [])];
    };
    // Replay ("arrows" mode) draws real and practice fills on the contract pane as DOM tags
    // (details on hover), so the canvas arrows there would only double them.
    const tagged = fillLabels === "arrows";
    p.top?.series.setMarkers(
      [...(tagged ? [] : [...realMarkers(side), ...sim]), ...extra(top), ...hiLo(top)].sort(byTime),
    );
    p.bottom.series.setMarkers([...realMarkers(side), ...extra(bottom), ...hiLo(bottom)].sort(byTime));
  }, [fills, simFills, playheadSec, hasTop, timeline, top, bottom, fillLabels]);

  // ---- fill tags (contract pane, replay): real and practice fills drawn so far ----
  const tagSources = useMemo(() => {
    const real = fills.flatMap((f) => {
      const exact = isoToUtcSeconds(f.ts);
      const sec = onBar(exact);
      if (sec > playheadSec) return [];
      return [{
        key: `r${f.seq}`,
        sec,
        price: f.price,
        side: f.side,
        sim: false,
        text: `${f.side === "buy" ? "B" : "S"} ${f.qty}`,
        details: [fillDetailText(f, ptClockSec(exact)), `${f.role === "entry" ? "Entry" : "Exit"} · your real fill`],
      }];
    });
    const practice = simFills.flatMap((f, i) => {
      const sec = onBar(isoToUtcSeconds(f.ts));
      if (sec > playheadSec) return [];
      return [{
        key: `s${i}`,
        sec,
        price: f.price,
        side: f.side,
        sim: true,
        text: `${f.side === "buy" ? "B" : "S"} ${f.qty}`,
        details: [fillDetailText(f, ptClock(sec)), "Practice fill (TradeDesk only)"],
      }];
    });
    return [...real, ...practice];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fills, simFills, playheadSec, startSec, barSec]);

  // ---- overlay geometry (px) for the shaded future / hold / chips ------------
  const chipSources = useMemo(
    () =>
      (fillLabels === "arrows" ? [] : fills)
        .map((f) => ({ sec: onBar(isoToUtcSeconds(f.ts)), exact: isoToUtcSeconds(f.ts), side: f.side }))
        .filter((f) => f.sec <= playheadSec)
        .map((f) => ({
          sec: f.sec,
          side: f.side,
          // The fill's own minute, not its bar's open (they differ on 5m+ bars).
          text: fillChipText(f.side, playheadLabel ? ptClockSec(f.exact) : ptClock(Math.floor(f.exact / 60) * 60)),
        })),
    [fills, playheadSec, playheadLabel, fillLabels, startSec, barSec],
  );
  useEffect(() => {
    const p = panes.current;
    if (!p) return;
    const measure = (rt: PaneRuntime | null, spec: PaneSpec | null): Layout => {
      if (!rt) return EMPTY_LAYOUT;
      const ts = rt.chart.timeScale();
      const x = (sec: number | null) =>
        sec === null ? null : (ts.timeToCoordinate(sec as UTCTimestamp) as number | null);
      // Both panes share one plot width: the pane minus its right price scale. (The time scale's
      // own width() can read 0 before its first paint, which hid every overlay.)
      const width = Math.max(
        p.bottom.chart.timeScale().width(),
        (bottomEl.current?.clientWidth ?? 0) - p.bottom.chart.priceScale("right").width(),
      );
      const h = (rt === p.top ? topEl.current : bottomEl.current)?.clientHeight ?? 0;
      const vr = readRange(ts.getVisibleLogicalRange());
      return {
        yMin: rt.series.coordinateToPrice(h) as number | null,
        yMax: rt.series.coordinateToPrice(0) as number | null,
        from: vr ? vr.from : null,
        to: vr ? vr.to : null,
        width,
        height: (rt === p.top ? topEl.current : bottomEl.current)?.clientHeight ?? 0,
        levelYs: (spec?.levelsOnAxis ? [] : (spec?.levels ?? [])).flatMap((lv) => {
          const y = rt.series.priceToCoordinate(lv.price) as number | null;
          return y === null ? [] : [{ label: lv.label, price: lv.price, y, color: lv.color }];
        }),
        markYs: (spec?.marks ?? []).flatMap((m) => {
          if (!m.key) return [];
          const y = rt.series.priceToCoordinate(m.price) as number | null;
          return y === null ? [] : [{ key: m.key, label: m.label, y }];
        }),
        playX: x(playheadSec),
        entryX: x(entrySec),
        exitX: x(exitSec),
        stopY:
          rt === p.top && stopPrice != null
            ? (rt.series.priceToCoordinate(stopPrice) as number | null)
            : null,
        targetY:
          rt === p.top && targetPrice != null
            ? (rt.series.priceToCoordinate(targetPrice) as number | null)
            : null,
        posYs:
          rt === p.top
            ? positions.flatMap((ps) => {
                const y = rt.series.priceToCoordinate(ps.avg) as number | null;
                return y === null ? [] : [{ key: ps.key, y }];
              })
            : [],
        fillTags:
          rt === p.top && fillLabels === "arrows"
            ? tagSources.flatMap((t) => {
                const tx = x(t.sec);
                const ty = rt.series.priceToCoordinate(t.price) as number | null;
                return tx === null || ty === null || tx < 0 || tx > width ? [] : [{ ...t, x: tx, y: ty }];
              })
            : [],
        chips: chipSources.flatMap((c) => {
          const cx = x(c.sec);
          return cx === null || cx < -4 || cx > width + 4 ? [] : [{ x: cx, text: c.text, side: c.side }];
        }),
      };
    };
    setLayouts({ top: measure(p.top, top), bottom: measure(p.bottom, bottom) });
  }, [layoutTick, playheadSec, entrySec, exitSec, stopPrice, targetPrice, avgKey, hasTop, top, bottom, chipSources, tagSources]);

  // ---- dragging the stop ---------------------------------------------------------
  const priceAtPointer = (e: React.PointerEvent, grabDy = 0): number | null => {
    const rt = panes.current?.top;
    const el = topEl.current;
    if (!rt || !el) return null;
    const y = e.clientY - el.getBoundingClientRect().top - grabDy; // where the line is, not the finger
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
  /** Pointer handlers that drag one plan line (stop or target) on the contract pane. */
  const dragHandlers = (kind: "stop" | "target", price: number, onChange: (p: number, final: boolean) => void) => ({
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => {
      e.currentTarget.setPointerCapture(e.pointerId);
      // Keep the grab offset, so the line does not jump to the pointer.
      const lineY = panes.current?.top?.series.priceToCoordinate(price) as number | null | undefined;
      const el = topEl.current;
      const grabDy = lineY != null && el ? e.clientY - el.getBoundingClientRect().top - lineY : 0;
      dragging.current = { kind, price, moved: false, grabDy };
      frozen.current = lastDomain.current.top;
    },
    onPointerMove: (e: React.PointerEvent<HTMLDivElement>) => {
      const d = dragging.current;
      if (d?.kind !== kind) return;
      const p = priceAtPointer(e, d.grabDy);
      if (p === null || p === d.price) return;
      dragging.current = { ...d, price: p, moved: true };
      onChange(p, false);
    },
    onPointerUp: () => {
      const d = dragging.current;
      if (d?.kind !== kind) return;
      endDrag();
      if (d.moved) onChange(d.price, true); // a click without a move is not a stop move
    },
    onPointerCancel: endDrag,
  });
  const topLayout = layouts.top;
  const tradeOverlay = (
    <>
      {(() => {
        // One row per position: the pill, then "+ Target" beside the practice one. Rows closer
        // than a pill's height are nudged apart so two positions never overlap.
        const rows = positions
          .flatMap((ps) => {
            const y = topLayout.posYs.find((v) => v.key === ps.key)?.y;
            return y === undefined ? [] : [{ ps, y: Math.min(topLayout.height - 10, Math.max(PILL_TOP_PX, y)) }];
          })
          .sort((a, b) => a.y - b.y);
        for (let i = 1; i < rows.length; i++) rows[i].y = Math.max(rows[i].y, rows[i - 1].y + 20);
        return rows.map(({ ps, y }) => {
          const risk = ps.riskPerShare;
          const suggested =
            ps.kind === "sim" && targetPrice == null && onTargetChange && risk != null && risk > 0
              ? Math.round((ps.avg + 2 * risk) * 100) / 100
              : null;
          return (
            <div key={ps.key} className="absolute flex -translate-y-1/2 items-center gap-1" style={{ top: y, left: 44 }}>
              <PositionPill
                y={0}
                qty={ps.qty}
                pnl={ps.pnl}
                pnlText={ps.pnl === null ? "—" : formatPnl(ps.pnl)}
                avgText={`${ps.kind === "sim" ? "Practice" : "Your"} position: ${ps.qty} @ ${ps.avg.toFixed(2)} avg`}
                inline
              />
              {suggested !== null && (
                <button
                  type="button"
                  className="pointer-events-auto rounded border border-up/60 bg-chart px-1.5 text-label text-up hover:bg-up/15"
                  onClick={() => onTargetChange?.(suggested, true)}
                  title={`Set a practice target at ${suggested.toFixed(2)} (2R off the risk at your first buy); drag it to move`}
                  data-testid="add-target"
                >
                  + Target
                </button>
              )}
            </div>
          );
        });
      })()}
      {stopPrice != null && topLayout.stopY != null && onStopChange && (
        <LinePill
          y={topLayout.stopY}
          kind="stop"
          label={stopAssumed ? "Stop (assumed)" : "Stop"}
          priceText={stopPrice.toFixed(2)}
          draggable
          {...dragHandlers("stop", stopPrice, onStopChange)}
        />
      )}
      {targetPrice != null && topLayout.targetY != null && onTargetChange && (
        <LinePill
          y={topLayout.targetY}
          kind="target"
          label="Target"
          priceText={targetPrice.toFixed(2)}
          draggable
          {...dragHandlers("target", targetPrice, onTargetChange)}
          onClear={() => onTargetChange(null, true)}
        />
      )}
      {topLayout.fillTags.map((t) => (
        <FillTag
          key={t.key}
          x={t.x}
          y={t.y}
          side={t.side}
          sim={t.sim}
          text={t.text}
          details={t.details}
          placeAbove={t.side === "sell"}
        />
      ))}
    </>
  );

  // ---- dragging a mark on the underlying pane (Learn: your level, stop, target) ----
  // The drag listens on the window, not the handle: the handle re-renders as the line moves,
  // and element pointer capture does not survive that.
  const markDragRef = useRef(onMarkDrag);
  markDragRef.current = onMarkDrag;
  const startMarkDrag = (key: string, e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const priceAt = (clientY: number): number | null => {
      const rt = panes.current?.bottom;
      const el = bottomEl.current;
      if (!rt || !el) return null;
      const raw = rt.series.coordinateToPrice(clientY - el.getBoundingClientRect().top);
      if (raw == null) return null;
      const price = Math.round(raw * 100) / 100;
      return price > 0 ? price : null;
    };
    let last: number | null = null;
    const move = (ev: PointerEvent) => {
      const price = priceAt(ev.clientY);
      if (price === null || price === last) return;
      last = price;
      markDragRef.current?.(key, price, false);
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      if (last !== null) markDragRef.current?.(key, last, true);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  };
  const markHandles = onMarkDrag
    ? layouts.bottom.markYs.map((m) => (
        <div
          key={m.key}
          role="slider"
          aria-label={`Drag the ${m.label.toLowerCase()} line`}
          data-testid={`mark-handle-${m.key}`}
          className="pointer-events-auto absolute inset-x-0 z-10 h-3 cursor-row-resize touch-none"
          style={{ top: m.y - 6 }}
          onPointerDown={(e) => startMarkDrag(m.key, e)}
        />
      ))
    : null;

  // The legend reads the hovered bar, else the latest drawn one (the playhead bar on Replay).
  const shown = hoverSec ?? playheadSec;
  // The underlying's bid/ask is read at the legend bar's last second, never past the replay.
  const quoteLimit = isReplay ? (revealSec ?? playheadSec + barSec - 1) : null;
  const quoteAt = shown === null ? null : quoteAtSec(shown, barSec, quoteLimit);

  // Fill details for the hovered minute (the markers themselves stay short; the full per-fill
  // list lives beside the chart on Replay and under it on the trade page).
  const detailFor = (which: "top" | "bottom", minute: number | null): string[] => {
    if (minute === null) return [];
    const out: string[] = [];
    for (const f of fills) {
      const t = onBar(isoToUtcSeconds(f.ts));
      if (t !== minute || t > playheadSec) continue;
      // Every fill in the minute gets its own chip with its exact second and order type. The
      // underlying pane leaves out the option price (it is not a price on that axis), unless it
      // is the only pane (a share trade: the bottom pane is the traded instrument).
      out.push(fillDetailText(f, ptClockSec(isoToUtcSeconds(f.ts)), { price: which === "top" || !hasTop }));
    }
    if (which === "top") {
      for (const f of simFills) {
        const t = onBar(isoToUtcSeconds(f.ts));
        if (t !== minute || t > playheadSec) continue;
        out.push(`sim ${f.side === "buy" ? "B" : "S"} ${f.qty} @ ${f.price.toFixed(2)}`);
      }
    }
    return out;
  };

  const noDataFor = (spec: PaneSpec): string | null => {
    if (isReplay && playheadLabel) return null; // Seconds mode: a quiet minute is not missing data
    const last = spec.candles.length ? onBar(isoToUtcSeconds(spec.candles[spec.candles.length - 1].ts)) : null;
    let at: number | null = null;
    if (isReplay) {
      if (last !== null && playheadSec > last) at = last; // only once the playhead has passed it
    } else if (last !== null && last < endSec) at = last;
    else if (noDataAfterSec !== null && (last === null || last <= noDataAfterSec)) at = noDataAfterSec;
    return at === null ? null : `No data after ${ptClock(at)}`;
  };

  // The stop toggle belongs to the contract pane; the zoom set lives once, on the underlying
  // pane, and zooms both panes together. How-to text sits in the "?" tooltip.
  const stopToggleNode =
    stopPriceProp != null ? (
      <div className="pointer-events-auto ml-auto flex shrink-0 items-center gap-1.5 pl-2">
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
      </div>
    ) : null;
  const zoomNode = (
    <div className="pointer-events-auto ml-auto flex shrink-0 items-center gap-1.5 pl-2" data-testid="chart-controls">
      {!top && stopToggleNode}
      <span
        className="cursor-help rounded-md p-1 text-muted-foreground"
        title={tradingView ? HELP_TEXT_TV : HELP_TEXT}
        aria-label={tradingView ? HELP_TEXT_TV : HELP_TEXT}
        role="img"
      >
        <HelpCircle className="h-4 w-4" />
      </span>
      <button
        type="button"
        aria-label="Zoom out"
        title="Zoom out (both charts)"
        onClick={() => controls.current?.zoom(BUTTON_ZOOM)}
        className="rounded-md border border-border p-1 text-muted-foreground transition-colors hover:bg-muted"
      >
        <Minus className="h-4 w-4" />
      </button>
      <button
        type="button"
        aria-label="Zoom in"
        title="Zoom in (both charts)"
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
    // No text selection or native drag: a drag on the chart must never pick up a ghost image of it.
    <div
      className={`relative flex h-full min-h-0 w-full min-w-0 select-none ${top && contractBelow ? "flex-col-reverse" : "flex-col"}`}
      onDragStart={(e) => e.preventDefault()}>
      {isReplay && !following && (
        <button
          type="button"
          onClick={() => controls.current?.jumpLatest()}
          className="absolute bottom-9 z-30 flex items-center gap-1 rounded-md border border-border bg-card/95 px-2 py-1 text-label text-foreground shadow-sm transition-colors hover:bg-muted"
          style={{ right: PRICE_SCALE_WIDTH + 12 }}
          title="Scroll to the latest candle and follow it again"
          data-testid="jump-latest"
        >
          Jump to latest
          <ChevronsRight className="h-3.5 w-3.5" />
        </button>
      )}
      {top && (
        <Pane
          className={`${mainPane === "bottom" ? "flex-[3]" : "flex-[6]"}${contractBelow ? " border-t border-border" : ""}`}
          spec={top}
          layout={layouts.top}
          shown={shown}
          playheadSec={playheadSec}
          isReplay={isReplay}
          hovered={hoverSec !== null}
          playheadLabel={playheadLabel}
          elRef={topEl}
          timeframe={timeframeLabel(barSec)}
          lastOnly
          showTicks
          overlay={tradeOverlay}
          // Replay tags each fill on this pane (details on hover), so no details box here.
          details={fillLabels === "arrows" ? [] : detailFor("top", shown)}
          detailsAsBox={fillLabels === "arrows"}
          noData={noDataFor(top)}
          controls={
            topActions ? (
              <>
                {topActions}
                {stopToggleNode}
              </>
            ) : (
              stopToggleNode
            )
          }
          barSec={barSec}
        />
      )}
      <Pane
        className={
          top
            ? `${mainPane === "bottom" ? "flex-[7]" : "flex-[4]"}${contractBelow ? "" : " border-t border-border"}`
            : "flex-1"
        }
        spec={bottom}
        layout={layouts.bottom}
        shown={shown}
        playheadSec={playheadSec}
        isReplay={isReplay}
        hovered={hoverSec !== null}
        playheadLabel={playheadLabel}
        elRef={bottomEl}
        timeframe={timeframeLabel(barSec)}
        showTicks={!top}
        tickTop={!top}
        overlay={markHandles}
        details={detailFor("bottom", shown)}
        detailsAsBox={fillLabels === "arrows"}
        noData={noDataFor(bottom)}
        controls={zoomNode}
        quoteAt={bottom.quoteSymbol ? quoteAt : null}
        barSec={barSec}
      />
    </div>
  );
}

/**
 * The one price chart, with its bottom bar: ranges, Interval (minute bars rolled up on the
 * client), Underlying/Option (which pane leads) and the Auto-scale lock.
 */
export function PriceChart({ footer = true, ...props }: Props) {
  const [tfMin, setTfMin] = useState(1);
  const [focus, setFocus] = useState<"top" | "bottom">(props.mainPane ?? "top");
  const [autoScale, setAutoScale] = useState(true);
  const baseBar = props.barSec ?? 60;
  const ctl = props.interval;
  const canRollUp = baseBar === 60 && !ctl;
  const eff = useMemo(
    () => (canRollUp && tfMin > 1 ? withInterval(props, tfMin * 60) : props),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // Primitive deps: callers pass `mode` / `defaultView` as fresh objects every render.
    [canRollUp, tfMin, props.top, props.bottom, props.startSec, props.endSec, props.playheadSec, props.mode.kind,
      props.mode.kind === "review" ? props.mode.exitSec : null, props.defaultView?.fromSec, props.defaultView?.toSec,
      props.noDataAfterSec, props.initialBars, props.fills, props.simFills, props.revealSec],
  );
  const core = <PriceChartCore {...props} {...eff} mainPane={focus} autoScale={autoScale} />;
  if (!footer) return core;
  const seg = (on: boolean, enabled = true) =>
    `num rounded px-1.5 py-0.5 text-label transition-colors ${
      on ? "bg-muted text-foreground" : enabled ? "text-muted-foreground hover:text-foreground" : "cursor-not-allowed text-muted-foreground/40"
    }`;
  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-col bg-chart">
      <div className="min-h-0 flex-1">{core}</div>
      <div
        className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-chart px-3 py-1"
        data-testid="chart-footer"
      >
        <div className="flex items-center gap-0.5" role="group" aria-label="Range">
          {RANGES.map((r) => (
            <button
              key={r}
              type="button"
              disabled={r !== "1D"}
              aria-pressed={r === "1D"}
              className={seg(r === "1D", r === "1D")}
              title={r === "1D" ? "The trade day" : "Only the trade day is loaded"}
            >
              {r}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-0.5" role="group" aria-label="Interval">
          <span className="mr-1 text-label text-muted-foreground">Interval:</span>
          {INTERVALS.map((iv) => {
            const ctlKey = ctl?.options.find((o) => o.toLowerCase() === iv.label.toLowerCase());
            const enabled = ctl ? ctlKey !== undefined : iv.available && (canRollUp ? iv.min >= 1 : iv.min * 60 === baseBar);
            const on = ctl ? ctl.value.toLowerCase() === iv.label.toLowerCase() : canRollUp ? tfMin === iv.min : iv.min * 60 === baseBar;
            return (
              <button
                key={iv.label}
                type="button"
                disabled={!enabled}
                aria-pressed={on}
                onClick={() => {
                  if (!enabled) return;
                  if (ctl && ctlKey) ctl.onChange(ctlKey);
                  else if (canRollUp) setTfMin(iv.min);
                }}
                className={seg(on, enabled)}
                title={ctl ? (enabled ? `${iv.label} bars` : "Not offered for this chart") : !iv.available ? iv.why : !canRollUp ? "This chart sets its own bar size" : `${iv.label} bars`}
                data-testid={`interval-${iv.label}`}
              >
                {iv.label}
              </button>
            );
          })}
        </div>
        <div className="ml-auto flex items-center gap-3">
          {props.top && (
            <div className="flex items-center gap-0.5" role="group" aria-label="Which chart leads">
              <button
                type="button"
                aria-pressed={focus === "bottom"}
                onClick={() => setFocus("bottom")}
                className={seg(focus === "bottom")}
                title="The underlying gets most of the height"
              >
                Underlying
              </button>
              <button
                type="button"
                aria-pressed={focus === "top"}
                onClick={() => setFocus("top")}
                className={seg(focus === "top")}
                title="The option gets most of the height"
              >
                Option
              </button>
            </div>
          )}
          <button
            type="button"
            aria-pressed={autoScale}
            onClick={() => setAutoScale((v) => !v)}
            className={`flex items-center gap-1 ${seg(autoScale)}`}
            title={autoScale ? "Auto-scale on: the price axis fits the visible bars. Click to scale it yourself (drag the price axis)." : "Auto-scale off: drag the price axis to scale. Click to fit the bars again."}
            data-testid="autoscale"
          >
            {autoScale ? <Lock className="h-3 w-3" /> : <LockOpen className="h-3 w-3" />}
            Auto-scale
          </button>
        </div>
      </div>
    </div>
  );
}

/** The chart's props on `tfSec` bars: minute bars rolled up, times floored onto the new grid. */
function withInterval(p: Props, tfSec: number): Partial<Props> {
  const ph = p.playheadSec;
  // Replay rolls up only what is drawn so far; review shows its whole window.
  const cut = p.mode.kind === "replay" ? ph : Number.POSITIVE_INFINITY;
  const pane = (s: PaneSpec | null): PaneSpec | null =>
    s && {
      ...s,
      candles: rollUp(s.candles, tfSec, cut),
      vwap: s.vwap ? rollUpLine(s.vwap, tfSec, cut) : undefined,
      emas: s.emas?.map((e) => ({ ...e, values: rollUpLine(e.values, tfSec, cut) })),
      markers: s.markers?.map((m) => ({ ...m, sec: bucketOf(m.sec, tfSec) })),
    };
  const b = (sec: number) => bucketOf(sec, tfSec);
  // The bar holding the playhead is still forming: quotes and fills stop at the real playhead
  // (its minute), never at the end of the larger bar.
  const lastSec = ph + (p.barSec ?? 60) - 1;
  const seen = <T extends { ts: string }>(xs: T[] | undefined) =>
    p.mode.kind === "replay" ? xs?.filter((f) => Math.floor(Date.parse(f.ts) / 1000) <= lastSec) : xs;
  return {
    revealSec: p.revealSec ?? lastSec,
    fills: seen(p.fills) ?? [],
    simFills: seen(p.simFills),
    top: pane(p.top),
    bottom: pane(p.bottom) as PaneSpec,
    startSec: b(p.startSec),
    endSec: b(p.endSec),
    playheadSec: b(ph),
    barSec: tfSec,
    mode: p.mode.kind === "review" && p.mode.exitSec != null ? { kind: "review", exitSec: b(p.mode.exitSec) } : p.mode,
    defaultView: p.defaultView && {
      fromSec: b(p.defaultView.fromSec),
      toSec: p.defaultView.toSec == null ? p.defaultView.toSec : b(p.defaultView.toSec),
    },
    noDataAfterSec: p.noDataAfterSec == null ? p.noDataAfterSec : b(p.noDataAfterSec),
    initialBars: p.initialBars == null ? p.initialBars : Math.max(1, Math.ceil(p.initialBars / (tfSec / 60))),
  };
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
  tickTop = false,
  overlay,
  details,
  detailsAsBox = false,
  noData,
  controls,
  timeframe,
  lastOnly = false,
  quoteAt = null,
  barSec = 60,
}: {
  /** "1m", shown after the symbol in the legend. */
  timeframe: string;
  /** Read the bid/ask (spec.quoteSymbol) at this second; null: no quote line. */
  quoteAt?: number | null;
  barSec?: number;
  /** Option pane: say it is the last trade (there is no historical bid/ask to show). */
  lastOnly?: boolean;
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
  tickTop?: boolean;
  overlay?: React.ReactNode;
  details: string[];
  /** Show `details` as a stacked box over the plot (one line per fill) instead of in the header. */
  detailsAsBox?: boolean;
  noData: string | null;
  controls?: React.ReactNode;
}) {
  const bar = shown === null ? null : legendBar(spec.candles, shown, playheadSec);
  // Header quote: the latest drawn close and its change since the session's first bar (never a bar after the playhead).
  const session = useMemo(() => {
    const first = spec.candles[0];
    let last: Candle | null = null;
    for (const c of spec.candles) {
      if (isoToUtcSeconds(c.ts) > playheadSec) break;
      last = c;
    }
    if (!first || !last || first.open === 0) return null;
    const change = last.close - first.open;
    return { last: last.close, change, pct: (change / first.open) * 100 };
  }, [spec.candles, playheadSec]);
  const lineAt = (m: Map<number, number> | undefined) =>
    shown === null || shown > playheadSec ? undefined : valueAtOrBefore(m, shown, barSec);
  const quote = useLegendQuote(spec.quoteSymbol ?? null, quoteAt);
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
      {/* Header + OHLC readout for the hovered (or playhead) minute. One fixed-height line: if
          the readout could wrap, hovering would resize the plot under the cursor and jitter. */}
      <div className="flex h-8 shrink-0 items-center gap-x-3 whitespace-nowrap px-3">
        {/* The readout clips; the controls never move. w-0 + flex-1: the readout takes the space
            left over and adds nothing to the chart's min-content width. Without it the nowrap
            OHLC text widened the page's grid column on hover, resizing the chart under the
            cursor (the Trade window "duplicate labels" glitch). */}
        <div className="flex w-0 min-w-0 flex-1 items-center gap-x-3 overflow-hidden">
        <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="text-label font-semibold text-foreground">{spec.label}</span>
        {session && (
          <span className="num flex items-baseline gap-1.5 text-label" data-testid="pane-quote">
            <span className="text-foreground">{session.last.toFixed(2)}</span>
            <span
              className={session.change >= 0 ? "text-up" : "text-down"}
              title="Change since the first bar of the session drawn so far"
            >
              {session.change >= 0 ? "▲" : "▼"} {Math.abs(session.change).toFixed(2)} ({Math.abs(session.pct).toFixed(2)}%)
            </span>
          </span>
        )}
        {spec.badge}
        <span className="num text-label text-muted-foreground">
          {shown === null
            ? ""
            : !hovered && isReplay && playheadLabel
              ? playheadLabel
              : ptClock(shown)}
          {hovered ? "" : isReplay ? " · playhead" : " · last bar"}
        </span>
        {lastOnly && (
          <span className="flex items-center gap-0.5" role="group" aria-label="Option price" data-testid="option-price-source">
            {(["Mid", "Bid", "Last", "Ask"] as const).map((k) => (
              <span
                key={k}
                aria-disabled={k !== "Last"}
                className={`num rounded px-1 text-label ${k === "Last" ? "bg-muted text-foreground" : "text-muted-foreground/40"}`}
                title={
                  k === "Last"
                    ? "Bars are the option's last trade (or the estimated premium)"
                    : "No historical option quotes in this data feed, so mid, bid and ask can't be drawn"
                }
              >
                {k}
              </span>
            ))}
          </span>
        )}
        {!detailsAsBox &&
          details.map((d, i) => (
            <span key={`${i}:${d}`} className="num rounded border border-border px-1.5 text-label text-foreground">
              {d}
            </span>
          ))}
        </div>
        {controls}
      </div>
      {/* Fill time chips, above the candles so they never cover a bar. None in arrows mode. */}
      <div
        className="relative shrink-0 overflow-hidden"
        style={{ height: layout.chips.length === 0 ? 0 : lanes * CHIP_LANE_PX }}
      >
        <div className="pointer-events-none absolute inset-y-0 left-0" style={{ width }}>
          {layout.chips.map((c, i) => (
            <span
              key={`${c.text}:${i}`}
              className={`num absolute whitespace-nowrap rounded border bg-card px-1 text-label ${
                c.side === "buy" ? "border-up/60 text-up" : "border-down/60 text-down"
              }`}
              style={{ left: placed[i]?.left ?? 0, top: (placed[i]?.lane ?? 0) * CHIP_LANE_PX + 1 }}
            >
              {c.text}
            </span>
          ))}
        </div>
      </div>
      <div className="relative min-h-0 flex-1 bg-chart">
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
              {showTicks && <TickLabel top={tickTop}>IN</TickLabel>}
            </div>
          )}
          {exitX !== null && (
            <div className="absolute inset-y-0 w-px bg-foreground/50" style={{ left: exitX }}>
              {showTicks && <TickLabel top={tickTop}>OUT</TickLabel>}
            </div>
          )}
          {isReplay && playX !== null && <div className="absolute inset-y-0 w-px bg-play" style={{ left: playX }} />}
          {spreadLabels(layout.levelYs, LEVEL_LABEL_GAP, layout.height).map((lv) => (
            <span
              key={lv.label}
              className="num absolute right-1 -translate-y-1/2 rounded bg-card/80 px-1 text-label text-grey"
              style={{ top: lv.labelY, color: lv.color }}
            >
              {lv.label} {lv.price.toFixed(2)}
            </span>
          ))}
          {noData && (
            <span className="num absolute right-1 top-1 rounded bg-card/90 px-1 text-label text-muted-foreground">
              {noData}
            </span>
          )}
        </div>
        {/* Interactive pieces (pills, tags, drag handles) sit above the chart canvas. */}
        {overlay && (
          <div className="pointer-events-none absolute inset-y-0 left-0 z-10" style={{ width: layout.width }}>
            {overlay}
          </div>
        )}
        {/* Legend, top left: symbol and timeframe, OHLC and change for the hovered (or
            latest) bar, then the indicator values at that bar. Fill details stack below it.
            Kept outside the plot overlay so it never depends on the measured plot width. */}
        <div className="pointer-events-none absolute left-1.5 top-1 z-10 flex max-w-[calc(100%-5rem)] flex-col items-start gap-0.5">
          <div className="num flex flex-wrap items-baseline gap-x-2 text-label leading-tight" data-testid="chart-legend">
            <span className="font-semibold text-foreground">{legendSymbol(spec.label)}</span>
            <span className="text-muted-foreground">{timeframe}</span>
            {bar ? (
              <span className={`flex flex-wrap gap-x-2 ${bar.close >= bar.open ? "text-up" : "text-down"}`}>
                <span><span className="text-foreground">O </span>{bar.open.toFixed(2)}</span>
                <span><span className="text-foreground">H </span>{bar.high.toFixed(2)}</span>
                <span><span className="text-foreground">L </span>{bar.low.toFixed(2)}</span>
                <span><span className="text-foreground">C </span>{bar.close.toFixed(2)}</span>
                {bar.change != null && <span>{formatChange(bar)}</span>}
              </span>
            ) : (
              <span className="text-muted-foreground">no bar</span>
            )}
            {lastOnly && bar && (
              <span
                className="text-foreground"
                title="Last trade at this bar. Bid/ask is not shown: the data source has no historical option quotes."
              >
                <span className="text-muted-foreground">Last </span>
                {bar.close.toFixed(2)}
              </span>
            )}
            {spec.quoteSymbol && bar && quoteAt !== null && (
              quote?.available && quote.bid != null && quote.ask != null ? (
                <span className="text-foreground" data-testid="legend-bidask" title={`Bid × ask at ${ptClockSec(quoteAt)}`}>
                  <span className="text-muted-foreground">Bid×Ask </span>
                  {formatBidAsk(quote.bid, quote.ask)}
                </span>
              ) : quote ? (
                <span className="text-foreground" data-testid="legend-last" title={`No bid/ask: ${quote.reason ?? "no quote"}. Showing the last price.`}>
                  <span className="text-muted-foreground">Last </span>
                  {bar.close.toFixed(2)}
                </span>
              ) : null
            )}
          </div>
          {(spec.vwap || (spec.emas && spec.emas.length > 0)) && (
            <div className="num flex flex-wrap gap-x-2.5 text-label leading-tight" data-testid="chart-legend-lines">
              {spec.vwap && (
                <span className="flex items-center gap-1" style={{ color: THEME.vwap }}>
                  <LineSwatch color={THEME.vwap} style="solid" />
                  VWAP {lineAt(spec.vwap)?.toFixed(2) ?? "—"}
                </span>
              )}
              {spec.emas?.map((e, i) => {
                const look = lineLook(e.label, i);
                return (
                  <span key={e.label} className="flex items-center gap-1" style={{ color: e.color ?? look.color }}>
                    <LineSwatch color={e.color ?? look.color} style={e.style ?? look.style} />
                    {e.label} {lineAt(e.values)?.toFixed(2) ?? "—"}
                  </span>
                );
              })}
            </div>
          )}
          {detailsAsBox && details.length > 0 && (
            <div
              className="num flex flex-col rounded border border-border bg-card/90 px-1.5 py-0.5 text-label text-foreground"
              data-testid="fill-details"
            >
              {details.map((d, i) => (
                <span key={`${i}:${d}`} className={d.startsWith("B ") ? "text-up" : d.startsWith("S ") ? "text-down" : undefined}>
                  {d}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** A small legend swatch drawn like the line: solid, dashed or dots. */
function LineSwatch({ color, style }: { color: string; style: LineStyleName }) {
  if (style === "dots" || style === "dotted")
    return (
      <span className="inline-flex w-4 justify-between" aria-hidden>
        {[0, 1, 2].map((k) => (
          <span key={k} className="inline-block h-1 w-1 rounded-full" style={{ background: color }} />
        ))}
      </span>
    );
  return (
    <span
      className="inline-block w-4"
      aria-hidden
      style={{ borderTop: `2px ${style === "dashed" ? "dashed" : "solid"} ${color}` }}
    />
  );
}

/**
 * The legend's bid/ask: the stock's latest historical quote at `at` (epoch seconds), fetched
 * once the legend settles on a bar (hovering across bars does not fire a request per bar) and
 * cached per second. Undefined until the first answer.
 */
function useLegendQuote(symbol: string | null, at: number | null) {
  const [settled, setSettled] = useState(at);
  useEffect(() => {
    const id = setTimeout(() => setSettled(at), 150);
    return () => clearTimeout(id);
  }, [at]);
  const q = useQuery({
    queryKey: ["legend-quote", symbol, settled],
    queryFn: () => api.replayQuote(symbol as string, new Date((settled as number) * 1000).toISOString()),
    enabled: symbol !== null && settled !== null,
    staleTime: Infinity,
    retry: false,
    placeholderData: (prev) => prev,
  });
  if (q.isError) return { available: false, reason: "quotes could not be loaded", bid: null, ask: null };
  return q.data;
}

/** The legend's symbol: the pane label without its note ("SPY, proxy for SPX" -> "SPY"). */
function legendSymbol(label: string): string {
  return label.split(/[,·]/)[0].replace(/\s+premium$/i, "").trim();
}

/** IN/OUT tag on a vertical line; `top` when the pane's own time axis sits at the bottom. */
function TickLabel({ children, top = false }: { children: React.ReactNode; top?: boolean }) {
  return (
    <span className={`num absolute ${top ? "top-1" : "bottom-1"} left-1 rounded bg-card/80 px-1 text-label text-foreground`}>
      {children}
    </span>
  );
}
