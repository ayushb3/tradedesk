/**
 * Self-check for tradeReview.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { ReviewDay, RuleResult, TradeExcursions } from "./api";
import { dayTally, ruleChips, storyStrip } from "./tradeReview";

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

// ---- rule chips
const rr = (code: RuleResult["code"], kept: boolean | null): RuleResult => ({ code, kept, detail: `d-${code}` });
const chips = ruleChips([
  rr("size_within_plan", null),
  rr("before_cutoff", false),
  rr("call_source", true),
  rr("max_loss_written", false),
]);
eq("chip order", chips.map((c) => c.code), ["call_source", "max_loss_written", "before_cutoff", "size_within_plan"]);
eq("chip labels", chips.map((c) => c.label), ["Mentor call", "No max loss written", "After cutoff", "Size within plan"]);
eq("chip tones", chips.map((c) => c.tone), ["kept", "broken", "broken", "unknown"]);
eq("chip detail", chips[0].detail, "d-call_source");
eq("own idea label", ruleChips([rr("call_source", false)])[0].label, "Own idea");
eq("unknown call label", ruleChips([rr("call_source", null)])[0].label, "Call source ?");
eq("no results", ruleChips(undefined), []);

// ---- day tally
const at = (hhmm: string) => `2026-01-05T${hhmm}:00Z`;
const pos = (id: number, status: string, pnl: number | null, from: string, to: string) => ({
  trade_id: id,
  status,
  pnl,
  fills: [
    { side: "B", ts: at(from) },
    { side: "S", ts: at(to) },
  ],
});
const day: ReviewDay = {
  date: "2026-01-05",
  positions: [
    pos(1, "closed", -60, "14:00", "14:10"),
    pos(2, "closed", -50, "14:20", "14:30"),
    pos(3, "closed", -40, "14:40", "14:50"),
    pos(4, "open", null, "15:00", "15:00"),
  ],
  stop_result: { limit: -100, hit: true, hit_at_pt: "06:30 PT" },
};
const t2 = dayTally(day, 2)!;
eq("tally net through trade 2", t2.net, -110);
eq("tally count", t2.trades, 2);
eq("tally at stop", t2.atStop, true);
eq("tally limit", t2.limit, -100);
const t1 = dayTally(day, 1)!;
eq("tally first trade not at stop", t1.atStop, false);
eq("tally excludes later trades", t1.net, -60);
eq("tally open trade", dayTally(day, 4), null);
eq("tally no day", dayTally(null, 1), null);
eq("tally no stop result", dayTally({ ...day, stop_result: undefined }, 1)!.limit, null);

// ---- story strip
const base: TradeExcursions = {
  trade_id: 1,
  tz_label: "PT",
  available: true,
  reason: null,
  contract: null,
  estimated: false,
  status: "closed",
  avg_in: 2,
  qty: 1,
  risk: 60,
  risk_source: "default_risk_pct",
  stop_price: 1.4,
  fills: [],
  post_exit: [],
  hold: {
    mae_usd: -50,
    mfe_usd: 30,
    mae_r: -0.83,
    mfe_r: 0.5,
    mae_ts_pt: "06:46:00 PT",
    mfe_ts_pt: "06:47:00 PT",
    drawdown_minutes: 1,
    crossed_stop: false,
    crossed_stop_at_pt: null,
  },
  after_exit: { window_min: 30, bars: 30, complete: true, returned_at_pt: "06:58:00 PT", minutes_after_exit: 8 },
};
const s = storyStrip(base)!;
eq("story keys", s.map((i) => i.key), ["best", "stop", "worst", "after"]);
eq("best", [s[0].value, s[0].sub], ["+0.50R", "at 06:47 PT"]);
eq("stop not touched", s[1].value, "Stop not touched");
eq("worst", [s[2].value, s[2].sub], ["-0.83R", "at 06:46 PT"]);
eq("after back", s[3].value, "Back to entry by 06:58 PT");
eq("not estimated", s.some((i) => i.estimated), false);

const hit = storyStrip({
  ...base,
  hold: { ...base.hold!, crossed_stop: true, crossed_stop_at_pt: "06:46:00 PT", crossed_stop_minutes: 3 },
  after_exit: { window_min: 30, bars: 30, complete: true, returned_at_pt: null, minutes_after_exit: null },
  quality: "suspect_wicks",
})!;
eq("stop hit", [hit[1].value, hit[1].sub], ["06:46 PT", "3 min after entry"]);
eq("never back", hit[3].value, "Never back to entry in 30 min");
eq("suspect marks best/stop/worst", hit.map((i) => i.estimated), [true, true, true, false]);
eq("unavailable", storyStrip({ ...base, available: false }), null);
eq("open trade has no after-exit", storyStrip({ ...base, status: "open" })!.length, 3);
eq("no bars after exit", storyStrip({ ...base, after_exit: { ...base.after_exit!, bars: 0 } })![3].value, "No bars after the exit");

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("tradeReview checks passed");
