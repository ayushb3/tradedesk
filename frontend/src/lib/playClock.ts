/**
 * The one playback clock. While `playing`, it advances by `speed` x elapsed real time in
 * whole `unitSec` steps (a minute bar, a second), carrying the remainder so a slow timer never
 * changes the speed. Every player drives its playhead from this: trade/day replay (seconds or
 * minute bars), free replay, Learn drills and the scenario author.
 */
import { useEffect, useRef } from "react";

import { advance, CLOCK_TICK_MS } from "@/lib/replaySpeed";

export function usePlayClock({
  playing,
  speed,
  unitSec,
  onSteps,
  maxSpeed,
}: {
  playing: boolean;
  /** Chart-seconds per real second. */
  speed: number;
  /** Seconds per step (60 for minute bars, 1 for seconds). */
  unitSec: number;
  /** Called with the number of whole steps due (always > 0). */
  onSteps: (steps: number) => void;
  /** Speed ceiling (default 64x; only the scenario author goes higher). */
  maxSpeed?: number;
}): void {
  const speedRef = useRef(speed);
  speedRef.current = speed;
  const stepsRef = useRef(onSteps);
  stepsRef.current = onSteps;
  const maxRef = useRef(maxSpeed);
  maxRef.current = maxSpeed;
  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let carry = 0;
    const id = window.setInterval(() => {
      const now = performance.now();
      const r = advance(carry, now - last, speedRef.current, unitSec, maxRef.current);
      last = now;
      carry = r.carry;
      if (r.steps > 0) stepsRef.current(r.steps);
    }, CLOCK_TICK_MS);
    return () => window.clearInterval(id);
  }, [playing, unitSec]);
}
