/**
 * Indicator choices for the stacked trade replay (SPY pane).
 * Defaults: VWAP plus one level ladder. EMAs are opt-in and drawn in grey.
 * RSI is not drawn in the stack.
 */
import type { LevelKey } from "@/lib/api";

export type Ladder = "key" | "premarket" | "range" | "off";

export interface StackIndicators {
  vwap: boolean;
  ladder: Ladder;
  ema: boolean;
}

export const DEFAULT_STACK_INDICATORS: StackIndicators = {
  vwap: true,
  ladder: "key",
  ema: false,
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

/** Greys for EMA 1..4, light to dark. Never a meaning hue. */
export const EMA_GREYS = ["#d4d4d8", "#a1a1aa", "#8b8b93", "#71717a"];
export const MAX_EMAS = EMA_GREYS.length;
