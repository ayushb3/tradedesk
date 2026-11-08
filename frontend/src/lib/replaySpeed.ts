/**
 * Replay speed: x = chart-seconds per real second (60x would be one chart-minute per second).
 * Speeds are powers of two, 0.5x to 64x: ↑ doubles, ↓ halves (owner, 2026-11-08: "each step
 * should only double or halve"). Pure helpers, no DOM; the stepper and both clocks read these.
 */

/** Every speed the replay plays at, slowest first. */
export const SPEED_STEPS = [0.5, 1, 2, 4, 8, 16, 32, 64] as const;
export const MIN_SPEED = SPEED_STEPS[0];
/** 64x is the top (owner: ~100x is the fastest he would use). */
export const MAX_SPEED = SPEED_STEPS[SPEED_STEPS.length - 1];
/** A fresh replay (no saved speed) starts at real time. */
export const DEFAULT_SPEED = 1;
/** Clock tick, ms. The playhead advances by speed x elapsed time, not one step per tick. */
export const CLOCK_TICK_MS = 50;
const STORAGE_KEY = "tradedesk.replay.speed";

export const clampSpeed = (s: number): number =>
  Number.isFinite(s) ? Math.min(MAX_SPEED, Math.max(MIN_SPEED, s)) : DEFAULT_SPEED;

/** The nearest step (on a log scale, so 3x -> 4x and 0.7x -> 0.5x... ties go up). */
export function snapSpeed(s: number): number {
  const c = Math.log2(clampSpeed(s));
  return clampSpeed(Math.pow(2, Math.round(c + 1e-9)));
}

/** Double (dir 1) or halve (-1), clamped at the ends. */
export function stepSpeed(s: number, dir: 1 | -1): number {
  return snapSpeed(snapSpeed(s) * (dir > 0 ? 2 : 0.5));
}

const trim = (n: number): string => (n >= 10 ? String(Math.round(n)) : String(Math.round(n * 10) / 10));

/** "64x", "1x", "0.5x". */
export const formatSpeed = (s: number): string => `${trim(clampSpeed(s))}x`;

/** "1 min ≈ 4 s" at 16x; "1 min ≈ 0.9 s" at 64x; "1 min ≈ 2 min" at 0.5x. */
export function minuteLabel(s: number): string {
  const real = 60 / clampSpeed(s);
  return real > 60 ? `1 min ≈ ${trim(real / 60)} min` : `1 min ≈ ${trim(real)} s`;
}

/**
 * Which clock plays: second bars whenever the trade has them (at every speed, so Shift+arrow
 * can always step one second), else minute bars.
 */
export function engineFor(_s: number, secondsAvailable: boolean): "seconds" | "minutes" {
  return secondsAvailable ? "seconds" : "minutes";
}

/** Second-bar resolution to ask for: 5s bars once a clock tick moves >= 5 chart-seconds. */
export function secondsRes(s: number, tickMs: number = CLOCK_TICK_MS): "1s" | "5s" {
  return (clampSpeed(s) * tickMs) / 1000 >= 5 ? "5s" : "1s";
}

/**
 * Advance a clock: `carry` chart-seconds already owed plus `elapsedMs` of real time at
 * `speed`, paid out in whole `unitSec` steps (1 for the seconds clock, 60 for minute bars).
 * The remainder carries to the next tick, so a slow or late timer never changes the speed.
 */
export function advance(
  carry: number,
  elapsedMs: number,
  speed: number,
  unitSec: number,
): { steps: number; carry: number } {
  const owed = carry + (Math.max(0, elapsedMs) / 1000) * clampSpeed(speed);
  const steps = Math.floor(owed / unitSec + 1e-9);
  return { steps, carry: Math.max(0, owed - steps * unitSec) };
}

/** The last speed used, snapped to a step (an old 60x reads as 64x), or the default. Storage may be missing or blocked. */
export function loadSpeed(storage: Pick<Storage, "getItem"> | null = safeStorage()): number {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) && n > 0 ? snapSpeed(n) : DEFAULT_SPEED;
  } catch {
    return DEFAULT_SPEED;
  }
}

export function saveSpeed(s: number, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, String(snapSpeed(s)));
  } catch {
    // private mode or a full quota: the speed just is not remembered
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
