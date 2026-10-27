/**
 * Self-check for learn.ts (synthetic data only).
 * Run: npm run check:sim   (esbuild bundle -> node; there is no test runner)
 */
import type { Candle, DrillPublic } from "./api";
import {
  EMPTY_DRAFT,
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

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("learn.check: all ok");
