/**
 * Self-check for ruleBar.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { ReplayDaySoFar } from "./api";
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

const day: ReplayDaySoFar = {
  trade_id: 3,
  session_date: "2026-01-05",
  net_so_far: -30.5,
  prior_trades: 2,
  daily_stop: -100,
  daily_target: 200,
  cutoff: "08:30",
  entry: "before",
  call_source: "mentor",
};

const s = ruleBarState(day);
eq("net maps through", s.dayNet, -30.5);
eq("prior trade count", s.priorTrades, 2);
eq("stop", s.stop, -100);
eq("target", s.target, 200);
eq("cutoff", s.cutoff, "08:30");
eq("entry before", s.entry, "before");
eq("caller is the call source", s.caller, "mentor");
eq("entry after", ruleBarState({ ...day, entry: "after" }).entry, "after");
eq("empty call source -> null", ruleBarState({ ...day, call_source: "" }).caller, null);
eq("zero net stays zero", ruleBarState({ ...day, net_so_far: 0, prior_trades: 0 }).dayNet, 0);

const none = ruleBarState(null);
eq("no day -> net null", none.dayNet, null);
eq("no day -> stop null", none.stop, null);
eq("no day -> entry null", none.entry, null);
eq("no day -> no caller", none.caller, null);
eq("no day -> zero trades", none.priorTrades, 0);

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
