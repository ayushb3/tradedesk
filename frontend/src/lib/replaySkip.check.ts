/**
 * Self-check for Skip (pure). Run: npm run check:sim
 */
import { skipTargetSec, sizeLabel } from "./replaySkip";

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

eq("labels", [sizeLabel(15), sizeLabel(60), sizeLabel(300), sizeLabel(3600)], ["15s", "1m", "5m", "1h"]);

if (failures) {
  console.error(`${failures} replaySkip check(s) failed`);
  process.exit(1);
}
console.log("replaySkip checks passed");
