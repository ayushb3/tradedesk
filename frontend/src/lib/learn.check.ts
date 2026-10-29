/**
 * Self-check for learn.ts (synthetic data only).
 * Run: npm run check:sim   (esbuild bundle -> node; there is no test runner)
 */
import type { Candle, DrillPublic } from "./api";
import {
  EMPTY_DRAFT,
  aggregateBars,
  bucketStart,
  hourlyLevels,
  kcuTimeframe,
  openingRange,
  sessionVwap,
  snapPrice,
  barsFor,
  missingText,
  startIndex,
  canSubmit,
  chartPickFor,
  currentStep,
  guardBars,
  isNotConfigured,
  nextUndone,
  parseInline,
  parseMarkdown,
  parsePrice,
  ptTime,
  ptWindow,
  drillWhen,
  requiredSteps,
  setEnter,
  shortDate,
  stepIndex,
  toAnswerRequest,
  type Draft,
} from "./learn";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const STEPS = ["level", "enter", "stop", "target"] as const;

// required steps
eq("steps before choice", requiredSteps(STEPS, null), ["level", "enter"]);
eq("steps enter", requiredSteps(STEPS, "enter"), ["level", "enter", "stop", "target"]);
eq("steps wait", requiredSteps(STEPS, "wait"), ["level", "enter"]);

// walk the machine: enter path
let d: Draft = { ...EMPTY_DRAFT };
eq("starts at level", currentStep(STEPS, d), "level");
d = { ...d, level: 100.5 };
eq("then enter", currentStep(STEPS, d), "enter");
d = setEnter(d, "enter");
eq("enter needs a side", currentStep(STEPS, d), "enter");
d = { ...d, side: "long" };
eq("then stop", currentStep(STEPS, d), "stop");
d = { ...d, stop: 99.9 };
eq("then target", currentStep(STEPS, d), "target");
eq("incomplete cannot submit", canSubmit(STEPS, d), false);
d = { ...d, target: 101.7 };
eq("done", currentStep(STEPS, d), null);
eq("can submit", canSubmit(STEPS, d), true);
eq("request enter", toAnswerRequest(d), { level: 100.5, enter: "enter", side: "long", stop: 99.9, target: 101.7 });

// wait path skips the rest and clears entry-only fields
d = setEnter(d, "wait");
eq("wait clears", [d.side, d.stop, d.target], [null, null, null]);
eq("wait is complete", canSubmit(STEPS, d), true);
eq("request wait", toAnswerRequest(d), { level: 100.5, enter: "wait", side: null, stop: null, target: null });
eq("no level, cannot submit", canSubmit(STEPS, setEnter({ ...EMPTY_DRAFT }, "wait")), false);

// chart picks
eq("pick level", chartPickFor("level"), "level");
eq("pick enter none", chartPickFor("enter"), null);
eq("pick done none", chartPickFor(null), null);

// parsePrice
eq("price ok", parsePrice(" 1,234.50 "), 1234.5);
eq("price empty", parsePrice(""), null);
eq("price junk", parsePrice("abc"), null);
eq("price zero", parsePrice("0"), null);
eq("price negative", parsePrice("-3"), null);

// no-leak guard: never a bar at or after pause_at before the answer
const mk = (min: number): Candle => ({
  symbol: "TEST",
  ts: new Date(Date.UTC(2026, 0, 2, 14, min)).toISOString(),
  open: 1,
  high: 2,
  low: 0.5,
  close: 1.5,
  volume: 10,
  timeframe: "1m",
});
const bars = [mk(0), mk(1), mk(2), mk(3), mk(4)];
const pause = mk(3).ts;
eq("guard cuts at pause", guardBars(bars, pause, false).map((c) => c.ts), [mk(0).ts, mk(1).ts, mk(2).ts]);
eq("guard exclusive of pause bar", guardBars(bars, pause, false).some((c) => c.ts === pause), false);
eq("guard passes all after answer", guardBars(bars, pause, true).length, 5);
eq("guard on already-safe bars", guardBars(bars.slice(0, 2), pause, false).length, 2);

// playback index
eq("step", stepIndex(0, 3), 1);
eq("step clamps", stepIndex(2, 3), 2);
eq("step empty", stepIndex(0, 0), 0);

// PT formatting (14:30Z in January = 06:30 PST; 13:30Z in July = 06:30 PDT)
eq("ptTime winter", ptTime("2026-01-02T14:30:00Z"), "06:30 PT");
eq("ptTime summer", ptTime("2026-07-02T13:30:00Z"), "06:30 PT");
eq("ptWindow", ptWindow("2026-01-02T14:30:00Z", "2026-01-02T15:12:00Z"), "06:30 to 07:12 PT");
eq(
  "drillWhen",
  drillWhen({ date: "2026-10-21", pause_at: "2026-10-21T18:59:00Z" }),
  "Session Oct 21 PT · pauses at 11:59 PT",
);
eq("shortDate", shortDate("2026-10-14"), "Oct 14");
eq("shortDate bad", shortDate("nope"), "nope");

// not configured
eq("notConfigured yes", isNotConfigured({ configured: false, message: "x" }), true);
eq("notConfigured array", isNotConfigured([]), false);
eq("notConfigured ok", isNotConfigured({ configured: true }), false);

// next undone
const dr = (id: string, status: "new" | "done"): DrillPublic => ({
  id,
  set_date: "2026-01-02",
  topic: "t",
  lessons: [],
  ticker: "TEST",
  date: "2026-01-02",
  timeframe: "1m",
  bars_from: mk(0).ts,
  pause_at: mk(3).ts,
  question_steps: ["level", "enter"],
  review: false,
  status,
  score: null,
});
const set = [dr("a", "done"), dr("b", "new"), dr("c", "new")];
eq("next after a", nextUndone(set, "a")?.id, "b");
eq("next after b", nextUndone(set, "b")?.id, "c");
eq("wraps", nextUndone([dr("a", "new"), dr("b", "done"), dr("c", "done")], "c")?.id, "a");
eq("none left", nextUndone([dr("a", "done"), dr("b", "new")], "b"), null);

// markdown
eq("md blocks", parseMarkdown("## Why\nline one\nline two\n\n- a\n- b\n\nEnd"), [
  { kind: "h", text: "Why" },
  { kind: "p", text: "line one line two" },
  { kind: "ul", items: ["a", "b"] },
  { kind: "p", text: "End" },
]);
eq("md inline", parseInline("a **b** `c` d"), [
  { text: "a " },
  { text: "b", bold: true },
  { text: " " },
  { text: "c", code: true },
  { text: " d" },
]);
eq("md html stays text", parseInline("<b>x</b>"), [{ text: "<b>x</b>" }]);

// ---- playback window + submit hint ----
eq("barsFor 1m", barsFor(30, "1m"), 30);
eq("barsFor 5m", barsFor(30, "5m"), 6);
eq("barsFor odd", barsFor(60, "day"), 60);
eq("startIndex lead", startIndex(200, 30), 169);
eq("startIndex short session", startIndex(10, 30), 0);
eq("missing all", missingText(STEPS, EMPTY_DRAFT), "To submit: set the level, pick Enter or Wait.");
eq("missing side", missingText(STEPS, { ...EMPTY_DRAFT, level: 1, enter: "enter" }), "To submit: pick Long or Short, set the stop, set the target.");
eq("missing none (wait)", missingText(STEPS, { ...EMPTY_DRAFT, level: 1, enter: "wait" }), "");

// ---- timeframes ----
eq("kcu 2m", kcuTimeframe(0), 2);
eq("kcu 2m edge", kcuTimeframe(29), 2);
eq("kcu 5m", kcuTimeframe(30), 5);
eq("kcu 10m", kcuTimeframe(90), 10);
const OPEN = Date.parse("2026-01-05T14:30:00Z") / 1000;
eq("bucket aligned to open", bucketStart(OPEN + 61 * 60, 3600, OPEN), OPEN + 3600);
const bar = (min: number, o: number, h: number, l: number, c: number): Candle => ({
  symbol: "X", ts: new Date((OPEN + min * 60) * 1000).toISOString(), open: o, high: h, low: l, close: c, volume: 1, timeframe: "1m",
});
const tape = [bar(0, 10, 11, 9, 10.5), bar(1, 10.5, 12, 10, 11), bar(2, 11, 11.5, 8, 9), bar(3, 9, 9.5, 8.5, 9.2)];
const agg = aggregateBars(tape, 2, OPEN);
eq("agg count", agg.length, 2);
eq("agg first", [agg[0].open, agg[0].high, agg[0].low, agg[0].close, agg[0].volume], [10, 12, 9, 11, 2]);
eq("agg forming bar uses only what was passed", aggregateBars(tape.slice(0, 3), 2, OPEN)[1].close, 9);
eq("agg 1m passthrough", aggregateBars(tape, 1, OPEN).length, 4);
const hourTape = Array.from({ length: 90 }, (_, i) => bar(i, 100, 100 + (i < 60 ? i / 10 : 1), 100 - (i < 60 ? 1 : 5), 100));
eq("hourly: none until the first hour closes", hourlyLevels(hourTape.slice(0, 59), OPEN, OPEN + 58 * 60).length, 0);
const hl = hourlyLevels(hourTape, OPEN, OPEN + 89 * 60);
eq("hourly: closed hour only", hl.map((x) => x.price), [105.9, 99]);

// ---- ORB + VWAP ----
eq("orb: hidden until 15 min closed", openingRange(hourTape, OPEN, OPEN + 13 * 60).length, 0);
eq("orb: high/low of first 15", openingRange(hourTape, OPEN, OPEN + 14 * 60).map((x) => x.price), [101.4, 99]);
const vw = sessionVwap([bar(0, 10, 12, 8, 10), bar(1, 10, 13, 11, 12)], 1, OPEN);
eq("vwap first", vw.get(OPEN), 10);
eq("vwap second", vw.get(OPEN + 60), 11);
eq("vwap bucketed keeps latest", sessionVwap([bar(0, 10, 12, 8, 10), bar(1, 10, 13, 11, 12)], 2, OPEN).get(OPEN), 11);

// ---- snap ----
eq("snap to near level", snapPrice(740.35, [740.33, 742]), 740.33);
eq("no snap when far", snapPrice(740.8, [740.33]), 740.8);
eq("snap picks nearest", snapPrice(740.4, [740.3, 740.45]), 740.45);

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("learn.check: all ok");
