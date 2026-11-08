/**
 * useReplay — thin wrapper over useReplayEngine that adds:
 *   - barLabel: pre-formatted "09:12 PT" string (current bar's display-zone (PT) wall-clock time)
 *   - jumpToTime(hhmm): seek to a bar by display-zone (PT) wall-clock time, returns true if seeked
 *   - the shared replay keys (lib/replayKeys): Space toggles, ←/→ step one candle
 *
 * Session identity change resets position (delegated to engine via total change).
 * Pass options.keyboardEnabled = false to suppress shortcuts (e.g. when a modal is open).
 */
import { useCallback, useMemo } from "react";

import type { ReplaySession } from "@/lib/api";
import { DISPLAY_TZ, DISPLAY_TZ_LABEL } from "@/lib/format";
import { useReplayKeys } from "@/lib/replayKeys";
import { barSecOf } from "@/lib/sessionChart";
import { useReplayEngine, type ReplayEngine } from "@/lib/useReplayEngine";


/**
 * Full state surface exposed to the control bar and chart.
 * Extends ReplayEngine with session-aware fields.
 *
 * jumpToTime(hhmm) returns true if a bar matching the requested PT time was
 * found and jumped to; false if no bar matched (caller may show a red ring).
 */
export interface ReplayState extends ReplayEngine {
  /** "09:12 PT" — the current bar's display-zone (PT) wall-clock time; empty while loading. */
  barLabel: string;
  /**
   * Seek the playhead to the bar at the given display-zone (PT) wall-clock time (HH:MM). If no
   * bar matches that exact minute (e.g. a pre-open time, or an off-grid minute on
   * a coarse timeframe), seeks to the nearest bar AT OR AFTER the requested time,
   * clamped to the session. Returns true if a seek happened, false only when the
   * input can't be parsed as a time (caller may show a red ring).
   */
  jumpToTime: (hhmm: string) => boolean;
}

/**
 * Build an HH:MM formatter for the display zone (`DISPLAY_TZ`, Pacific).
 *
 * Uses hourCycle "h23" (00:00–23:59) rather than the ambiguous hour12:false
 * flag, which some Safari/Chrome versions interpret as "h24" (producing
 * "24:00" at midnight instead of "00:00").
 */
function makeHmFormatter(): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: DISPLAY_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

/**
 * Minutes-since-midnight of an ISO timestamp in the formatter's timezone, or
 * null if it can't be parsed. Used to compare bar times against a jump target
 * without string-matching (so "at or after" seeks work).
 */
function minutesOfDay(fmt: Intl.DateTimeFormat, iso: string): number | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const parts = fmt.formatToParts(d);
  const hh = Number(parts.find((p) => p.type === "hour")?.value);
  const mm = Number(parts.find((p) => p.type === "minute")?.value);
  if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
  return hh * 60 + mm;
}

export function useReplay(
  session: ReplaySession | undefined,
  options?: {
    /** Default: true. Pass false to disable Space/Arrow keyboard shortcuts. */
    keyboardEnabled?: boolean;
    /** Speed (chart-seconds per real second); omitted, the engine keeps its own. */
    chartSpeed?: number;
    /** Speed ceiling (free replay plays whole bars and goes past 64x). */
    maxSpeed?: number;
  },
): ReplayState {
  const keyboardEnabled = options?.keyboardEnabled ?? true;

  const total = session?.candles.length ?? 0;
  const barSec = useMemo(() => (session ? barSecOf(session.candles) : 60), [session]);
  const engine = useReplayEngine(total, { chartSpeed: options?.chartSpeed, barSec, maxSpeed: options?.maxSpeed });

  // --- barLabel -----------------------------------------------------------
  // "09:12 PT" — the current bar's display-zone (PT) wall-clock time. The scrubber already
  // conveys position, so we no longer show the "N / total" bar count.
  const barLabel = useMemo(() => {
    if (!session || total === 0) return "";
    const candle = session.candles[engine.currentIndex];
    if (!candle) return "";
    try {
      const fmt = makeHmFormatter();
      const d = new Date(candle.ts);
      if (isNaN(d.getTime())) return "";
      return `${fmt.format(d)} ${DISPLAY_TZ_LABEL}`;
    } catch {
      // Intl or Date parsing failure — hide the label rather than show noise.
      return "";
    }
  }, [session, total, engine.currentIndex]);

  // --- jumpToTime ---------------------------------------------------------
  const jumpToTime = useCallback(
    (hhmm: string): boolean => {
      if (!session || total === 0) return false;
      // Accept "H:MM" or "HH:MM"; reject anything else.
      const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
      if (!m) return false;
      const hh = Number(m[1]);
      const mm = Number(m[2]);
      if (hh > 23 || mm > 59) return false;
      const wanted = hh * 60 + mm;

      const fmt = makeHmFormatter();
      const candles = session.candles;

      // Find the first bar AT OR AFTER the requested minute. Bars are chronological,
      // so the first one whose display-zone minute-of-day >= wanted is the seek target.
      for (let i = 0; i < candles.length; i++) {
        const barMinutes = minutesOfDay(fmt, candles[i].ts);
        if (barMinutes == null) continue;
        if (barMinutes >= wanted) {
          engine.setIndex(i);
          return true;
        }
      }
      // Requested time is past the last bar — seek to the final bar.
      engine.setIndex(candles.length - 1);
      return true;
    },
    [session, total, engine.setIndex],
  );

  // --- keys: the shared keymap (Space, ←/→ one candle; ↑/↓ belong to the speed control)
  useReplayKeys(
    {
      onToggle: engine.toggle,
      onStep: (dir) => (dir > 0 ? engine.stepForward() : engine.stepBack()),
    },
    keyboardEnabled,
  );

  return {
    ...engine,
    barLabel,
    jumpToTime,
  };
}
