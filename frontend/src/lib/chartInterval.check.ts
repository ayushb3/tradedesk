/**
 * Self-check for the Interval roll-up (synthetic bars only).
 * Run: npm run check:sim
 */
import type { Candle } from "./api";
import { bucketOf, rollUp, rollUpLine } from "./chartInterval";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const T0 = Date.UTC(2026, 0, 2, 14, 30) / 1000; // a 5m boundary
const bar = (i: number, o: number, h: number, l: number, c: number): Candle => ({
  symbol: "TEST",
  ts: new Date((T0 + i * 60) * 1000).toISOString(),
  open: o,
  high: h,
  low: l,
  close: c,
  volume: 10,
  timeframe: "1m",
});
// 7 minute bars: 14:30 … 14:36
const bars = [
  bar(0, 10, 11, 9, 10.5),
  bar(1, 10.5, 12, 10, 11),
  bar(2, 11, 11.5, 8, 9),
  bar(3, 9, 10, 8.5, 9.5),
  bar(4, 9.5, 10, 9, 9.8),
  bar(5, 9.8, 13, 9.7, 12),
  bar(6, 12, 12.5, 11, 11.2),
];

eq("bucket on the clock", [bucketOf(T0 + 299, 300), bucketOf(T0 + 300, 300)], [T0, T0 + 300]);

const all = rollUp(bars, 300, T0 + 6 * 60);
eq("two 5m bars", all.length, 2);
eq("first bar OHLC", [all[0].open, all[0].high, all[0].low, all[0].close], [10, 12, 8, 9.8]);
eq("first bar opens at 14:30", all[0].ts, new Date(T0 * 1000).toISOString());
eq("forming bar so far", [all[1].open, all[1].high, all[1].low, all[1].close], [9.8, 13, 9.7, 11.2]);
eq("volume sums", all[0].volume, 50);

// No lookahead: at a 14:32 playhead the 5m bar holds only 14:30-14:32.
const early = rollUp(bars, 300, T0 + 2 * 60);
eq("forming bar stops at the playhead", [early.length, early[0].high, early[0].low, early[0].close], [1, 12, 8, 9]);
eq("nothing after the playhead", rollUp(bars, 300, T0 - 1), []);

const line = new Map<number, number>(bars.map((_, i) => [T0 + i * 60, i]));
eq("line: value at each bar's last minute", [...rollUpLine(line, 300, T0 + 6 * 60)], [[T0, 4], [T0 + 300, 6]]);
eq("line: stops at the playhead", [...rollUpLine(line, 300, T0 + 2 * 60)], [[T0, 2]]);

if (failures) {
  console.error(`${failures} chartInterval check(s) failed`);
  process.exit(1);
}
console.log("chartInterval: all checks passed");
