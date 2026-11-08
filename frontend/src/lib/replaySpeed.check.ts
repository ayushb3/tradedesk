/**
 * Self-check for replaySpeed.ts (pure numbers, no data).
 * Run: npm run check:sim
 */
import {
  advance, clampSpeed, DEFAULT_SPEED, engineFor, FAST_DEFAULT_SPEED, FAST_MAX_SPEED, formatSpeed, loadSpeed, MAX_SPEED, MIN_SPEED,
  minuteLabel, saveSpeed, secondsRes, snapSpeed, SPEED_STEPS, stepSpeed,
} from "./replaySpeed";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol;

// ---- steps: powers of two, 0.5x to 64x, default 1x
eq("steps", [...SPEED_STEPS], [0.5, 1, 2, 4, 8, 16, 32, 64]);
eq("range", [MIN_SPEED, MAX_SPEED], [0.5, 64]);
eq("default is 1x", DEFAULT_SPEED, 1);
eq("clamps out of range", [clampSpeed(0.01), clampSpeed(10000), clampSpeed(NaN)], [MIN_SPEED, MAX_SPEED, DEFAULT_SPEED]);

// ---- up doubles, down halves, clamped
eq("up from 4", stepSpeed(4, 1), 8);
eq("down from 4", stepSpeed(4, -1), 2);
eq("up from 1", stepSpeed(1, 1), 2);
eq("down from 1 -> 0.5", stepSpeed(1, -1), 0.5);
eq("down at min stays", stepSpeed(0.5, -1), 0.5);
eq("up at max stays", stepSpeed(64, 1), 64);
eq("up from off-step 60 snaps first", stepSpeed(60, 1), 64);
eq("down from off-step 60", stepSpeed(60, -1), 32);

// ---- snapping to the nearest step
eq("60 -> 64", snapSpeed(60), 64);
eq("100 -> 64", snapSpeed(100), 64);
eq("30 -> 32", snapSpeed(30), 32);
eq("5 -> 4", snapSpeed(5), 4);
eq("10 -> 8", snapSpeed(10), 8);
eq("every step is its own snap", SPEED_STEPS.map((s) => snapSpeed(s)), [...SPEED_STEPS]);

// ---- labels
eq("format", [formatSpeed(64), formatSpeed(0.5), formatSpeed(1)], ["64x", "0.5x", "1x"]);
eq("minute label 16x", minuteLabel(16), "1 min ≈ 3.8 s");
eq("minute label 1x", minuteLabel(1), "1 min ≈ 60 s");
eq("minute label 0.5x", minuteLabel(0.5), "1 min ≈ 2 min");
eq("minute label 64x", minuteLabel(64), "1 min ≈ 0.9 s");

// ---- engine and resolution
eq("seconds when available, any speed", [engineFor(1, true), engineFor(64, true)], ["seconds", "seconds"]);
eq("minutes when no seconds data", engineFor(2, false), "minutes");
eq("1s res at 64x", secondsRes(64, 50), "1s");
eq("5s res when a tick moves >= 5 s", secondsRes(100, 100), "5s");

// ---- the clock advances by speed x elapsed, carrying the remainder
let a = advance(0, 50, 1, 1); // 0.05 s owed
eq("1x: no step after 50 ms", a.steps, 0);
let total = a.steps;
for (let i = 0; i < 19; i++) {
  a = advance(a.carry, 50, 1, 1);
  total += a.steps;
}
eq("1x: one step after 1000 ms in 20 ticks", total, 1);
eq("1x: carry ~0 after a full second", near(a.carry, 0, 1e-6), true);
eq("32x: 1.6 chart-s per 50 ms tick", advance(0, 50, 32, 1).steps, 1);
eq("64x minute bars: 1 bar per 937.5 ms", advance(0, 938, 64, 60).steps, 1);
eq("a request above the cap plays at 64x", advance(0, 1000, 600, 1).steps, 64);
eq("late timer catches up, same speed", advance(0, 500, 64, 1).steps, 32);
eq("negative elapsed ignored", advance(0, -100, 64, 1).steps, 0);

// ---- whole-bar players (free replay, author): same doubling steps, a higher ceiling
eq("fast ceiling and start", [FAST_MAX_SPEED, FAST_DEFAULT_SPEED], [1024, 64]);
eq("fast: up from 64", stepSpeed(64, 1, FAST_MAX_SPEED), 128);
eq("fast: up at 1024 stays", stepSpeed(1024, 1, FAST_MAX_SPEED), 1024);
eq("fast: 15m bar at 1024x every ~0.9 s", advance(0, 879, 1024, 900, FAST_MAX_SPEED).steps, 1);
eq("default ceiling still 64", stepSpeed(64, 1), 64);
eq("fast format", formatSpeed(512, FAST_MAX_SPEED), "512x");

// ---- remembered speed survives a broken storage
const mem = new Map<string, string>();
const store = {
  getItem: (k: string) => mem.get(k) ?? null,
  setItem: (k: string, v: string) => {
    mem.set(k, v);
  },
};
eq("default when empty", loadSpeed(store), DEFAULT_SPEED);
saveSpeed(32, store);
eq("remembers", loadSpeed(store), 32);
mem.set("tradedesk.replay.speed", "600");
eq("a saved 600x snaps to 64x", loadSpeed(store), 64);
mem.set("tradedesk.replay.speed", "60");
eq("a saved 60x snaps to 64x", loadSpeed(store), 64);
mem.set("tradedesk.replay.speed", "5");
eq("a saved 5x snaps to 4x", loadSpeed(store), 4);
mem.set("tradedesk.replay.speed", "junk");
eq("junk -> default", loadSpeed(store), DEFAULT_SPEED);
const broken = {
  getItem: (): string | null => {
    throw new Error("blocked");
  },
  setItem: (): void => {
    throw new Error("blocked");
  },
};
eq("throwing storage -> default", loadSpeed(broken), DEFAULT_SPEED);
saveSpeed(32, broken); // must not throw
eq("throwing setItem is swallowed", true, true);

if (failures) {
  console.error(`${failures} replaySpeed check(s) failed`);
  process.exit(1);
}
console.log("replaySpeed: all checks passed");
