/**
 * Self-check for priceChart.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import {
  formatChange,
  legendBar,
  timeframeLabel,
  AUTOSCALE_PAD,
  MAX_BAR_SPACING,
  chipWidth,
  clampRange,
  defaultRange,
  fillChipText,
  fillDetailText,
  fillValueText,
  fillsInTimeOrder,
  orderTypeLabel,
  spreadTags,
  fitWindow,
  indexOfSec,
  indexRange,
  minVisibleBars,
  paddedRange,
  placeChips,
  rightLimit,
  followShift,
  latestInView,
  valueAtOrBefore,
  quoteAtSec,
  formatBidAsk,
  RIGHT_MARGIN_BARS,
} from "./priceChart";
import { zoomRange } from "./reviewChart";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else {
    console.log(`ok   ${name}`);
  }
}
function truth(name: string, cond: boolean, detail = "") {
  if (!cond) {
    failures++;
    console.error(`FAIL ${name} ${detail}`);
  } else console.log(`ok   ${name}`);
}

const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);

// ---- playhead clamp (replay): the right edge never passes the playhead bar
const minBars = minVisibleBars(1000);
eq("min bars follows plot width", minBars, Math.ceil(1000 / MAX_BAR_SPACING));
const limReplay = rightLimit({ replay: true, playIdx: 60, lastIdx: 100, minBars });
eq("replay limit is the playhead", limReplay, 60);
eq("replay limit never past the timeline", rightLimit({ replay: true, playIdx: 200, lastIdx: 100, minBars }), 100);
eq("review limit is the whole window", rightLimit({ replay: false, playIdx: 10, lastIdx: 100, minBars }), 100);
const panned = clampRange({ from: 50, to: 90 }, { rightLimit: limReplay, minBars });
eq("pan past the playhead stops at it, width kept", panned, { from: 20, to: 60 });
const zoomedOut = clampRange({ from: -30, to: 140 }, { rightLimit: limReplay, minBars });
eq("zoom out cannot exceed lead start .. playhead", zoomedOut, { from: 0, to: 60 });
const zoomedIn = clampRange({ from: 30, to: 32 }, { rightLimit: limReplay, minBars });
truth("zoom in keeps the minimum bar count", zoomedIn.to - zoomedIn.from >= minBars - 1, JSON.stringify(zoomedIn));
eq("pan left of the lead start stops at it", clampRange({ from: -10, to: 30 }, { rightLimit: 60, minBars }), { from: 0, to: 40 });
// few bars played: the view is wide enough to keep spacing sane, blank to the right
const early = rightLimit({ replay: true, playIdx: 5, lastIdx: 100, minBars });
eq("early replay: view is minBars wide", early, minBars - 1);
eq("early replay: cannot pan", clampRange({ from: 3, to: 3 + early }, { rightLimit: early, minBars }), { from: 0, to: early });
eq("default replay view = start .. limit", defaultRange({ rightLimit: limReplay }), { from: 0, to: 60 });
// zoom (buttons / wheel) goes through the same limit
const z = zoomRange({ from: 0, to: 60 }, 60, 1 / 1.5, limReplay, minBars);
truth("zoom in on the right edge stays at the playhead", z.to <= 60 && z.to > 59.99, JSON.stringify(z));
const zo = zoomRange({ from: 20, to: 60 }, 60, 3, limReplay, minBars);
eq("zoom out stops at the full played range", zo, { from: 0, to: 60 });

// ---- autoscale of a narrow band: tight, not a fixed 700-800 band
const narrow = paddedRange({ min: 730, max: 735 })!;
truth("narrow band low is just under 730", narrow.minValue > 729 && narrow.minValue < 730, JSON.stringify(narrow));
truth("narrow band high is just over 735", narrow.maxValue > 735 && narrow.maxValue < 736, JSON.stringify(narrow));
eq("padding is a few percent of the range", +(735 + 5 * AUTOSCALE_PAD).toFixed(4), +narrow.maxValue.toFixed(4));
const flat = paddedRange({ min: 100, max: 100 })!;
truth("flat bars still get a non-empty range", flat.maxValue > flat.minValue);
eq("no bars, no range", paddedRange(null), null);
const withStop = paddedRange({ min: 1.2, max: 1.5 }, [1.0, null])!;
truth("an extra level (stop) widens the range", withStop.minValue < 1.0, JSON.stringify(withStop));

// ---- premium never goes below 0
const nearZero = paddedRange({ min: 0.01, max: 0.3 }, [], { floorZero: true })!;
eq("premium floor at 0", nearZero.minValue, 0);
const lowStop = paddedRange({ min: 0.05, max: 0.4 }, [0], { floorZero: true })!;
eq("premium floor with a zero stop", lowStop.minValue, 0);
truth("stock price is not floored", paddedRange({ min: 0.02, max: 0.3 })!.minValue < 0.02);

// ---- default window fits the data that exists
const entry = sec("2026-01-05T15:00:30Z");
const exit = sec("2026-01-05T15:10:10Z");
const first = sec("2026-01-05T14:00:00Z");
const full = fitWindow({ entrySec: entry, exitSec: exit, firstBarSec: first, lastBarSec: sec("2026-01-05T21:00:00Z") });
eq("window start = entry - 15m", full.startSec, sec("2026-01-05T14:45:00Z"));
eq("window end = exit + 30m", full.endSec, sec("2026-01-05T15:40:00Z"));
eq("full data: no note", full.noDataAfterSec, null);
const shortData = fitWindow({ entrySec: entry, exitSec: exit, firstBarSec: first, lastBarSec: sec("2026-01-05T15:22:00Z") });
eq("bars end early: window ends at the last bar", shortData.endSec, sec("2026-01-05T15:22:00Z"));
eq("bars end early: note says where", shortData.noDataAfterSec, sec("2026-01-05T15:22:00Z"));
const openTrade = fitWindow({ entrySec: entry, exitSec: null, firstBarSec: first, lastBarSec: sec("2026-01-05T15:22:00Z") });
eq("open trade runs to the last bar, no note", [openTrade.endSec, openTrade.noDataAfterSec], [sec("2026-01-05T15:22:00Z"), null]);
const early2 = fitWindow({ entrySec: sec("2026-01-05T14:05:00Z"), exitSec: sec("2026-01-05T14:08:00Z"), firstBarSec: first, lastBarSec: sec("2026-01-05T21:00:00Z") });
eq("start clamped to the first bar", early2.startSec, first);
// index helpers on a minute grid
eq("index of a minute", indexOfSec(full.startSec, entry, 100), 15);
eq("index clamps", indexOfSec(full.startSec, full.startSec - 600, 100), 0);
eq("fit range indices", indexRange(full.startSec, full.startSec, full.endSec, 55), { from: 0, to: 55 });

// ---- fill chips: side + time only, no collisions, nothing clipped
eq("chip text is side and time", fillChipText("buy", "06:34 PT"), "B 06:34 PT");
eq("sell chip text", fillChipText("sell", "06:36 PT"), "S 06:36 PT");
const w = chipWidth("B 06:34 PT");
const near = placeChips([{ x: 100, w }, { x: 112, w }], 800);
truth("close chips go to different lanes", near[0].lane !== near[1].lane, JSON.stringify(near));
const apart = placeChips([{ x: 100, w }, { x: 400, w }], 800);
eq("far chips share a lane", [apart[0].lane, apart[1].lane], [0, 0]);
const edge = placeChips([{ x: 790, w }], 800);
truth("chip near the right edge flips left, inside the strip", edge[0].left + w <= 800 && edge[0].left < 790, JSON.stringify(edge));
const leftEdge = placeChips([{ x: -2, w }], 800);
truth("chip at the left edge stays inside", leftEdge[0].left >= 0);
const many = placeChips([{ x: 50, w }, { x: 60, w }, { x: 70, w }], 800);
const overlap = (a: number, b: number) => Math.abs(many[a].left - many[b].left) < w && many[a].lane === many[b].lane;
truth("no two chips overlap", !overlap(0, 1) && !overlap(0, 2) && !overlap(1, 2), JSON.stringify(many));

// ---- fill details: list rows and the hover readout ------------------------------
eq("orderTypeLabel limit", orderTypeLabel("limit"), "LMT");
eq("orderTypeLabel market", orderTypeLabel("market"), "MKT");
eq("orderTypeLabel stop", orderTypeLabel("stop"), "STOP");
eq("orderTypeLabel unknown", orderTypeLabel(null), "");
eq("orderTypeLabel settlement", orderTypeLabel("settlement"), "SETL");
eq("orderTypeLabel trailing", orderTypeLabel("trailing_stop"), "TSTP");
eq("orderTypeLabel other", orderTypeLabel("market_on_close"), "MARK");
{
  const out = spreadTags([
    { x: 100, y: 50, side: "buy" as const },
    { x: 104, y: 52, side: "buy" as const },
    { x: 300, y: 50, side: "sell" as const },
    { x: 302, y: 50, side: "sell" as const },
  ]);
  eq("spreadTags first buy stays", out[0].y, 50);
  eq("spreadTags second buy steps down", out[1].y, 70);
  eq("spreadTags first sell stays", out[2].y, 50);
  eq("spreadTags second sell steps up", out[3].y, 32);
  const edge = spreadTags([{ x: 0, y: 190, side: "buy" as const }, { x: 0, y: 190, side: "buy" as const }], 200);
  eq("spreadTags flips at the bottom edge", edge[1].y, 172);
  eq("spreadTags buy and sell at one point stay", spreadTags([{ x: 0, y: 50, side: "buy" as const }, { x: 0, y: 50, side: "sell" as const }])[1].y, 50);
  eq("spreadTags zero height ignored", spreadTags([{ x: 0, y: 50, side: "buy" as const }, { x: 0, y: 50, side: "buy" as const }], 0)[1].y, 68);
  eq("spreadTags far apart untouched", spreadTags([{ x: 0, y: 0, side: "buy" as const }, { x: 200, y: 0, side: "buy" as const }])[1].y, 0);
}
eq("fillValueText whole", fillValueText(250), "$250");
eq("fillValueText cents", fillValueText(1000.5), "$1,000.50");
eq("fillValueText missing (older backend)", fillValueText(undefined), "—");
eq(
  "fillDetailText with type",
  fillDetailText({ side: "buy", qty: 2, price: 1.25, order_type: "limit" }, "07:30:05 PT"),
  "B 2 @ 1.25 07:30:05 PT LMT",
);
eq(
  "fillDetailText no price, no type",
  fillDetailText({ side: "sell", qty: 3, price: 0.9, order_type: null }, "07:45:00 PT", { price: false }),
  "S 3 07:45:00 PT",
);
eq(
  "fillsInTimeOrder: same-minute fills stay separate, by time then seq",
  fillsInTimeOrder([
    { seq: 3, ts: "2026-06-10T14:45:00Z" },
    { seq: 2, ts: "2026-06-10T14:30:41Z" },
    { seq: 1, ts: "2026-06-10T14:30:05Z" },
    { seq: 0, ts: "2026-06-10T14:30:41Z" },
  ]).map((f) => f.seq),
  [1, 0, 2, 3],
);

// ---- legend: OHLC + change for the hovered (or playhead) bar, never past the playhead
{
  const bars = [
    { ts: "2026-06-10T13:30:00Z", open: 1, high: 1.2, low: 0.9, close: 1.0 },
    { ts: "2026-06-10T13:31:00Z", open: 1, high: 1.3, low: 1.0, close: 1.1 },
  ];
  const t0 = Date.parse(bars[0].ts) / 1000;
  const t1 = t0 + 60;
  eq("legend: first bar has no change", legendBar(bars, t0, t1)?.change, null);
  eq("legend: change from previous close", legendBar(bars, t1, t1)?.change?.toFixed(2), "0.10");
  eq("legend: pct", legendBar(bars, t1, t1)?.changePct?.toFixed(2), "10.00");
  eq("legend: past the playhead is hidden", legendBar(bars, t1, t0), null);
  eq("legend: no bar at that minute", legendBar(bars, t1 + 60, t1 + 60), null);
  eq("formatChange up", formatChange({ change: 0.1, changePct: 10 }), "+0.10 (+10.00%)");
  eq("formatChange down", formatChange({ change: -0.4, changePct: -2.1 }), "−0.40 (−2.10%)");
  eq("formatChange none", formatChange({ change: null, changePct: null }), "");
  eq("timeframe labels", [timeframeLabel(60), timeframeLabel(300), timeframeLabel(15), timeframeLabel(3600)], ["1m", "5m", "15s", "1h"]);
}

// ---- follow the latest candle with a right margin; jump-to-latest rules
eq("margin: limit sits past the playhead", rightLimit({ replay: true, playIdx: 60, lastIdx: 100, minBars, marginBars: 3 }), 63);
eq("margin: may run past the last slot by the margin only", rightLimit({ replay: true, playIdx: 100, lastIdx: 100, minBars, marginBars: 3 }), 103);
eq("margin is 3 bars", RIGHT_MARGIN_BARS, 3);
eq("followed view keeps still while the latest is inside it", followShift({ from: 40, to: 70 }, 60, 3), { from: 40, to: 70 });
eq("followed view slides when the latest nears the edge", followShift({ from: 40, to: 70 }, 69, 3), { from: 42, to: 72 });
eq("followed view slides back when stepping back past its left edge", followShift({ from: 40, to: 70 }, 30, 3), { from: 3, to: 33 });
eq("latest in view", [latestInView({ from: 40, to: 70 }, 70), latestInView({ from: 40, to: 70 }, 72)], [true, false]);
eq("default view inside a longer timeline", defaultRange({ rightLimit: 400 }, 300), { from: 300, to: 400 });
eq("default view with an end", defaultRange({ rightLimit: 400 }, 300, 350), { from: 300, to: 350 });

// ---- legend helpers: indicator at or before a bar, bid/ask time
const ind = new Map<number, number>([[600, 1.5], [660, 1.6]]);
eq("value at the bar", valueAtOrBefore(ind, 660), 1.6);
eq("forming minute falls back to the last finished one", valueAtOrBefore(ind, 720), 1.6);
eq("never a later value", valueAtOrBefore(ind, 540), undefined);
eq("quote at the bar's last second", quoteAtSec(600, 60, null), 659);
eq("quote never past the playhead second", quoteAtSec(600, 60, 630), 630);
eq("bid x ask", formatBidAsk(26.1, 26.125), "26.10 × 26.13");

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("priceChart checks passed");
