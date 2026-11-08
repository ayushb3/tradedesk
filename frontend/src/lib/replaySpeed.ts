/**
 * Replay speed: one continuous, log-scale number, x = chart-seconds per real second
 * (60x = one chart-minute per second). Pure helpers, no DOM; the slider, both clocks and
 * the seconds/minutes switch all read these.
 */

export const MIN_SPEED = 0.5;
/** 100x is the fastest anyone can follow (owner, 2026-11-08: 600x was unwatchable). */
export const MAX_SPEED = 100;
export const DEFAULT_SPEED = 60;
/** Snap points on the slider (Shift-drag skips them). `[` / `]` step between them. */
export const SPEED_NOTCHES = [1, 2, 5, 10, 30, 60, 100] as const;
/** Below this speed the replay plays second bars (when the trade has them). */
export const SECONDS_BELOW = 60;
/** Slider resolution: positions 0..SLIDER_STEPS map onto MIN..MAX on a log scale. */
export const SLIDER_STEPS = 1000;
/** Clock tick, ms. The playhead advances by speed x elapsed time, not one step per tick. */
export const CLOCK_TICK_MS = 50;
/** A drag snaps to a notch within this distance, in log-speed units (~12%). */
const SNAP_LOG = 0.12;
const STORAGE_KEY = "tradedesk.replay.speed";

const LO = Math.log(MIN_SPEED);
const HI = Math.log(MAX_SPEED);

export const clampSpeed = (s: number): number =>
  Number.isFinite(s) ? Math.min(MAX_SPEED, Math.max(MIN_SPEED, s)) : DEFAULT_SPEED;

export function speedToPos(s: number): number {
  return Math.round(((Math.log(clampSpeed(s)) - LO) / (HI - LO)) * SLIDER_STEPS);
}

export function posToSpeed(pos: number): number {
  const p = Math.min(SLIDER_STEPS, Math.max(0, pos)) / SLIDER_STEPS;
  return tidy(Math.exp(LO + p * (HI - LO)));
}

/** Two significant figures, so a free (shift-dragged) speed reads cleanly. */
export function tidy(s: number): number {
  const c = clampSpeed(s);
  const mag = Math.pow(10, Math.floor(Math.log10(c)) - 1);
  return clampSpeed(Math.round(c / mag) * mag);
}

/** The nearest notch when within snapping distance, else the speed itself. */
export function snapSpeed(s: number): number {
  const c = clampSpeed(s);
  let best: number = c;
  let bestD = SNAP_LOG;
  for (const n of SPEED_NOTCHES) {
    const d = Math.abs(Math.log(c) - Math.log(n));
    if (d <= bestD) {
      best = n;
      bestD = d;
    }
  }
  return best;
}

/** The next notch up (dir 1) or down (-1); MIN_SPEED below the lowest notch. */
export function stepNotch(s: number, dir: 1 | -1): number {
  const c = clampSpeed(s);
  const stops = [MIN_SPEED, ...SPEED_NOTCHES];
  if (dir > 0) return stops.find((n) => n > c + 1e-9) ?? MAX_SPEED;
  for (let i = stops.length - 1; i >= 0; i--) if (stops[i] < c - 1e-9) return stops[i];
  return MIN_SPEED;
}

const trim = (n: number): string => (n >= 10 ? String(Math.round(n)) : String(Math.round(n * 10) / 10));

/** "60x", "2.5x", "0.5x". */
export const formatSpeed = (s: number): string => `${trim(clampSpeed(s))}x`;

/** "1 min ≈ 2 s" at 30x; "1 min ≈ 0.6 s" at 100x; "1 min ≈ 2 min" at 0.5x. */
export function minuteLabel(s: number): string {
  const real = 60 / clampSpeed(s);
  return real > 60 ? `1 min ≈ ${trim(real / 60)} min` : `1 min ≈ ${trim(real)} s`;
}

/** Which clock plays at this speed: second bars below 60x when available, else minute bars. */
export function engineFor(s: number, secondsAvailable: boolean): "seconds" | "minutes" {
  return clampSpeed(s) < SECONDS_BELOW && secondsAvailable ? "seconds" : "minutes";
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

/** The last speed used (a saved speed above the cap reads as the cap), or the default. Storage may be missing or blocked. */
export function loadSpeed(storage: Pick<Storage, "getItem"> | null = safeStorage()): number {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    const n = raw == null ? NaN : Number(raw);
    return Number.isFinite(n) && n > 0 ? clampSpeed(n) : DEFAULT_SPEED;
  } catch {
    return DEFAULT_SPEED;
  }
}

export function saveSpeed(s: number, storage: Pick<Storage, "setItem"> | null = safeStorage()): void {
  try {
    storage?.setItem(STORAGE_KEY, String(clampSpeed(s)));
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
