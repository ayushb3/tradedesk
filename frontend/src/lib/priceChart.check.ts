/**
 * Self-check for priceChart.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import {
  AUTOSCALE_PAD,
  MAX_BAR_SPACING,
  chipWidth,
  clampRange,
  defaultRange,
  fillChipText,
  fitWindow,
  indexOfSec,
  indexRange,
  minVisibleBars,
  paddedRange,
  placeChips,
  rightLimit,
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

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("priceChart checks passed");
