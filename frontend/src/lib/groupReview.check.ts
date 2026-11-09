/**
 * Self-check for Practice and group review (pure). Run: npm run check:sim
 * Synthetic trades and windows only.
 */
import {
  adjacentEntry,
  ceilMinute,
  isPracticeDate,
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
  pos(1, "SPY", "05:20", "05:30", 10),
  pos(2, "SPY", "05:50", "06:00", -5),
  pos(3, "QQQ", "08:10", "08:15", 3),
  pos(4, "QQQ", "09:45", "10:00", 7),
  pos(5, "QQQ", "10:10", "10:15", 2),
];
// Synthetic windows and labels (not anyone's real rules).
const rules = {
  tz: "America/Los_Angeles",
  window: { start: "05:10", end: "06:20" },
  extra_windows: [{ start: "09:35", end: "10:25", label: "Late" }],
};
eq(
  "sessions from trade times, named by window, else by phase",
  sessionGroups(P, rules).map((g) => [g.label, g.range, g.tradeIds]),
  [
    ["Morning", "05:20–06:00", [1, 2]],
    ["Midday", "08:10–08:15", [3]],
    ["Late", "09:45–10:15", [4, 5]],
  ],
);
// No rules: phases by New York time (12:36 PT = 15:36 ET is in the last 30 minutes).
const Q = [
  pos(6, "SPY", "12:36", "12:37", 1),
  pos(7, "SPY", "12:39", "12:41", 1),
  pos(8, "SPY", "10:55", "11:00", 1),
  pos(9, "SPY", "11:30", "11:35", 1),
];
eq(
  "no rules: phases, a 20+ min pause splits, repeats numbered",
  sessionGroups(Q, null).map((g) => [g.label, g.tradeIds]),
  [
    ["Midday", [8]],
    ["Midday 2", [9]],
    ["MOC", [6, 7]],
  ],
);
eq(
  "an open trade bridges a pause",
  sessionGroups([pos(1, "SPY", "05:20", "06:10", 1), pos(2, "SPY", "06:00", "06:05", 1), pos(3, "SPY", "06:25", "06:30", 1)], null).map(
    (g) => g.tradeIds,
  ),
  [[1, 2, 3]],
);
// DST: 2026-07-01 is PDT (UTC-7); 05:20 PDT = 12:20Z, inside the 05:10 window.
eq(
  "summer time",
  sessionGroups([{ trade_id: 1, fills: [{ side: "B" as const, qty: 1, px: 1, ts: "2026-07-01T12:20:00Z", ts_pt: "" }] }], rules).map((g) => [g.label, g.range]),
  [["Morning", "05:20–05:20"]],
);

const w = groupWindow(P, [4, 5]);
eq("window: 5 min before first entry, 10 after last exit", w && [w.startSec, w.endSec], [s("09:40"), s("10:25")]);
eq("window: none", groupWindow(P, [99]), null);

const e = groupEntries(P, [5, 4]);
eq("entries in order", e, [s("09:45"), s("10:10")]);
eq("next entry", adjacentEntry(e, e[0], 1), e[1]);
eq("prev from just after the second entry", adjacentEntry(e, e[1] + 30, -1), e[1]);
eq("prev from on the second entry", adjacentEntry(e, e[1], -1), e[0]);
eq("no next after last", adjacentEntry(e, e[1], 1), null);
// Minute player: a mid-minute entry lands on the next bar open; that bar is "on" the entry.
const mid = [s("09:45") + 30, s("10:10") + 30];
eq("minute landing: next from the landed bar", adjacentEntry(mid, ceilMinute(mid[0]), 1, ceilMinute), mid[1]);
eq("minute landing: prev from the landed bar", adjacentEntry(mid, ceilMinute(mid[1]), -1, ceilMinute), mid[0]);
eq("minute landing: next from before", adjacentEntry(mid, s("09:45"), 1, ceilMinute), mid[0]);

eq("pnl hidden until exit", groupPnl(P, [4, 5], s("09:59")), { pnl: 0, closed: 0, total: 2 });
eq("pnl after one exit", groupPnl(P, [4, 5], s("10:00")), { pnl: 7, closed: 1, total: 2 });
eq("pnl all", groupPnl(P, [4, 5], s("10:30")), { pnl: 9, closed: 2, total: 2 });

eq("practice symbol: the most-traded option root", practiceSymbol(P), "QQQ");
eq("practice symbol: stocks fall back to SPY", practiceSymbol([{ ticker: "NVDA" }]), "SPY");
eq("practice symbol: SPX practises on SPXW", practiceSymbol([{ ticker: "SPX" }]), "SPXW");
eq("practice symbol default", practiceSymbol([]), "SPY");
const today = new Date("2026-03-04T20:00:00Z");
eq(
  "practice date validation",
  ["2026-03-04", "2026-03-07", "2026-13-45", "2026-02-30", "2026-03-05", "x"].map((d) => isPracticeDate(d, today)),
  [true, false, false, false, false, false],
);
eq(
  "practice href",
  practiceHref("2026-03-04", "SPY", "/days/2026-03-04"),
  "/replay?mode=symbol&date=2026-03-04&symbol=SPY&from=%2Fdays%2F2026-03-04",
);
eq(
  "group href",
  groupReplayHref("2026-03-04", [5, 4], "QQQ"),
  "/replay?day=2026-03-04&trades=4%2C5&symbol=QQQ&from=%2Fdays%2F2026-03-04",
);
eq("parse ids", [parseTradeIds("4,5,x,5,-1"), parseTradeIds(""), parseTradeIds("a")], [[4, 5], null, null]);

if (failures) {
  console.error(`${failures} groupReview check(s) failed`);
  process.exit(1);
}
console.log("groupReview checks passed");
