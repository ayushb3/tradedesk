/**
 * useReplay — thin wrapper over useReplayEngine that adds:
 *   - barLabel: pre-formatted "12:12 ET" string (current bar's ET wall-clock time)
 *   - jumpToTime(hhmm): seek to a bar by ET wall-clock time, returns true if seeked
 *   - keyboard bindings: Space=toggle, ArrowLeft=stepBack, ArrowRight=stepForward
 *
 * Session identity change resets position (delegated to engine via total change).
 * Pass options.keyboardEnabled = false to suppress shortcuts (e.g. when a modal is open).
 */
import { useCallback, useEffect, useMemo } from "react";

import type { ReplaySession } from "@/lib/api";
import {
  useReplayEngine,
  type ReplayEngine,
} from "@/lib/useReplayEngine";

// Re-export Speed and helpers so consumers only need one import.
export type { Speed } from "@/lib/useReplayEngine";
export { SPEEDS, speedLabel } from "@/lib/useReplayEngine";

/**
 * Full state surface exposed to the control bar and chart.
 * Extends ReplayEngine with session-aware fields.
 *
 * jumpToTime(hhmm) returns true if a bar matching the requested ET time was
 * found and jumped to; false if no bar matched (caller may show a red ring).
 */
export interface ReplayState extends ReplayEngine {
  /** "12:12 ET" — the current bar's ET wall-clock time; empty while loading. */
  barLabel: string;
  /**
   * Seek the playhead to the bar at the given ET wall-clock time (HH:MM). If no
   * bar matches that exact minute (e.g. a pre-open time, or an off-grid minute on
   * a coarse timeframe), seeks to the nearest bar AT OR AFTER the requested time,
   * clamped to the session. Returns true if a seek happened, false only when the
   * input can't be parsed as a time (caller may show a red ring).
   */
  jumpToTime: (hhmm: string) => boolean;
}

/**
 * Build an HH:MM formatter for the given timezone.
 *
 * Uses hourCycle "h23" (00:00–23:59) rather than the ambiguous hour12:false
 * flag, which some Safari/Chrome versions interpret as "h24" (producing
 * "24:00" at midnight instead of "00:00").
 */
function makeHmFormatter(tz: string): Intl.DateTimeFormat {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: tz || "America/New_York",
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
function etMinutesOfDay(fmt: Intl.DateTimeFormat, iso: string): number | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const parts = fmt.formatToParts(d);
  const hh = Number(parts.find((p) => p.type === "hour")?.value);
  const mm = Number(parts.find((p) => p.type === "minute")?.value);
  if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
  return hh * 60 + mm;
}

const SUPPRESS_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isInputFocused(target: EventTarget | null): boolean {
  if (!target || !(target instanceof Element)) return false;
  if (SUPPRESS_TAGS.has(target.tagName)) return true;
  if (target.hasAttribute("contenteditable")) return true;
  // Suppress when focus is anywhere inside a dialog (covers shadcn Dialog/Sheet).
  if (target.closest('[role="dialog"]')) return true;
  return false;
}

export function useReplay(
  session: ReplaySession | undefined,
  options?: {
    /** Default: true. Pass false to disable Space/Arrow keyboard shortcuts. */
    keyboardEnabled?: boolean;
  },
): ReplayState {
  const keyboardEnabled = options?.keyboardEnabled ?? true;

  const total = session?.candles.length ?? 0;
  const engine = useReplayEngine(total);

  // --- barLabel -----------------------------------------------------------
  // "12:12 ET" — the current bar's ET wall-clock time. The scrubber already
  // conveys position, so we no longer show the "N / total" bar count.
  const barLabel = useMemo(() => {
    if (!session || total === 0) return "";
    const candle = session.candles[engine.currentIndex];
    if (!candle) return "";
    try {
      const fmt = makeHmFormatter(session.meta.tz);
      const d = new Date(candle.ts);
      if (isNaN(d.getTime())) return "";
      return `${fmt.format(d)} ET`;
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

      const fmt = makeHmFormatter(session.meta.tz);
      const candles = session.candles;

      // Find the first bar AT OR AFTER the requested minute. Bars are chronological,
      // so the first one whose ET minute-of-day >= wanted is the seek target.
      for (let i = 0; i < candles.length; i++) {
        const barMinutes = etMinutesOfDay(fmt, candles[i].ts);
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

  // --- keyboard bindings --------------------------------------------------
  useEffect(() => {
    if (!keyboardEnabled) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (isInputFocused(e.target)) return;

      switch (e.key) {
        case " ":
          e.preventDefault(); // prevent page scroll
          engine.toggle();
          break;
        case "ArrowLeft":
          e.preventDefault();
          engine.stepBack();
          break;
        case "ArrowRight":
          e.preventDefault();
          engine.stepForward();
          break;
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [keyboardEnabled, engine.toggle, engine.stepBack, engine.stepForward]);

  return {
    ...engine,
    barLabel,
    jumpToTime,
  };
}
