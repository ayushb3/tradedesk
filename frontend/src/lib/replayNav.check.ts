/**
 * Self-check for replayNav.ts (synthetic times only).
 * Run: npm run check:sim
 */
import { exitTarget, isDay, rthWindow, safeFrom, tradeReplayHref } from "./replayNav";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

// ---- from= is an in-app path only
eq("plain path", safeFrom("/journal/2026-06-10"), "/journal/2026-06-10");
eq("path with query", safeFrom("/trades/3?tab=review"), "/trades/3?tab=review");
eq("other origin", safeFrom("https://example.com"), null);
eq("protocol-relative", safeFrom("//example.com/x"), null);
eq("backslash trick", safeFrom("/\\example.com"), null);
eq("control chars", safeFrom("/a\nb"), null);
eq("empty", [safeFrom(""), safeFrom(null), safeFrom(undefined)], [null, null, null]);

// ---- Exit
eq("exit to from", exitTarget("/journal/2026-06-10", 7), "/journal/2026-06-10");
eq("exit to the trade", exitTarget(null, 7), "/trades/7");
eq("exit to the picker", exitTarget(null, null), null);

// ---- links
eq("trade link keeps from", tradeReplayHref(7, "/journal/2026-06-10"), "/replay?trade=7&lead=15&from=%2Fjournal%2F2026-06-10");
eq("trade link drops a bad from", tradeReplayHref(7, "https://x.y", 5), "/replay?trade=7&lead=5");
eq("day param", [isDay("2026-06-10"), isDay("2026-6-10"), isDay(null)], [true, false, false]);

// ---- day window: 15 min before the 09:30 ET open to the last bar before 16:00 ET
const at = (h: number, m: number) => Date.UTC(2026, 5, 10, h, m) / 1000; // June: ET = UTC-4
const bars: number[] = [];
for (let t = at(8, 0); t <= at(23, 59); t += 60) bars.push(t); // 04:00 to 19:59 ET
eq("rth window", rthWindow(bars), { startSec: at(13, 15), endSec: at(19, 59) });
eq("rth window, 5 min lead", rthWindow(bars, 5)?.startSec, at(13, 25));
const pre = bars.filter((t) => t < at(13, 0)); // premarket only
eq("no regular hours -> whole session", rthWindow(pre), { startSec: pre[0], endSec: pre[pre.length - 1] });
eq("no bars", rthWindow([]), null);

if (failures) {
  console.error(`${failures} replayNav check(s) failed`);
  process.exit(1);
}
console.log("replayNav: all checks passed");
