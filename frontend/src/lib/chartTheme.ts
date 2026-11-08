/**
 * The one chart theme. lightweight-charts needs literal colors, so these mirror the tokens in
 * index.css; every chart imports them from here instead of keeping its own copy.
 * Indicator and level colors live in lib/chartColors.
 */
import { VWAP_COLOR } from "@/lib/chartColors";

export const CHART_THEME = {
  text: "#a1a1aa",
  grid: "#26262b",
  border: "#26262b",
  up: "#26a69a",
  down: "#ef5350",
  /** Playhead, sim fills. */
  play: "#3b82f6",
  /** The stop line. */
  stop: "#f59e0b",
  vwap: VWAP_COLOR,
  /** Blended average entry. */
  avg: "#a1a1aa",
  /** Crosshair lines (thin, dashed) and their axis tags. */
  crosshair: "#9598a1",
  crosshairLabel: "#363a45",
  /** Exit markers that are neither a buy nor a sell. */
  neutral: "#a1a1aa",
  font: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
} as const;
