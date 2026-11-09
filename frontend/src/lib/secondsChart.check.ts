/**
 * Self-check for the seconds chart helpers (synthetic bars only).
 * Run: npm run check:sim
 */
import type { Candle } from "./api";
import { CHUNK_SEC, chunksFor, chunkStart, resSecOf, rollUpSeconds, shiftSec } from "./secondsChart";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const T0 = Date.UTC(2026, 2, 13, 20, 0) / 1000; // a 30-minute boundary
const sec = (i: number, p = 100 + i): Candle => ({
  symbol: "TEST",
  ts: new Date((T0 + i) * 1000).toISOString(),
  open: p,
  high: p + 1,
  low: p - 1,
  close: p + 0.5,
  volume: 2,
  timeframe: "1s",
});
const bars = Array.from({ length: 60 }, (_, i) => sec(i));

eq("chunkStart on boundary", chunkStart(T0), T0);
eq("chunkStart mid", chunkStart(T0 + 1799), T0);
eq("chunkStart next", chunkStart(T0 + 1800), T0 + 1800);
eq("chunksFor current + previous", chunksFor(T0 + 100), [
  { start: T0 - CHUNK_SEC, end: T0 },
  { start: T0, end: T0 + CHUNK_SEC },
]);
eq("resSecOf", [resSecOf("5s"), resSecOf("15s"), resSecOf("30s")], [5, 15, 30]);

const r5 = rollUpSeconds(bars, 5, T0 + 59);
eq("5s count", r5.length, 12);
eq("5s first ohlc", [r5[0].open, r5[0].high, r5[0].low, r5[0].close, r5[0].volume], [100, 105, 99, 104.5, 10]);
eq("5s timeframe/ts", [r5[1].timeframe, r5[1].ts], ["5s", new Date((T0 + 5) * 1000).toISOString()]);
const sums = [5, 15, 30].map((r) => rollUpSeconds(bars, r, T0 + 59).reduce((a, b) => a + b.volume, 0));
eq("volume sums equal across res", sums, [120, 120, 120]);

const cut = rollUpSeconds(bars, 15, T0 + 20);
eq("forming bar at cut count", cut.length, 2);
eq("forming bar holds only <= cut", [cut[1].close, cut[1].volume, cut[1].high], [120.5, 12, 121]);
eq("no bar beyond cut", rollUpSeconds(bars, 5, T0 + 4).length, 1);

const messy = [...bars.slice(30), ...bars.slice(0, 40)].reverse();
eq("unsorted+duplicates equal clean", rollUpSeconds(messy, 5, T0 + 59), r5);
eq("empty input", rollUpSeconds([], 5, T0), []);
eq("all after cut", rollUpSeconds(bars, 5, T0 - 1), []);

eq("shiftSec plain", shiftSec({ index: 2, offset: 10 }, 5, 10), { index: 2, offset: 15 });
eq("shiftSec carry fwd", shiftSec({ index: 2, offset: 59 }, 1, 10), { index: 3, offset: 0 });
eq("shiftSec carry back", shiftSec({ index: 3, offset: 0 }, -1, 10), { index: 2, offset: 59 });
eq("shiftSec multi-minute", shiftSec({ index: 1, offset: 30 }, 150, 10), { index: 4, offset: 0 });
eq("shiftSec clamp start", shiftSec({ index: 0, offset: 3 }, -10, 10), { index: 0, offset: 0 });
eq("shiftSec clamp end", shiftSec({ index: 9, offset: 50 }, 100, 10), { index: 9, offset: 59 });
eq("shiftSec empty", shiftSec({ index: 0, offset: 5 }, 3, 0), { index: 0, offset: 0 });

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
