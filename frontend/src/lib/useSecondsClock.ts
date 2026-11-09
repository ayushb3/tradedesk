import { useCallback, useEffect, useRef, useState } from "react";

import { usePlayClock } from "@/lib/playClock";
import { shiftSec } from "@/lib/secondsChart";

export interface SecondsClock {
  /** Second-level playhead: minuteSec + offset. */
  playSec: number | null;
  offset: number;
  isPlaying: boolean;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  /** ±1 second with carry across minutes; pauses. */
  stepFine: (dir: 1 | -1) => void;
  /** ±1 minute keeping the offset; pauses. */
  stepMinute: (dir: 1 | -1) => void;
  /** ±sec seconds with carry; pauses. */
  skip: (dir: 1 | -1, sec: number) => void;
  /** Jump to an absolute second (needs indexOfMinute); pauses. */
  setPlaySec: (sec: number) => void;
}

/** A second-level playhead layered on a minute-bar engine (inert when disabled). */
export function useSecondsClock({
  enabled,
  minuteSec,
  index,
  total,
  setIndex,
  engineIsPlaying,
  enginePause,
  speed,
  indexOfMinute,
  maxSpeed,
}: {
  enabled: boolean;
  minuteSec: number | null;
  index: number;
  total: number;
  setIndex: (i: number) => void;
  engineIsPlaying: boolean;
  enginePause: () => void;
  speed: number;
  indexOfMinute?: (minuteSec: number) => number;
  /** Speed ceiling (free replay goes past 64x). */
  maxSpeed?: number;
}): SecondsClock {
  const [offset, setOffset] = useState(59);
  const [playing, setPlaying] = useState(false);

  // Latest values for stable callbacks.
  const st = useRef({ index, offset, total });
  st.current = { index, offset, total };

  const isPlaying = enabled && playing;
  const playSec = minuteSec == null ? null : minuteSec + (enabled ? offset : 59);

  const moveTo = useCallback(
    (p: { index: number; offset: number }) => {
      const prev = st.current.index;
      st.current = { ...st.current, offset: p.offset, index: p.index };
      setOffset(p.offset);
      if (p.index !== prev) setIndex(p.index);
    },
    [setIndex],
  );

  const pause = useCallback(() => setPlaying(false), []);

  // Disabling (or an empty session) stops our clock; disabling also restores the full-minute offset.
  useEffect(() => {
    if (!enabled || total === 0) {
      setPlaying(false);
      if (!enabled) setOffset(59);
    }
  }, [enabled, total]);

  // The engine started playing on its own (its Play button): hand the clock back to it.
  useEffect(() => {
    if (enabled && engineIsPlaying) setPlaying(false);
  }, [enabled, engineIsPlaying]);

  usePlayClock({
    playing: isPlaying && total > 0,
    speed,
    unitSec: 1,
    maxSpeed,
    onSteps: (n) => {
      const { index: i, offset: o, total: t } = st.current;
      const next = shiftSec({ index: i, offset: o }, n, t);
      moveTo(next);
      if (next.index >= t - 1 && next.offset >= 59) setPlaying(false);
    },
  });

  const play = useCallback(() => {
    const { index: i, offset: o, total: t } = st.current;
    if (t === 0) return;
    enginePause();
    // Replaying from the very end restarts from the first second.
    if (i >= t - 1 && o >= 59) moveTo({ index: 0, offset: 0 });
    setPlaying(true);
  }, [enginePause, moveTo]);

  const toggle = useCallback(() => {
    if (playing) pause();
    else play();
  }, [playing, pause, play]);

  const skip = useCallback(
    (dir: 1 | -1, sec: number) => {
      setPlaying(false);
      enginePause();
      const { index: i, offset: o, total: t } = st.current;
      moveTo(shiftSec({ index: i, offset: o }, dir * sec, t));
    },
    [enginePause, moveTo],
  );

  const stepFine = useCallback((dir: 1 | -1) => skip(dir, 1), [skip]);
  const stepMinute = useCallback((dir: 1 | -1) => skip(dir, 60), [skip]);

  const setPlaySec = useCallback(
    (sec: number) => {
      if (!indexOfMinute) return;
      setPlaying(false);
      enginePause();
      const { total: t } = st.current;
      if (t === 0) return;
      const minute = Math.floor(sec / 60) * 60;
      const idx = Math.max(0, Math.min(indexOfMinute(minute), t - 1));
      moveTo({ index: idx, offset: Math.max(0, Math.min(sec - minute, 59)) });
    },
    [indexOfMinute, enginePause, moveTo],
  );

  return {
    playSec,
    offset: enabled ? offset : 59,
    isPlaying,
    play,
    pause,
    toggle,
    stepFine,
    stepMinute,
    skip,
    setPlaySec,
  };
}
