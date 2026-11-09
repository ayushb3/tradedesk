/**
 * Self-check for Skip (pure). Run: npm run check:sim
 */
import { clampSkipSec, skipIndex, skipTargetSec, sizeLabel } from "./replaySkip";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const T = Date.parse("2026-03-13T17:00:00Z") / 1000; // a 5m and 1h boundary
const m = (n: number) => T + n * 60;

// Minute player (unit 60) on a 5m chart: candle [T, T+5m) closes on minute T+4.
eq("5m fwd mid-candle lands on its close", skipTargetSec(m(1), 300, 1, 60), m(4));
eq("5m fwd on a close goes to the next close", skipTargetSec(m(4), 300, 1, 60), m(9));
eq("5m back mid-candle goes to the previous close", skipTargetSec(m(2), 300, -1, 60), m(-1));
eq("5m back on a close goes one candle back", skipTargetSec(m(9), 300, -1, 60), m(4));
eq("1m chart: skip = one minute", [skipTargetSec(m(3), 60, 1, 60), skipTargetSec(m(3), 60, -1, 60)], [m(4), m(2)]);

// Seconds clock (unit 1) on a 15s chart.
eq("15s fwd mid-candle", skipTargetSec(T + 3, 15, 1, 1), T + 14);
eq("15s fwd on close", skipTargetSec(T + 14, 15, 1, 1), T + 29);
eq("15s back", skipTargetSec(T + 20, 15, -1, 1), T + 14);
// Seconds clock on a 1m / 5m chart (trade replay seconds mode).
eq("seconds on 5m chart closes on the last second", skipTargetSec(T + 61, 300, 1, 1), T + 299);
// A bar smaller than the unit (a 15s chart on a minute player) skips one unit.
eq("bar smaller than unit", skipTargetSec(m(2), 15, 1, 60), m(3));

// Seconds clock at the session edges: back from 9:30:20 must not land after the playhead.
eq("clamp before the first bar", clampSkipSec(T - 1, T, T + 3600), T);
eq("clamp past the last bar", clampSkipSec(T + 4000, T, T + 3600), T + 3659);
eq("clamp inside", clampSkipSec(T + 100, T, T + 3600), T + 100);
// Bar player with a gap: bars at 0,1,2,5,6 min; on bar 2 (minute 2), a 5m skip targets minute 4
// (no bar) -> would map back to bar 2; it must move on to bar 3.
const opens = [0, 1, 2, 5, 6].map(m);
eq("gap cannot stall forward", skipIndex(opens, 2, m(4), 1), 3);
eq("normal forward", skipIndex(opens, 0, m(4), 1), 2);
eq("back", skipIndex(opens, 4, m(-1), -1), 0);
eq("back always moves", skipIndex(opens, 3, m(5), -1), 2);
eq("end clamps", skipIndex(opens, 4, m(20), 1), 4);

eq("labels", [sizeLabel(15), sizeLabel(60), sizeLabel(300), sizeLabel(3600)], ["15s", "1m", "5m", "1h"]);

if (failures) {
  console.error(`${failures} replaySkip check(s) failed`);
  process.exit(1);
}
console.log("replaySkip checks passed");
