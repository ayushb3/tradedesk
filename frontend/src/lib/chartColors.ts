/**
 * Indicator and level colors shared by every chart (replay, the trade page, Learn).
 * lightweight-charts needs literal colors. Meaning hues stay reserved: green/red candles and
 * fill arrows, blue (#3b82f6) for the playhead and sim fills, amber for the stop.
 */
import type { LevelKey } from "@/lib/api";

export type LineStyleName = "solid" | "dashed" | "dotted";

/** VWAP: white. */
export const VWAP_COLOR = "#f4f4f5";

/** EMAs: one green family, brightest first (9, 21, 50). A 4th+ EMA reuses the dimmest. */
export const EMA_GREENS = ["#4ade80", "#16a34a", "#166534"] as const;
export const emaColor = (i: number): string => EMA_GREENS[Math.min(i, EMA_GREENS.length - 1)];

/** SMA 200 / 400: red, dashed, 200 brighter than 400. */
export const SMA_REDS: Record<number, string> = { 200: "#ef4444", 400: "#991b1b" };
export const smaColor = (period: number): string => SMA_REDS[period] ?? SMA_REDS[400];

/** Premarket and prior-day high/low: yellow. The rest of the level ladder stays slate. */
export const LEVEL_YELLOW = "#facc15";
export const LEVEL_SLATE = "#64748b";
export const LEVEL_COLORS: Record<LevelKey, string> = {
  pdh: LEVEL_YELLOW,
  pdl: LEVEL_YELLOW,
  pmh: LEVEL_YELLOW,
  pml: LEVEL_YELLOW,
  or_high: LEVEL_SLATE,
  or_low: LEVEL_SLATE,
  day_high: LEVEL_SLATE,
  day_low: LEVEL_SLATE,
};

/** Caller ("Somesh") levels: blue lines and dotted rays; the 1h 200 SMA is red dashed. */
export const CALLER_BLUE = "#60a5fa";
export const CALLER_STYLE: Record<string, { color: string; style: LineStyleName; label: string }> = {
  level: { color: CALLER_BLUE, style: "solid", label: "S" },
  ray: { color: CALLER_BLUE, style: "dotted", label: "S ray" },
  sma200_1h: { color: SMA_REDS[200], style: "dashed", label: "200 SMA 1h" },
};
export const callerStyle = (kind: string) => CALLER_STYLE[kind] ?? CALLER_STYLE.level;
