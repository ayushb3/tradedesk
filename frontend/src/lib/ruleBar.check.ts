/**
 * Self-check for ruleBar.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { DeskDay, DeskRules } from "./api";
import { meterGeometry, ruleBarState } from "./ruleBar";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  const ok =
    typeof got === "number" && typeof want === "number"
      ? Math.abs(got - want) < 1e-9
      : got === want;
  if (!ok) {
    failures++;
    console.error(`FAIL ${name}: got ${String(got)} want ${String(want)}`);
  } else {
    console.log(`ok   ${name}`);
  }
}

const rules: DeskRules = { window: { start: "06:30", end: "08:30" }, daily_stop: -100, daily_target: 200 };
// January is PST (UTC-8): 14:00Z = 06:00 PT.
const at = (hhmm: string) => `2026-01-05T${hhmm}:00Z`;
const pos = (id: number, status: string, pnl: number | null, from: string, to: string, caller?: string) => ({
  trade_id: id,
  status,
  pnl,
  fills: [
    { side: "B", ts: at(from) },
    { side: "S", ts: at(to) },
  ],
  plan: caller ? { caller } : null,
});
const day: DeskDay = {
  date: "2026-01-05",
  positions: [
    pos(1, "closed", -30, "14:00", "14:10"),
    pos(2, "closed", 50, "14:20", "14:40"),
    pos(3, "closed", -80, "15:00", "15:20", "caller-a"), // the replayed trade
    pos(4, "closed", 999, "15:30", "15:40"), // after entry: must not leak
    pos(5, "open", 10, "14:50", "14:55"), // still open: excluded
    pos(6, "closed", 7, "14:50", "15:05"), // closes after the entry: excluded
  ],
  fields: { callers: ["caller-b"] },
};

const s = ruleBarState({ rules, day, tradeId: 3, entryTs: at("15:00") });
eq("net counts only trades closed before entry", s.dayNet, 20);
eq("prior trade count", s.priorTrades, 2);
eq("stop", s.stop, -100);
eq("target", s.target, 200);
eq("cutoff", s.cutoff, "08:30");
eq("entry 07:00 PT is before", s.entry, "before");
eq("caller from own plan", s.caller, "caller-a");

eq("entry 08:31 PT is after", ruleBarState({ rules, day, tradeId: 3, entryTs: at("16:31") }).entry, "after");
eq("entry at the cutoff is before", ruleBarState({ rules, day, tradeId: 3, entryTs: at("16:30") }).entry, "before");
eq("caller falls back to day callers", ruleBarState({ rules, day, tradeId: 99, entryTs: at("15:00") }).caller, "caller-b");

const none = ruleBarState({ rules: null, day: null, tradeId: 3, entryTs: at("15:00") });
eq("no day -> net null", none.dayNet, null);
eq("no rules -> stop null", none.stop, null);
eq("no rules -> entry null", none.entry, null);
eq("no caller", none.caller, null);
eq("day with no earlier trade -> 0", ruleBarState({ rules, day, tradeId: 3, entryTs: at("13:00") }).dayNet, 0);

const g = meterGeometry(-50, -100, 200)!;
eq("meter zero", g.zeroPct, 100 / 3);
eq("meter fill width", g.fillWidthPct, 100 / 6);
eq("meter fill starts left of zero", g.fillLeftPct, 100 / 3 - 100 / 6);
eq("meter clamps past the stop", meterGeometry(-500, -100, 200)!.fillLeftPct, 0);
eq("meter null without target", meterGeometry(0, -100, null), null);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ruleBar checks passed");
