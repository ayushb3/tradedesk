/**
 * Indicator and level colors shared by every chart (replay, the trade page, Learn).
 * lightweight-charts needs literal colors. Meaning hues stay reserved: green/red candles and
 * fill arrows, blue (#3b82f6) for the playhead and sim fills, amber for the stop.
 */
import type { LevelKey } from "@/lib/api";

/** "dots": sparse round dots (lightweight-charts SparseDotted), drawn wide enough to read. */
export type LineStyleName = "solid" | "dashed" | "dotted" | "dots";

export interface LineLook {
  color: string;
  style: LineStyleName;
  /** Line width in px. */
  width: 1 | 2 | 3 | 4;
}

/**
 * Every indicator line's look, on every chart (Replay, the trade page, the Learn drill player)
 * and its legend swatch. Change a color or style here and every chart follows.
 */
export const INDICATOR_LOOK = {
  vwap: { color: "#f4f4f5", style: "solid", width: 1 }, // white
  ema8: { color: "#4ade80", style: "solid", width: 1 }, // Learn's fast EMA: same as the 9
  ema9: { color: "#4ade80", style: "solid", width: 1 }, // green
  ema21: { color: "#b91c1c", style: "solid", width: 1 }, // dark red
  ema50: { color: "#7dd3fc", style: "solid", width: 1 }, // light blue
  sma200: { color: "#f97316", style: "dots", width: 2 }, // orange, dotted
  sma400: { color: "#ef4444", style: "dots", width: 2 }, // red, dotted
} satisfies Record<string, LineLook>;

const LOOKS: Record<string, LineLook> = INDICATOR_LOOK;
const EMA_ORDER = [INDICATOR_LOOK.ema9, INDICATOR_LOOK.ema21, INDICATOR_LOOK.ema50];

/**
 * The look for an indicator key ("ema21", "sma200", "vwap") or legend label ("EMA21").
 * An EMA period not in the table takes the look of its slot (`i`: 0 fast, 1, 2 slow); an
 * unknown SMA takes the SMA 400 look.
 */
export function lineLook(keyOrLabel: string, i = 0): LineLook {
  const k = keyOrLabel.toLowerCase().replace(/\s+/g, "");
  if (LOOKS[k]) return LOOKS[k];
  if (k.startsWith("sma")) return INDICATOR_LOOK.sma400;
  return EMA_ORDER[Math.min(Math.max(0, i), EMA_ORDER.length - 1)];
}

/** VWAP: white. */
export const VWAP_COLOR = INDICATOR_LOOK.vwap.color;

/** EMA color by slot (0 fast .. 2 slow); prefer lineLook("ema21") where the period is known. */
export const emaColor = (i: number): string => lineLook("ema", i).color;

/** SMA color by period (200 orange, 400 red). */
export const smaColor = (period: number): string => lineLook(`sma${period}`).color;

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
  sma200_1h: { color: "#ef4444", style: "dashed", label: "200 SMA 1h" },
};
export const callerStyle = (kind: string) => CALLER_STYLE[kind] ?? CALLER_STYLE.level;
