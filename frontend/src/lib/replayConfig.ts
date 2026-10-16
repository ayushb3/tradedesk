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

export const DEFAULT_REPLAY_CONFIG: ReplayConfig = {
  timeframe: "1m",
  emaPeriods: DEFAULT_EMA_PERIODS,
  rsiPeriod: DEFAULT_RSI_PERIOD,
  orMinutes: DEFAULT_OR_MINUTES,
  hiddenOverlays: new Set(),
  hiddenLevels: new Set(),
  rsiHidden: false,
};
