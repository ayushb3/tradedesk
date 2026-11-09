/**
 * Self-check for Practice and group review (pure). Run: npm run check:sim
 * Synthetic trades and windows only.
 */
import {
  adjacentEntry,
  groupEntries,
  groupPnl,
  groupReplayHref,
  groupWindow,
  parseTradeIds,
  practiceHref,
  practiceSymbol,
  sessionGroups,
} from "./groupReview";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

// 2026-03-04 is PST (UTC-8): 09:00 PT = 17:00Z.
const at = (hhmm: string) =>
  `2026-03-04T${String(Number(hhmm.slice(0, 2)) + 8).padStart(2, "0")}:${hhmm.slice(3)}:00Z`;
const s = (hhmm: string) => Date.parse(at(hhmm)) / 1000;
const pos = (id: number, ticker: string, open: string, close: string, pnl: number) => ({
  trade_id: id,
  ticker,
  pnl,
  fills: [
    { side: "B" as const, qty: 1, px: 1, ts: at(open), ts_pt: open },
    { side: "S" as const, qty: 1, px: 1, ts: at(close), ts_pt: close },
  ],
});
const P = [
  pos(1, "SPY", "06:40", "06:50", 10),
  pos(2, "SPY", "07:10", "07:20", -5),
  pos(3, "SPXW", "10:10", "10:15", 3),
  pos(4, "SPXW", "11:15", "11:30", 7),
  pos(5, "SPXW", "11:40", "11:45", 2),
];
// Synthetic windows (placeholder times and labels).
const rules = {
  tz: "America/Los_Angeles",
  window: { start: "06:30", end: "08:00" },
  extra_windows: [{ start: "11:00", end: "12:00", label: "Late" }],
};
eq(
  "sessions from trade times, named by window, else by phase",
  sessionGroups(P, rules).map((g) => [g.label, g.range, g.tradeIds]),
  [
    ["Morning", "06:40–07:20", [1, 2]],
    ["Midday", "10:10–10:15", [3]],
    ["Late", "11:15–11:45", [4, 5]],
  ],
);
// No rules: phases by New York time (12:42 PT = 15:42 ET is the last 30 minutes).
const Q = [pos(6, "SPXW", "12:42", "12:43", 1), pos(7, "SPXW", "12:48", "12:50", 1), pos(8, "SPY", "11:15", "11:20", 1), pos(9, "SPY", "11:50", "11:55", 1)];
eq(
  "no rules: phases, a 20+ min pause splits, repeats numbered",
  sessionGroups(Q, null).map((g) => [g.label, g.tradeIds]),
  [
    ["Midday", [8]],
    ["Midday 2", [9]],
    ["MOC", [6, 7]],
  ],
);
eq("an open trade bridges a pause", sessionGroups([pos(1, "SPY", "06:40", "07:30", 1), pos(2, "SPY", "07:20", "07:25", 1), pos(3, "SPY", "07:45", "07:50", 1)], null).map((g) => g.tradeIds), [[1, 2, 3]]);

const w = groupWindow(P, [4, 5]);
eq("window: 5 min before first entry, 10 after last exit", w && [w.startSec, w.endSec], [s("11:10"), s("11:55")]);
eq("window: none", groupWindow(P, [99]), null);

const e = groupEntries(P, [5, 4]);
eq("entries in order", e, [s("11:15"), s("11:40")]);
eq("next entry", adjacentEntry(e, e[0], 1), e[1]);
eq("prev from inside the second entry's minute", adjacentEntry(e, e[1] + 30, -1), e[0]);
eq("no next after last", adjacentEntry(e, e[1], 1), null);

eq("pnl hidden until exit", groupPnl(P, [4, 5], s("11:29")), { pnl: 0, closed: 0, total: 2 });
eq("pnl after one exit", groupPnl(P, [4, 5], s("11:30")), { pnl: 7, closed: 1, total: 2 });
eq("pnl all", groupPnl(P, [4, 5], s("12:00")), { pnl: 9, closed: 2, total: 2 });

eq("practice symbol: the most-traded ticker", practiceSymbol(P), "SPXW");
eq("practice symbol default", practiceSymbol([]), "SPY");
eq(
  "practice href",
  practiceHref("2026-03-04", "SPY", "/days/2026-03-04"),
  "/replay?mode=symbol&date=2026-03-04&symbol=SPY&from=%2Fdays%2F2026-03-04",
);
eq(
  "group href",
  groupReplayHref("2026-03-04", [5, 4], "SPXW"),
  "/replay?day=2026-03-04&trades=4%2C5&symbol=SPXW&from=%2Fdays%2F2026-03-04",
);
eq("parse ids", [parseTradeIds("4,5,x,5,-1"), parseTradeIds(""), parseTradeIds("a")], [[4, 5], null, null]);

if (failures) {
  console.error(`${failures} groupReview check(s) failed`);
  process.exit(1);
}
console.log("groupReview checks passed");
