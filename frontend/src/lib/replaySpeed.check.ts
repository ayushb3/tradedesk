/**
 * Self-check for replaySpeed.ts (pure numbers, no data).
 * Run: npm run check:sim
 */
import {
  advance, clampSpeed, DEFAULT_SPEED, engineFor, formatSpeed, loadSpeed, MAX_SPEED, MIN_SPEED,
  minuteLabel, posToSpeed, saveSpeed, secondsRes, SLIDER_STEPS, snapSpeed, SPEED_NOTCHES, speedToPos, stepNotch,
} from "./replaySpeed";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}
const near = (a: number, b: number, tol = 1e-9) => Math.abs(a - b) <= tol;

// ---- log-scale slider mapping
eq("min at 0", speedToPos(MIN_SPEED), 0);
eq("max at end", speedToPos(MAX_SPEED), SLIDER_STEPS);
eq("pos 0 -> min", posToSpeed(0), MIN_SPEED);
eq("pos end -> max", posToSpeed(SLIDER_STEPS), MAX_SPEED);
eq("round trip 60x", snapSpeed(posToSpeed(speedToPos(60))), 60);
eq("round trip 1x", snapSpeed(posToSpeed(speedToPos(1))), 1);
eq("monotonic", speedToPos(2) < speedToPos(5) && speedToPos(5) < speedToPos(80), true);
eq("clamps out of range", [clampSpeed(0.01), clampSpeed(10000), clampSpeed(NaN)], [MIN_SPEED, MAX_SPEED, DEFAULT_SPEED]);

// ---- snapping
eq("snaps near a notch", snapSpeed(57), 60);
eq("snaps 1.1 to 1", snapSpeed(1.1), 1);
eq("free between notches", snapSpeed(18), 18);

// ---- [ and ] step notches
eq("] from 60", stepNotch(60, 1), 100);
eq("[ from 60", stepNotch(60, -1), 30);
eq("] from free 18", stepNotch(18, 1), 30);
eq("[ from free 18", stepNotch(18, -1), 10);
eq("[ from 1 -> 0.5", stepNotch(1, -1), 0.5);
eq("[ at min stays", stepNotch(0.5, -1), 0.5);
eq("] at max stays", stepNotch(100, 1), 100);
eq("cap is 100x", MAX_SPEED, 100);
eq("notches stop at 100", SPEED_NOTCHES[SPEED_NOTCHES.length - 1], 100);

// ---- labels
eq("format", [formatSpeed(60), formatSpeed(0.5), formatSpeed(2.5)], ["60x", "0.5x", "2.5x"]);
eq("minute label 30x", minuteLabel(30), "1 min ≈ 2 s");
eq("minute label 1x", minuteLabel(1), "1 min ≈ 60 s");
eq("minute label 0.5x", minuteLabel(0.5), "1 min ≈ 2 min");
eq("minute label 100x", minuteLabel(100), "1 min ≈ 0.6 s");

// ---- engine and resolution
eq("seconds below 60x", engineFor(30, true), "seconds");
eq("minutes at 60x", engineFor(60, true), "minutes");
eq("minutes when no seconds data", engineFor(2, false), "minutes");
eq("1s res at 30x", secondsRes(30, 50), "1s");
eq("5s res when a tick moves >= 5 s", secondsRes(100, 50), "5s");

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
eq("30x: 1.5 chart-s per 50 ms tick", advance(0, 50, 30, 1).steps, 1);
eq("30x: carry keeps the half", near(advance(0, 50, 30, 1).carry, 0.5, 1e-9), true);
eq("60x minute bars: 1 bar per real second", advance(0, 1000, 60, 60).steps, 1);
eq("100x minute bars: 1 bar per 600 ms", advance(0, 600, 100, 60).steps, 1);
eq("a request above the cap plays at 100x", advance(0, 600, 600, 60).steps, 1);
eq("late timer catches up, same speed", advance(0, 500, 60, 1).steps, 30);
eq("negative elapsed ignored", advance(0, -100, 60, 1).steps, 0);

// ---- remembered speed survives a broken storage
const mem = new Map<string, string>();
const store = {
  getItem: (k: string) => mem.get(k) ?? null,
  setItem: (k: string, v: string) => {
    mem.set(k, v);
  },
};
eq("default when empty", loadSpeed(store), DEFAULT_SPEED);
saveSpeed(30, store);
eq("remembers", loadSpeed(store), 30);
mem.set("tradedesk.replay.speed", "600");
eq("a saved 600x clamps to 100x", loadSpeed(store), 100);
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
saveSpeed(30, broken); // must not throw
eq("throwing setItem is swallowed", true, true);

if (failures) {
  console.error(`${failures} replaySpeed check(s) failed`);
  process.exit(1);
}
console.log("replaySpeed: all checks passed");
