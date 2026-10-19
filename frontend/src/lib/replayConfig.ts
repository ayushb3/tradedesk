/**
 * Replay indicator/timeframe configuration shared across the Replay page, the
 * control bar (timeframe select + indicator settings popover), and the chart.
 *
 * Two kinds of setting:
 *   - **Params** (timeframe, EMA periods, RSI period, OR minutes) change what the
 *     backend computes → they refetch the session (part of the query key).
 *   - **Visibility** (which overlays/levels/RSI to draw) is pure client state →
 *     toggling is instant, no refetch.
 */
import type { ReplayTimeframe } from "@/lib/api";

export interface ReplayConfig {
  timeframe: ReplayTimeframe;
  emaPeriods: number[];
  rsiPeriod: number;
  orMinutes: number;
  /** Overlay keys (ema9, vwap, …) the user has toggled OFF. */
  hiddenOverlays: Set<string>;
  /** Level keys (pdh, or_high, …) the user has toggled OFF. */
  hiddenLevels: Set<string>;
  rsiHidden: boolean;
}

export const DEFAULT_EMA_PERIODS = [9, 21, 50, 200];
export const DEFAULT_RSI_PERIOD = 14;
export const DEFAULT_OR_MINUTES = 5;

/** Overlay keys for a set of EMA periods (the backend keys them ema<period>). */
export const emaKeys = (periods: number[]): string[] => periods.map((p) => `ema${p}`);

/** Defaults: VWAP and one level ladder (prior day + opening range) only. */
const DEFAULT_HIDDEN_LEVELS = ["pmh", "pml", "day_high", "day_low"];

export const DEFAULT_REPLAY_CONFIG: ReplayConfig = {
  timeframe: "1m",
  emaPeriods: DEFAULT_EMA_PERIODS,
  rsiPeriod: DEFAULT_RSI_PERIOD,
  orMinutes: DEFAULT_OR_MINUTES,
  // EMAs are opt-in, RSI is off.
  hiddenOverlays: new Set(emaKeys(DEFAULT_EMA_PERIODS)),
  hiddenLevels: new Set(DEFAULT_HIDDEN_LEVELS),
  rsiHidden: true,
};
