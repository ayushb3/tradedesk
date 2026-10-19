/**
 * Self-check for reviewChart.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { Trade, TradeExcursions } from "./api";
import {
  AFTER_EXIT_GREY,
  debriefStarted,
  ensureUtc,
  greyAfterExit,
  isZoomWheel,
  revealBound,
  reviewWindow,
  writtenBeforeEntry,
  zoomRange,
} from "./reviewChart";

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

const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);

// ---- window: entry - 15 min .. exit + 30 min, clamped to the bars
const first = sec("2026-01-05T14:00:00Z");
const last = sec("2026-01-05T21:00:00Z");
const w = reviewWindow("2026-01-05T15:00:30Z", "2026-01-05T15:10:10Z", first, last);
eq("window start", w.startSec, sec("2026-01-05T14:45:00Z"));
eq("window end", w.endSec, sec("2026-01-05T15:40:00Z"));
eq("window exit minute", w.exitSec, sec("2026-01-05T15:10:00Z"));
const early = reviewWindow("2026-01-05T14:05:00Z", "2026-01-05T14:08:00Z", first, last);
eq("start clamped to first bar", early.startSec, first);
const late = reviewWindow("2026-01-05T20:40:00Z", "2026-01-05T20:50:00Z", first, last);
eq("end clamped to last bar", late.endSec, last);
const open = reviewWindow("2026-01-05T15:00:00Z", null, first, last);
eq("open trade runs to the last bar", [open.endSec, open.exitSec], [last, null]);

// ---- after-exit toggle
eq("hidden: bars stop at the exit", revealBound(1000, 700, false), 700);
eq("shown: bars run to the end", revealBound(1000, 700, true), 1000);
eq("open trade: no exit to stop at", revealBound(1000, null, false), 1000);

// ---- grey after the exit (bars after the exit minute only; whitespace untouched)
const bar = (time: number) => ({ time, open: 1, high: 2, low: 0.5, close: 1.5 }) as never;
const greyed = greyAfterExit([bar(60), bar(120), bar(180), { time: 240 } as never], 120) as unknown as Record<string, unknown>[];
eq("exit bar stays coloured", greyed[1].color, undefined);
eq("after-exit bar is grey", [greyed[2].color, greyed[2].borderColor, greyed[2].wickColor], [AFTER_EXIT_GREY, AFTER_EXIT_GREY, AFTER_EXIT_GREY]);
eq("whitespace untouched", greyed[3], { time: 240 });
eq("open trade: nothing greyed", (greyAfterExit([bar(60)], null) as unknown as Record<string, unknown>[])[0].color, undefined);

// ---- wheel: modifier only
eq("plain wheel is not a zoom", isZoomWheel({ ctrlKey: false, metaKey: false, altKey: false }), false);
eq("ctrl wheel zooms", isZoomWheel({ ctrlKey: true, metaKey: false, altKey: false }), true);
eq("cmd wheel zooms", isZoomWheel({ ctrlKey: false, metaKey: true, altKey: false }), true);

// ---- zoom keeps the anchor, stays inside [0, last], keeps a minimum width
const z = zoomRange({ from: 0, to: 100 }, 50, 0.5, 100);
eq("zoom in keeps the anchor centred", z, { from: 25, to: 75 });
eq("zoom out clamps to the full range", zoomRange({ from: 10, to: 90 }, 50, 3, 100), { from: 0, to: 100 });
const tight = zoomRange({ from: 40, to: 52 }, 46, 0.1, 100);
eq("minimum width", Math.round(tight.to - tight.from), 8);
const edge = zoomRange({ from: 0, to: 100 }, 0, 0.5, 100);
eq("zoom at the left edge stays in range", edge.from >= 0 && edge.to <= 100, true);

// ---- written before entry
const trade = (over: Partial<Trade>) => ({ qty: 2, stop_price: 0.8, r_basis: "written", ...over }) as unknown as Trade;
const exc = (over: Partial<TradeExcursions>) =>
  ({ risk: 120, risk_source: "written_max_loss", stop_price: 0.8, ...over }) as unknown as TradeExcursions;
const rows = writtenBeforeEntry(trade({}), exc({}));
eq("written rows", rows.map((r) => [r.key, r.value, r.assumed]), [
  ["max_loss", "$120.00", false],
  ["stop", "0.80", false],
  ["size", "2 contracts", false],
]);
const assumed = writtenBeforeEntry(trade({ qty: 1 }), exc({ risk_source: "default_risk_pct", stop_price: 0.7 }));
eq("assumed stop is flagged, max loss is not written", assumed.map((r) => [r.key, r.value, r.assumed]), [
  ["max_loss", "not written", true],
  ["stop", "0.70", true],
  ["size", "1 contract", false],
]);
const noExc = writtenBeforeEntry(trade({ r_basis: "assumed", stop_price: null }), null);
eq("no excursions, nothing written", noExc.map((r) => [r.key, r.value, r.assumed]), [
  ["max_loss", "not written", true],
  ["stop", "none", false],
  ["size", "2 contracts", false],
]);

// ---- debrief timestamps are PT (a bare ISO string is UTC, never the browser zone)
eq("bare iso gets a zone", ensureUtc("2026-01-05T15:30:00"), "2026-01-05T15:30:00Z");
eq("zoned iso kept", ensureUtc("2026-01-05T15:30:00-05:00"), "2026-01-05T15:30:00-05:00");
eq("winter UTC to PT", debriefStarted("2026-01-05T15:30:00"), "Started 07:30 PT");
eq("summer UTC to PT", debriefStarted("2026-07-06T15:30:00Z"), "Started 08:30 PT");
eq("missing", debriefStarted(null), null);

if (failures > 0) {
  console.error(`${failures} reviewChart check(s) failed`);
  process.exit(1);
}
console.log("reviewChart checks passed");
