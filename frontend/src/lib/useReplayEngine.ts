import { useCallback, useEffect, useState } from "react";

/**
 * Bar-replay engine. The engine owns exactly one piece of truth: `currentIndex`.
 * Every consumer (chart, overlays, trade panel) reads the world at
 * `currentIndex` and NEVER beyond it — that single gate is what enforces the
 * no-lookahead guarantee across the whole UI.
 */

export type Speed = 0.5 | 1 | 2 | 4 | 5 | 10 | "max";

export const SPEEDS: readonly Speed[] = [0.5, 1, 2, 5, 10, "max"] as const;

export function speedLabel(s: Speed): string {
  return s === "max" ? "Max" : `${s}x`;
}

/** Base cadence at 1x, in ms per bar. Speed scales this. */
const BASE_MS = 650;
const MAX_MS = 8;

export interface ReplayEngine {
  currentIndex: number;
  /** Alias for currentIndex — convenience for callers that talk about "bars". */
  currentBar: number;
  total: number;
  isPlaying: boolean;
  speed: Speed;
  atStart: boolean;
  atEnd: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  stepForward: () => void;
  stepBack: () => void;
  restart: () => void;
  setSpeed: (s: Speed) => void;
  setIndex: (i: number) => void;
}

export function useReplayEngine(total: number): ReplayEngine {
  const [currentIndex, setIndexState] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);

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

  // Advance timer while playing. Recreated when speed/total changes.
  useEffect(() => {
    if (!isPlaying || total === 0) return;
    const interval = speed === "max" ? MAX_MS : BASE_MS / speed;
    const id = window.setInterval(() => {
      setIndexState((i) => (i >= lastIndex ? i : i + 1));
    }, interval);
    return () => window.clearInterval(id);
  }, [isPlaying, speed, total, lastIndex]);

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
    setSpeed,
    setIndex,
  };
}
