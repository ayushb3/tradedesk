import { useEffect, useRef } from "react";
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Candle, CriticAdherence, ReviewFill } from "@/lib/api";
import { isoToUtcSeconds, makeDisplayFormatters, toCandlestickData } from "@/lib/chart";
import { DISPLAY_TZ } from "@/lib/format";

// Keep in sync with the dark theme (mirrors ReplayChart CHART_THEME).
const CHART_THEME = {
  text: "#a1a1aa",
  grid: "#26262b",
  border: "#26262b",
  up: "#26a69a",
  down: "#ef5350",
};

// Marker colors: entry = teal/red by side, exit = neutral grey. A hovered
// marker brightens (highlight) without changing meaning.
const ENTRY_BUY = "#26a69a";
const ENTRY_SELL = "#ef5350";
const EXIT_COLOR = "#a1a1aa";
const HILITE_ENTRY_BUY = "#4fd6c8";
const HILITE_ENTRY_SELL = "#ff8785";
const HILITE_EXIT = "#f4f4f5";

/** A horizontal reference band drawn across the price scale (e.g. MFE/MAE). */
export interface PriceBand {
  price: number;
  color: string;
  title: string;
}

/**
 * A critic rule-check pinned to a bar (PRD-M9 R9). `iso` is the ISO-8601 UTC
 * timestamp of the evidence bar; `adherence` drives the marker color. Rendered
 * VISUALLY DISTINCT from fill markers: a circle centered on the bar (fills use
 * side-encoded arrows above/below), so a rule check never reads as a fill.
 */
export interface RuleCheckMarker {
  iso: string;
  adherence: CriticAdherence;
  /** Short label shown under the marker (e.g. the rule name, truncated). */
  label: string;
}

// Rule-check marker colors by adherence — distinct from the fill palette.
const RULE_CHECK_COLOR: Record<CriticAdherence, string> = {
  pass: "#34d399", // emerald
  fail: "#fb7185", // rose
  unclear: "#fbbf24", // amber
};

interface ReviewChartProps {
  candles: Candle[];
  fills: ReviewFill[];
  timeframe: "1m" | "5m";
  /** Exchange timezone for axis/crosshair formatting. */
  tz?: string;
  /** Currently-hovered fill id (shared with FillTable), or null. */
  hoveredFillId: number | null;
  onHoverFill: (id: number | null) => void;
  /**
   * Optional horizontal reference bands (MFE/MAE excursion levels). Drawn as
   * dashed price lines on the series. Omitted (M7a underlying) ⇒ no lines.
   */
  priceBands?: PriceBand[];
  /**
   * Optional critic rule-check annotations (PRD-M9 R9). Each maps its ISO ts to
   * the containing bar and draws a circle marker colored by adherence. Markers
   * whose ts falls outside the visible candle window are skipped.
   */
  ruleCheckMarkers?: RuleCheckMarker[];
  /**
   * Initial visible range (epoch seconds). Omitted => fit all bars. The user can
   * still zoom/pan freely; a new range (or new bars) re-frames the chart.
   */
  visibleRange?: { from: number; to: number } | null;
  /** Bars opening after this epoch second are drawn greyed (post-exit). */
  dimAfter?: number | null;
}

const DIM_COLOR = "rgba(161, 161, 170, 0.35)";

function markerColor(f: ReviewFill, hovered: boolean): string {
  if (f.role === "exit") return hovered ? HILITE_EXIT : EXIT_COLOR;
  if (f.side === "buy") return hovered ? HILITE_ENTRY_BUY : ENTRY_BUY;
  return hovered ? HILITE_ENTRY_SELL : ENTRY_SELL;
}

/**
 * Bucket a fill's raw execution timestamp DOWN to its containing bar's open
 * second, for the active timeframe. lightweight-charts places markers at the
 * bar whose `time` EXACTLY equals the marker time, and `subscribeCrosshairMove`
 * reports `param.time` as a bar-open UTCTimestamp. A raw exec ts (e.g. 09:32:14)
 * floors-to-second at 09:32:00, which aligns with 1m bar-opens by luck but never
 * with 5m bar-opens (:00/:05/:10…). Bucketing to the bar step fixes both marker
 * placement and the crosshair→row match on every timeframe.
 */
function bucketToBar(iso: string, timeframe: "1m" | "5m"): UTCTimestamp {
  const seconds = isoToUtcSeconds(iso) as number;
  const step = timeframe === "5m" ? 300 : 60;
  return (seconds - (seconds % step)) as UTCTimestamp;
}

/**
 * Static (non-replay) underlying candle chart with one marker per fill.
 *
 * Marker vocabulary (distinct entry vs exit at a glance):
 *  - buy  → ▲ arrowUp,   sell → ▼ arrowDown  (shape follows the executed side)
 *  - entry markers sit BELOW the bar, exit markers ABOVE it (position encodes role)
 *  - entries are colored teal/red by side; exits are neutral grey
 *  - each marker is labelled `role qty@price`
 *
 * Two-way hover link with the fill table: `hoveredFillId` re-renders markers so
 * the matching one brightens; a crosshair move over a fill's bar raises
 * `onHoverFill` so the table row highlights. Unlike ReplayChart this shows the
 * full window at once (no progressive reveal) — it's a review, not a replay.
 */
export function ReviewChart({
  candles,
  fills,
  timeframe,
  tz = DISPLAY_TZ,
  hoveredFillId,
  onHoverFill,
  priceBands,
  ruleCheckMarkers,
  visibleRange,
  dimAfter,
}: ReviewChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const fittedKeyRef = useRef<string | null>(null);
  // Latest fills + hover callback, read inside the crosshair subscription
  // without re-subscribing on every render.
  const fillsRef = useRef(fills);
  const onHoverRef = useRef(onHoverFill);
  const timeframeRef = useRef(timeframe);
  fillsRef.current = fills;
  onHoverRef.current = onHoverFill;
  timeframeRef.current = timeframe;

  // ---- create the chart once (recreated only when tz changes) --------------
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const et = makeDisplayFormatters(tz);
    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: CHART_THEME.text,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      },
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
      },
      autoSize: false,
    });

    const series = chart.addCandlestickSeries({
      upColor: CHART_THEME.up,
      downColor: CHART_THEME.down,
      borderVisible: false,
      wickUpColor: CHART_THEME.up,
      wickDownColor: CHART_THEME.down,
      priceLineVisible: false,
    });

    chartRef.current = chart;
    seriesRef.current = series;
    fittedKeyRef.current = null;

    // Chart → table hover: map the crosshair bar time to a fill on that bar.
    const onCrosshair = (param: MouseEventParams<Time>) => {
      if (param.time == null) {
        onHoverRef.current(null);
        return;
      }
      const t = param.time as UTCTimestamp;
      // A fill belongs to whichever bar contains its timestamp; match fills whose
      // bucketed bar-open time equals the crosshair bar time. `param.time` is the
      // bar's UTCTimestamp (seconds). When several fills share a bar (a scale-in/
      // out within one bar), pick the first deterministically; the marker effect
      // highlights ALL fills on the same bar, so the whole stack lights up.
      const hit = fillsRef.current.find(
        (f) => bucketToBar(f.ts, timeframeRef.current) === t,
      );
      onHoverRef.current(hit ? hit.id : null);
    };
    chart.subscribeCrosshairMove(onCrosshair);

    const resize = () =>
      chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshair);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [tz]);

  // ---- feed candle data + frame the window --------------------------------
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    if (candles.length === 0) {
      series.setData([]);
      return;
    }
    const data = toCandlestickData(candles);
    series.setData(
      dimAfter == null
        ? data
        : data.map((d) =>
            (d.time as number) > dimAfter
              ? { ...d, color: DIM_COLOR, borderColor: DIM_COLOR, wickColor: DIM_COLOR }
              : d,
          ),
    );

    // Frame once per distinct window (symbol/tf/length/range); zoom/pan after
    // that is the user's.
    const key = `${candles[0]?.symbol}:${timeframe}:${candles.length}:${candles[0]?.ts}:${visibleRange?.from ?? ""}:${visibleRange?.to ?? ""}`;
    if (fittedKeyRef.current !== key) {
      if (visibleRange) {
        try {
          chart.timeScale().setVisibleRange({
            from: visibleRange.from as UTCTimestamp,
            to: visibleRange.to as UTCTimestamp,
          });
        } catch {
          chart.timeScale().fitContent();
        }
      } else {
        chart.timeScale().fitContent();
      }
      fittedKeyRef.current = key;
    }
  }, [candles, timeframe, visibleRange, dimAfter]);

  // ---- markers (re-rendered on fills OR hover to drive the highlight) ------
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    // The hovered fill's bar-open time (if any). Every fill bucketed to the same
    // bar highlights together, so a stack of same-bar fills (scale-in/out) lights
    // up as one — regardless of whether the hover came from the chart or the table.
    const hovered = fills.find((f) => f.id === hoveredFillId) ?? null;
    const hoveredBar =
      hovered != null ? bucketToBar(hovered.ts, timeframe) : null;

    const markers: SeriesMarker<UTCTimestamp>[] = fills.map((f) => {
      const bar = bucketToBar(f.ts, timeframe);
      const isHovered = hoveredBar != null && bar === hoveredBar;
      return {
        time: bar,
        // Role encodes position: entries below the bar, exits above.
        position: f.role === "entry" ? "belowBar" : "aboveBar",
        // Side encodes shape: buys point up, sells point down.
        shape: f.side === "buy" ? "arrowUp" : "arrowDown",
        color: markerColor(f, isHovered),
        text: `${f.role} ${f.qty}@${f.price.toFixed(2)}`,
        size: isHovered ? 2 : 1,
      };
    });

    // Rule-check markers (PRD-M9 R9): visually distinct from fills — a circle
    // sitting IN the bar (not an arrow above/below), colored by adherence. Only
    // markers whose bucketed bar falls inside the visible candle window are
    // drawn (guard: skip ts that don't map to a bar we're showing).
    if (ruleCheckMarkers && ruleCheckMarkers.length > 0 && candles.length > 0) {
      const firstBar = bucketToBar(candles[0].ts, timeframe);
      const lastBar = bucketToBar(candles[candles.length - 1].ts, timeframe);
      for (const rc of ruleCheckMarkers) {
        const bar = bucketToBar(rc.iso, timeframe);
        if (bar < firstBar || bar > lastBar) continue; // outside window → skip
        markers.push({
          time: bar,
          position: "inBar",
          shape: "circle",
          color: RULE_CHECK_COLOR[rc.adherence] ?? RULE_CHECK_COLOR.unclear,
          text: rc.label,
          size: 1,
        });
      }
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);
  }, [fills, hoveredFillId, timeframe, ruleCheckMarkers, candles]);

  // ---- MFE/MAE bands (horizontal price lines) — opt-in via priceBands -------
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    // Clear any previously-drawn lines, then draw the current set.
    for (const line of priceLinesRef.current) series.removePriceLine(line);
    priceLinesRef.current = [];
    for (const band of priceBands ?? []) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: band.price,
          color: band.color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: band.title,
        }),
      );
    }
  }, [priceBands]);

  return <div ref={containerRef} className="h-full min-h-0 w-full" />;
}
