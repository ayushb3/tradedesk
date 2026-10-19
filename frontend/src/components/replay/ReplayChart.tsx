import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LogicalRange,
  type SeriesMarker,
  type UTCTimestamp,
} from "lightweight-charts";

import type { IndicatorKey, LevelKey, ReplayFill, ReplaySession, SimTrade } from "@/lib/api";
import {
  isoToUtcSeconds,
  makeEtFormatters,
  revealedCandlestickData,
  revealedLineData,
  valueAt,
} from "@/lib/chart";

// Keep in sync with the dark theme in index.css.
const CHART_THEME = {
  text: "#a1a1aa",
  grid: "#26262b",
  border: "#26262b",
  up: "#26a69a",
  down: "#ef5350",
};

/** Colors cycled through for EMA overlays, in order of increasing period. */
const EMA_COLORS = ["#f5a623", "#4f9dff", "#a855f7", "#ec4899", "#14b8a6", "#f97316"];

export interface OverlaySpec {
  key: string; // matches a key in session.indicators, e.g. "ema9"
  label: string;
  color: string;
  width: 1 | 2;
}

/**
 * Build the overlay-series specs from the EMA keys actually present in the
 * session payload (keys are dynamic: ema9, ema21, …). VWAP is appended last.
 * Sorted by EMA period so colors are assigned shortest→longest consistently.
 */
export function overlaySpecs(indicators: Record<string, unknown>): OverlaySpec[] {
  const emaKeys = Object.keys(indicators)
    .filter((k) => /^ema\d+$/.test(k))
    .sort((a, b) => Number(a.slice(3)) - Number(b.slice(3)));
  const specs: OverlaySpec[] = emaKeys.map((k, i) => ({
    key: k,
    label: k.toUpperCase(),
    color: EMA_COLORS[i % EMA_COLORS.length],
    width: 2,
  }));
  if ("vwap" in indicators) {
    specs.push({ key: "vwap", label: "VWAP", color: "#22d3ee", width: 2 });
  }
  return specs;
}

/** RSI key present in the payload (rsi14, rsi7, …), or null. */
export function rsiKey(indicators: Record<string, unknown>): string | null {
  return Object.keys(indicators).find((k) => /^rsi\d+$/.test(k)) ?? null;
}

/** Horizontal levels drawn as price lines, using the value at currentIndex. */
export const LEVEL_META: { key: LevelKey; label: string; color: string }[] = [
  { key: "pdh", label: "PDH", color: "#f59e0b" },
  { key: "pdl", label: "PDL", color: "#f59e0b" },
  { key: "pmh", label: "PMH", color: "#8b5cf6" },
  { key: "pml", label: "PML", color: "#8b5cf6" },
  { key: "or_high", label: "ORH", color: "#10b981" },
  { key: "or_low", label: "ORL", color: "#10b981" },
  { key: "day_high", label: "HOD", color: "#64748b" },
  { key: "day_low", label: "LOD", color: "#64748b" },
];

const RSI_COLOR = "#eab308";

/**
 * Which overlays / levels / RSI to draw. A key absent from a set (or the set
 * being undefined) means "show it" — visibility is opt-out so a freshly loaded
 * session shows everything. `hiddenOverlays`/`hiddenLevels` hold the keys the
 * user has toggled OFF.
 */
export interface ChartVisibility {
  hiddenOverlays: Set<string>;
  hiddenLevels: Set<string>;
  rsiHidden: boolean;
}

const SHOW_ALL: ChartVisibility = {
  hiddenOverlays: new Set(),
  hiddenLevels: new Set(),
  rsiHidden: false,
};

interface ReplayChartProps {
  session: ReplaySession;
  currentIndex: number;
  trades: SimTrade[];
  visibility?: ChartVisibility;
  /** Contract mode: horizontal stop line at this premium level. */
  stopPrice?: number | null;
  /** Contract mode: fills already revealed by the server (labelled in PT). */
  fills?: ReplayFill[];
  /** Contract mode has no RSI; hide the subpane. */
  hideRsi?: boolean;
}

/**
 * Price chart (candles + EMA/VWAP overlays + level price lines) with a synced
 * RSI subchart below. Everything is driven by `currentIndex`: only bars
 * 0..currentIndex are ever fed to the chart, so no future data is displayed.
 *
 * Overlay/RSI keys are dynamic (ema9, ema8, rsi14, rsi7, …) — derived from the
 * session payload via overlaySpecs()/rsiKey() so custom indicator params flow
 * through without hardcoding.
 */
export function ReplayChart({
  session,
  currentIndex,
  trades,
  visibility = SHOW_ALL,
  stopPrice = null,
  fills = [],
  hideRsi = false,
}: ReplayChartProps) {
  const priceRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);

  const priceChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlayRefs = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const levelLinesRef = useRef<IPriceLine[]>([]);
  const stopLineRef = useRef<IPriceLine | null>(null);
  const fittedKeyRef = useRef<string | null>(null);

  // ---- create charts once -------------------------------------------------
  useEffect(() => {
    const priceEl = priceRef.current;
    const rsiEl = rsiRef.current;
    if (!priceEl || !rsiEl) return;

    const et = makeEtFormatters(session.meta.tz);

    const commonLayout = {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: CHART_THEME.text,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    } as const;

    const priceChart = createChart(priceEl, {
      layout: commonLayout,
      grid: {
        vertLines: { color: CHART_THEME.grid },
        horzLines: { color: CHART_THEME.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: CHART_THEME.border, minimumWidth: 64 },
      localization: { timeFormatter: et.timeFormatter },
      timeScale: {
        borderColor: CHART_THEME.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: et.tickMarkFormatter,
        // The shared time axis lives under the RSI pane.
        visible: false,
      },
      autoSize: false,
    });

    const rsiChart = createChart(rsiEl, {
      layout: commonLayout,
      grid: {
        vertLines: { color: CHART_THEME.grid },
        horzLines: { color: "transparent" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: CHART_THEME.border, minimumWidth: 64 },
      localization: { timeFormatter: et.timeFormatter },
      timeScale: {
        borderColor: CHART_THEME.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: et.tickMarkFormatter,
      },
      autoSize: false,
    });

    const candleSeries = priceChart.addCandlestickSeries({
      upColor: CHART_THEME.up,
      downColor: CHART_THEME.down,
      borderVisible: false,
      wickUpColor: CHART_THEME.up,
      wickDownColor: CHART_THEME.down,
      priceLineVisible: false,
    });

    // Overlay line series (EMA/VWAP) are created/destroyed reactively in a
    // dedicated effect below, because their keys are dynamic (custom EMA
    // periods) and toggled by visibility.
    overlayRefs.current.clear();

    const rsiSeries = rsiChart.addLineSeries({
      color: RSI_COLOR,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      // Pin RSI to a 0..100 frame regardless of the revealed slice.
      autoscaleInfoProvider: () => ({
        priceRange: { minValue: 0, maxValue: 100 },
      }),
    });
    for (const level of [30, 70]) {
      rsiSeries.createPriceLine({
        price: level,
        color: CHART_THEME.grid,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: String(level),
      });
    }

    priceChartRef.current = priceChart;
    rsiChartRef.current = rsiChart;
    candleSeriesRef.current = candleSeries;
    rsiSeriesRef.current = rsiSeries;

    // Keep the two time scales in lockstep (guarded against feedback loops).
    let syncing = false;
    const link = (from: IChartApi, to: IChartApi) =>
      from.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
        if (syncing || !range) return;
        syncing = true;
        to.timeScale().setVisibleLogicalRange(range);
        syncing = false;
      });
    link(priceChart, rsiChart);
    link(rsiChart, priceChart);

    const resize = () => {
      priceChart.applyOptions({
        width: priceEl.clientWidth,
        height: priceEl.clientHeight,
      });
      rsiChart.applyOptions({
        width: rsiEl.clientWidth,
        height: rsiEl.clientHeight,
      });
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(priceEl);
    ro.observe(rsiEl);

    fittedKeyRef.current = null;

    return () => {
      ro.disconnect();
      priceChart.remove();
      rsiChart.remove();
      priceChartRef.current = null;
      rsiChartRef.current = null;
      candleSeriesRef.current = null;
      rsiSeriesRef.current = null;
      overlayRefs.current.clear();
      levelLinesRef.current = [];
    };
    // Recreate when the session timezone changes (formatters are captured).
  }, [session.meta.tz]);

  // ---- manage overlay line series lifecycle (dynamic keys + visibility) ----
  // EMA/VWAP series are added/removed here so custom indicator periods and
  // on/off toggles take effect without recreating the whole chart. Keyed on the
  // set of visible overlay keys.
  const visibleOverlays = overlaySpecs(session.indicators).filter(
    (o) => !visibility.hiddenOverlays.has(o.key),
  );
  const overlaySignature = visibleOverlays.map((o) => o.key).join(",");
  useEffect(() => {
    const chart = priceChartRef.current;
    if (!chart) return;
    const wanted = new Map(visibleOverlays.map((o) => [o.key, o]));

    // Remove series no longer wanted.
    for (const [key, series] of overlayRefs.current) {
      if (!wanted.has(key)) {
        chart.removeSeries(series);
        overlayRefs.current.delete(key);
      }
    }
    // Add newly wanted series.
    for (const o of visibleOverlays) {
      if (!overlayRefs.current.has(o.key)) {
        overlayRefs.current.set(
          o.key,
          chart.addLineSeries({
            color: o.color,
            lineWidth: o.width,
            lineStyle: LineStyle.Solid,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          }),
        );
      }
    }
    // Reveal effect below repopulates data for the current index.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlaySignature, session.meta.tz]);

  // ---- progressive reveal on every index / session change -----------------
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    const rsiSeries = rsiSeriesRef.current;
    if (!candleSeries || !rsiSeries) return;

    const { candles, indicators, levels } = session;
    if (candles.length === 0) return;

    const end = Math.min(currentIndex, candles.length - 1);
    // Candles: reveal 0..currentIndex as real bars; later bars are whitespace
    // so the full session width exists as logical slots from the first render.
    candleSeries.setData(revealedCandlestickData(candles, end));

    // Overlays: causal series revealed up to currentIndex. Iterate the live
    // series map (managed by the lifecycle effect) so dynamic keys + toggles work.
    for (const [key, series] of overlayRefs.current) {
      series.setData(revealedLineData(candles, indicators[key as IndicatorKey], end));
    }

    // RSI subpane — key is dynamic (rsi14, rsi7, …). Hide when toggled off.
    const rKey = rsiKey(indicators);
    rsiSeries.setData(
      visibility.rsiHidden || !rKey
        ? []
        : revealedLineData(candles, indicators[rKey as IndicatorKey], end),
    );

    // Level price lines: value AT currentIndex only (skip nulls + hidden).
    for (const line of levelLinesRef.current) candleSeries.removePriceLine(line);
    levelLinesRef.current = [];
    for (const lv of LEVEL_META) {
      if (visibility.hiddenLevels.has(lv.key)) continue;
      const price = valueAt(levels[lv.key], end);
      if (price == null) continue;
      levelLinesRef.current.push(
        candleSeries.createPriceLine({
          price,
          color: lv.color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: lv.label,
        }),
      );
    }

    // On first render of a session, lock the visible logical range to the FULL
    // session width. The candle series is padded with whitespace for unrevealed
    // bars (see revealedCandlestickData), so all `candles.length` logical slots
    // already exist — the range spans the whole session instead of being clamped
    // to the 1–2 real bars present at index=0. Reveals then land inside this
    // fixed window; the user can pan/zoom freely without the range being reset.
    const key = `${session.symbol}:${session.session_date}:${candles.length}`;
    if (fittedKeyRef.current !== key) {
      // Half-bar margin on each side so the first/last candle isn't flush
      // against the chart edge (lightweight-charts convention).
      const fullRange = { from: -0.5, to: candles.length - 0.5 };
      priceChartRef.current?.timeScale().setVisibleLogicalRange(fullRange);
      rsiChartRef.current?.timeScale().setVisibleLogicalRange(fullRange);
      fittedKeyRef.current = key;
    }
    // overlaySignature/visibility drive series set + which lines are drawn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, currentIndex, overlaySignature, visibility]);

  // ---- time axis: shown under the RSI pane, or on the price pane without RSI --
  useEffect(() => {
    priceChartRef.current?.applyOptions({ timeScale: { visible: hideRsi } });
  }, [hideRsi, session.meta.tz]);

  // ---- stop line (Contract mode): one horizontal line at the stop premium --
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;
    if (stopLineRef.current) {
      candleSeries.removePriceLine(stopLineRef.current);
      stopLineRef.current = null;
    }
    if (stopPrice == null) return;
    stopLineRef.current = candleSeries.createPriceLine({
      price: stopPrice,
      color: CHART_THEME.down,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "STOP",
    });
  }, [stopPrice, session]);

  // ---- trade markers (only for bars already revealed) ---------------------
  useEffect(() => {
    const candleSeries = candleSeriesRef.current;
    if (!candleSeries) return;
    const { candles } = session;
    if (candles.length === 0) return;

    const end = Math.min(currentIndex, candles.length - 1);
    const cutoff = isoToUtcSeconds(candles[end].ts);

    const markers: SeriesMarker<UTCTimestamp>[] = [];
    trades.forEach((t, ti) => {
      const isLong = t.direction === "long";
      for (const leg of t.entries) {
        const time = isoToUtcSeconds(leg.ts);
        if (time > cutoff) continue; // never mark a future fill
        markers.push({
          time,
          position: isLong ? "belowBar" : "aboveBar",
          color: isLong ? CHART_THEME.up : CHART_THEME.down,
          shape: isLong ? "arrowUp" : "arrowDown",
          text: `#${ti + 1} ${isLong ? "L" : "S"}`,
        });
      }
      for (const leg of t.exits) {
        const time = isoToUtcSeconds(leg.ts);
        if (time > cutoff) continue;
        markers.push({
          time,
          position: isLong ? "aboveBar" : "belowBar",
          color: "#a1a1aa",
          shape: "square",
          text: "exit",
        });
      }
    });
    // Real fills (Contract mode): the server already withheld future ones; the
    // cutoff check is a second guard. Labelled with the PT clock time.
    for (const f of fills) {
      const time = Math.floor(isoToUtcSeconds(f.ts) / 60) * 60;
      if (time > cutoff) continue;
      const isBuy = f.side === "buy";
      markers.push({
        time: time as UTCTimestamp,
        position: isBuy ? "belowBar" : "aboveBar",
        color: isBuy ? CHART_THEME.up : CHART_THEME.down,
        shape: isBuy ? "arrowUp" : "arrowDown",
        text: `${f.role} ${f.qty} @ ${f.price.toFixed(2)} ${f.time_pt}`,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    candleSeries.setMarkers(markers);
  }, [session, currentIndex, trades, fills]);

  return (
    <div className="flex h-full min-h-0 w-full flex-col">
      <div ref={priceRef} className="min-h-0 flex-1" />
      <div
        ref={rsiRef}
        className={hideRsi ? "hidden" : "h-32 shrink-0 border-t border-border"}
        aria-label="RSI(14)"
      />
    </div>
  );
}
