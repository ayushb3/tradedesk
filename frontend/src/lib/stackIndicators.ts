/**
 * Indicator choices for the stacked trade replay (SPY pane).
 * Defaults: VWAP (white), one level ladder, SMA 200/400 (red dashed) and the caller's levels.
 * EMAs 9/21/50 are opt-in (greens). RSI is not drawn in the stack. Colors: lib/chartColors.ts.
 */
import type { LevelKey } from "@/lib/api";

export type Ladder = "key" | "premarket" | "range" | "off";

export interface StackIndicators {
  vwap: boolean;
  ladder: Ladder;
  ema: boolean;
  sma: boolean;
  /** The caller's posted levels for the day (blue lines, dotted rays, red 1h 200 SMA). */
  caller: boolean;
}

export const DEFAULT_STACK_INDICATORS: StackIndicators = {
  vwap: true,
  ladder: "key",
  ema: false,
  sma: true,
  caller: true,
};

export const LADDERS: Record<Ladder, { label: string; levels: { key: LevelKey; label: string }[] }> = {
  key: {
    label: "Key levels",
    levels: [
      { key: "pdh", label: "PDH" },
      { key: "pdl", label: "PDL" },
      { key: "or_high", label: "ORH" },
      { key: "or_low", label: "ORL" },
    ],
  },
  premarket: {
    label: "Premarket",
    levels: [
      { key: "pmh", label: "PMH" },
      { key: "pml", label: "PML" },
    ],
  },
  range: {
    label: "Day range",
    levels: [
      { key: "day_high", label: "HOD" },
      { key: "day_low", label: "LOD" },
    ],
  },
  off: { label: "No levels", levels: [] },
};
