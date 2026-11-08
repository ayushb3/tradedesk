import { useCallback, useEffect, useState } from "react";

import { usePlayClock } from "@/lib/playClock";
import { DEFAULT_SPEED } from "@/lib/replaySpeed";

/**
 * Bar-replay engine. The engine owns exactly one piece of truth: `currentIndex`.
 * Every consumer (chart, overlays, trade panel) reads the world at
 * `currentIndex` and NEVER beyond it — that single gate is what enforces the
 * no-lookahead guarantee across the whole UI.
 */

export interface ReplayEngine {
  currentIndex: number;
  /** Alias for currentIndex — convenience for callers that talk about "bars". */
  currentBar: number;
  total: number;
  isPlaying: boolean;
  /** Chart-seconds per real second (the shared speed steps, lib/replaySpeed). */
  speed: number;
  atStart: boolean;
  atEnd: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  stepForward: () => void;
  stepBack: () => void;
  /** The fine step (Shift+arrow, Shift-click): one second on second bars. Absent on minute bars, where Shift steps a candle too. */
  stepFine?: (dir: 1 | -1) => void;
  restart: () => void;
  /** Sets the engine's own speed; ignored when the page drives `chartSpeed`. */
  setSpeed: (s: number) => void;
  setIndex: (i: number) => void;
}

export interface EngineOptions {
  /**
   * Speed in chart-seconds per real second, when the page owns it (a SpeedSlider). Omitted:
   * the engine keeps its own, starting at the default speed.
   */
  chartSpeed?: number;
  /** Bar length in seconds (default 60: minute bars). */
  barSec?: number;
}

export function useReplayEngine(total: number, opts?: EngineOptions): ReplayEngine {
  const barSec = opts?.barSec ?? 60;
  const [currentIndex, setIndexState] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [ownSpeed, setOwnSpeed] = useState<number>(DEFAULT_SPEED);
  const speed = opts?.chartSpeed ?? ownSpeed;

  // A new session (different length) resets the playhead to the first bar.
  useEffect(() => {
    setIndexState(0);
    setIsPlaying(false);
  }, [total]);

  const lastIndex = total > 0 ? total - 1 : 0;
  const atEnd = total === 0 || currentIndex >= lastIndex;
  const atStart = currentIndex <= 0;

  const clamp = useCallback(
    (i: number) => Math.max(0, Math.min(i, lastIndex)),
    [lastIndex],
  );

  const setIndex = useCallback(
    (i: number) => setIndexState(clamp(i)),
    [clamp],
  );

  // The shared clock: speed x elapsed real time, in whole bars.
  usePlayClock({
    playing: isPlaying && total > 0,
    speed,
    unitSec: barSec,
    onSteps: (n) => setIndexState((i) => Math.min(i + n, lastIndex)),
  });

  // Auto-stop at the last bar.
  useEffect(() => {
    if (atEnd && isPlaying) setIsPlaying(false);
  }, [atEnd, isPlaying]);

  const play = useCallback(() => {
    if (total === 0) return;
    // Replaying from the end restarts from the first bar.
    setIndexState((i) => (i >= lastIndex ? 0 : i));
    setIsPlaying(true);
  }, [total, lastIndex]);

  const pause = useCallback(() => setIsPlaying(false), []);
  const toggle = useCallback(() => {
    if (isPlaying) pause();
    else play();
  }, [isPlaying, play, pause]);

  const stepForward = useCallback(() => {
    setIsPlaying(false);
    setIndexState((i) => clamp(i + 1));
  }, [clamp]);

  const stepBack = useCallback(() => {
    setIsPlaying(false);
    setIndexState((i) => clamp(i - 1));
  }, [clamp]);

  const restart = useCallback(() => {
    setIsPlaying(false);
    setIndexState(0);
  }, []);

  return {
    currentIndex,
    currentBar: currentIndex,
    total,
    isPlaying,
    speed,
    atStart,
    atEnd,
    play,
    pause,
    toggle,
    stepForward,
    stepBack,
    restart,
    setSpeed: setOwnSpeed,
    setIndex,
  };
}
