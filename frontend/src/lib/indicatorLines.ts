/**
 * The moving-average lines a replay session carries, as minute-keyed maps ready for
 * PriceChart: EMAs 9/21/50 in greens, SMAs 200/400 in red dashes. The server computes them
 * causally (value at a bar uses only that bar and earlier), so a map is safe to reveal up
 * to the playhead.
 */
import type { LevelKey, ReplaySession } from "@/lib/api";
import { callerStyle, emaColor, LEVEL_COLORS, smaColor, type LineStyleName } from "@/lib/chartColors";
import { isoToUtcSeconds, valueAt } from "@/lib/chart";
import { floorMinute } from "@/lib/stackedReplay";
import { LADDERS, type StackIndicators } from "@/lib/stackIndicators";

export interface ChartLine {
  label: string;
  values: Map<number, number>;
  color: string;
  style: LineStyleName;
}

/** EMAs drawn on the stacked charts (the EMA 200 is left out: the SMA 200 is the 200 line). */
export const STACK_EMA_PERIODS = [9, 21, 50] as const;
export const SMA_PERIODS = [200, 400] as const;

function minuteMap(session: ReplaySession, key: string): Map<number, number> {
  const series = (session.indicators as Record<string, (number | null)[] | undefined>)[key];
  const out = new Map<number, number>();
  if (!series) return out;
  session.candles.forEach((c, i) => {
    const v = series[i];
    if (v != null && !Number.isNaN(v)) out.set(floorMinute(isoToUtcSeconds(c.ts)), v);
  });
  return out;
}

export function emaLines(session: ReplaySession): ChartLine[] {
  return STACK_EMA_PERIODS.filter((p) => `ema${p}` in session.indicators).map((p, i) => ({
    label: `EMA${p}`,
    values: minuteMap(session, `ema${p}`),
    color: emaColor(i),
    style: "solid" as const,
  }));
}

export function smaLines(session: ReplaySession): ChartLine[] {
  return SMA_PERIODS.filter((p) => `sma${p}` in session.indicators).map((p) => ({
    label: `SMA${p}`,
    values: minuteMap(session, `sma${p}`),
    color: smaColor(p),
    style: "dashed" as const,
  }));
}

export function vwapMap(session: ReplaySession): Map<number, number> {
  return minuteMap(session, "vwap");
}

export interface PaneLevel {
  label: string;
  price: number;
  color: string;
  style: LineStyleName;
}

/**
 * The SPY pane's horizontal lines at bar `idx` (the causal value there, never a later one):
 * the chosen level ladder (premarket / prior-day yellow, the rest slate) and, when on, the
 * caller's levels for the day (constant; blue lines, dotted rays, the 1h 200 SMA red dashed).
 */
export function paneLevels(session: ReplaySession, idx: number, ind: StackIndicators): PaneLevel[] {
  const out: PaneLevel[] = LADDERS[ind.ladder].levels.flatMap((l: { key: LevelKey; label: string }) => {
    const v = valueAt(session.levels[l.key], idx);
    return v == null ? [] : [{ label: l.label, price: v, color: LEVEL_COLORS[l.key], style: "dashed" as const }];
  });
  if (ind.caller) {
    for (const cl of session.caller_levels ?? []) {
      const st = callerStyle(cl.kind);
      out.push({ label: st.label, price: cl.price, color: st.color, style: st.style });
    }
  }
  return out;
}
