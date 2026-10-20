/**
 * Self-check for secondsReplay.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { Candle } from "./api";
import {
  arrowDelta, barsUpTo, finishedOnly, formatClockSeconds, formingCandle, liveCandle, mergeBars,
  minuteIndexAt, newestTs, pollDue, replaySeries, stepSec, tickMs,
} from "./secondsReplay";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const BASE = Date.UTC(2026, 5, 19, 14, 0, 0) / 1000; // a whole minute
const at = (s: number) => new Date((BASE + s) * 1000).toISOString();
const bar = (s: number, o: number, h: number, l: number, c: number, v = 1, tf = "1s"): Candle => ({
  symbol: "X", ts: at(s), open: o, high: h, low: l, close: c, volume: v, timeframe: tf,
});

const secs = [
  bar(0, 10, 11, 9.5, 10.5, 2),
  bar(1, 10.5, 12, 10, 11, 3),
  bar(4, 11, 11.2, 8, 8.5, 1), // seconds 2 and 3 are empty
  bar(30, 8.5, 9, 8.4, 8.9, 5),
  bar(61, 9, 9.5, 9, 9.2, 4), // next minute
];

// ---- forming candle: the current minute fills in as the playhead advances
eq("nothing printed yet", formingCandle(secs, BASE, BASE - 1), null);
eq("first second", formingCandle(secs, BASE, BASE), { ...secs[0], ts: at(0), timeframe: "1m" });
const f4 = formingCandle(secs, BASE, BASE + 4)!;
eq("running OHLCV at +4s", [f4.open, f4.high, f4.low, f4.close, f4.volume], [10, 12, 8, 8.5, 6]);
const f3 = formingCandle(secs, BASE, BASE + 3)!;
eq("empty seconds add nothing", [f3.high, f3.low, f3.close], [12, 9.5, 11]);
const f45 = formingCandle(secs, BASE, BASE + 45)!;
eq("close is the latest close", f45.close, 8.9);
eq("a later minute's bars never count", formingCandle(secs, BASE, BASE + 60)!.close, 8.9);
eq("second minute starts from its own first second", formingCandle(secs, BASE + 60, BASE + 61)!.open, 9);
eq("forming candle is stamped at the minute, 1m", [f45.ts, f45.timeframe], [at(0), "1m"]);

// ---- no future: nothing after the playhead can come out of any helper
let leaks = 0;
for (let head = -2; head < 100; head++) {
  if (barsUpTo(secs, BASE + head).some((b) => Date.parse(b.ts) / 1000 > BASE + head)) leaks++;
  const series = replaySeries([], secs, BASE + head);
  const f = series[series.length - 1];
  if (f && head < 1 && f.high > 11) leaks++; // at +0 only the first second (high 11) may show
  if (f && head < 4 && f.low < 9.5) leaks++; // the 8.0 low prints at +4
}
eq("no helper returns a bar or price after the playhead", leaks, 0);

// ---- the series: finished minutes are the minute bars, the current minute is forming
const minutes: Candle[] = [
  bar(-60, 1, 2, 0.5, 1.5, 100, "1m"),
  bar(0, 10, 99, 1, 50, 100, "1m"), // the real minute bar: must NOT show while it forms
  bar(60, 9, 9.9, 8, 9.5, 100, "1m"),
];
const s30 = replaySeries(minutes, secs, BASE + 30);
eq("finished + forming", s30.map((c) => c.ts), [at(-60), at(0)]);
eq("forming replaces the real minute bar (no 99 high)", s30[1].high, 12);
eq("quiet start of a minute: only finished bars", replaySeries(minutes, [], BASE + 60).map((c) => c.ts), [at(-60), at(0)]);
eq("the playhead minute and later minute bars are dropped", replaySeries(minutes, secs, BASE - 30).map((c) => c.ts), []);

// ---- causal maps lose the playhead minute
const m = new Map<number, number>([[BASE - 60, 1], [BASE, 2], [BASE + 60, 3]]);
eq("finishedOnly", [...finishedOnly(m, BASE + 20).keys()], [BASE - 60]);

// ---- merging polls
const held = mergeBars([], [secs[1], secs[0]]);
eq("merge sorts", held.map((b) => b.ts), [at(0), at(1)]);
eq("merge dedupes", mergeBars(held, [secs[1], secs[2]]).map((b) => b.ts), [at(0), at(1), at(4)]);
eq("newest ts", newestTs(held), at(1));
eq("newest of nothing", newestTs([]), null);
eq("poll due when ahead", [pollDue(null, 5), pollDue(10, 11), pollDue(10, 10), pollDue(10, 3)], [true, true, false, false]);

// ---- clock: steps and keys
eq("step 1s", stepSec(100, 1, 0, 1000), 101);
eq("step back clamps at start", stepSec(2, -60, 0, 1000), 0);
eq("step forward clamps at end", stepSec(990, 60, 0, 1000), 1000);
eq(
  "arrow keys",
  [arrowDelta("ArrowRight", false), arrowDelta("ArrowLeft", false), arrowDelta("ArrowRight", true), arrowDelta("ArrowLeft", true)],
  [1, -1, 60, -60],
);
eq("speeds are multiples of real time", [tickMs(1), tickMs(2), tickMs(4)], [1000, 500, 250]);

// ---- readout and sim (14:00:07 UTC is 07:00:07 in Pacific daylight time)
eq("PT clock with seconds", formatClockSeconds(BASE + 7), "07:00:07");
eq(
  "minute index follows the playhead minute",
  [minuteIndexAt(minutes, BASE + 5), minuteIndexAt(minutes, BASE + 61), minuteIndexAt(minutes, BASE - 90)],
  [1, 2, 0],
);
const live = liveCandle(replaySeries(minutes, secs, BASE + 4), BASE + 4)!;
eq("sim price is the latest close, stamped at the playhead", [live.close, live.ts], [8.5, at(4)]);
eq("no price before the first print", liveCandle([], BASE), null);

if (failures) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("secondsReplay: all checks passed");
