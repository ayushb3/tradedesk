/** Self-check for freeOptions.ts (synthetic numbers only). Run: npm run check:sim */
import {
  atmStrike,
  contractBarAt,
  contractLabel,
  dayLimitHit,
  inTradingWindow,
  isOptionRoot,
  pricing,
  ptMinutesOf,
  RULE_CLOSE_FRAC,
  strikeStep,
  underlyingScale,
  type Candle,
} from "./freeOptions";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (got !== want) {
    failures++;
    console.error(`FAIL ${name}: got ${String(got)} want ${String(want)}`);
  } else console.log(`ok   ${name}`);
}

eq("isOptionRoot SPY", isOptionRoot("SPY"), true);
eq("isOptionRoot lower", isOptionRoot("spxw"), true);
eq("isOptionRoot NVDA", isOptionRoot("NVDA"), false);
eq("step SPY", strikeStep("SPY"), 1);
eq("step QQQ", strikeStep("QQQ"), 1);
eq("step SPXW", strikeStep("SPXW"), 5);
eq("scale SPXW", underlyingScale("SPXW"), 10);
eq("scale SPY", underlyingScale("SPY"), 1);
eq("atm SPY", atmStrike("SPY", 600.4), 600);
eq("atm SPY up", atmStrike("SPY", 600.6), 601);
eq("atm SPXW x10", atmStrike("SPXW", 702.34), 7025);
eq("atm SPXW rounds to 5", atmStrike("SPXW", 702.8), 7030);
eq("pricing SPY spread", pricing("SPY").halfSpread, 0.01);
eq("pricing QQQ fee", pricing("QQQ").feePerContract, 0.04);
eq("pricing SPXW spread", pricing("SPXW").halfSpread, 0.05);
eq("pricing SPXW fee", pricing("SPXW").feePerContract, 0.97);
eq("label call", contractLabel("SPY", "call", 600, "2026-11-13"), "SPY 600C 11/13");
eq("label SPXW put", contractLabel("SPXW", "put", 7025, "2026-11-13"), "SPXW 7025P 11/13");
eq("rule frac", RULE_CLOSE_FRAC, 0.5);

const mk = (iso: string) =>
  ({ ts: iso, open: 1, high: 1, low: 1, close: 1, volume: 1 }) as unknown as Candle;
const bars = [mk("2026-11-13T20:40:00Z"), mk("2026-11-13T20:41:00Z"), mk("2026-11-13T20:43:00Z")];
const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
let r = contractBarAt(bars, sec("2026-11-13T20:41:00Z"));
eq("exact bar", r?.bar.ts, bars[1].ts);
eq("exact not stale", r?.stale, false);
r = contractBarAt(bars, sec("2026-11-13T20:42:00Z"));
eq("missing minute -> previous", r?.bar.ts, bars[1].ts);
eq("missing minute stale", r?.stale, true);
r = contractBarAt(bars, sec("2026-11-13T20:59:00Z"));
eq("after last -> last", r?.bar.ts, bars[2].ts);
eq("after last stale", r?.stale, true);
eq("before first -> null", contractBarAt(bars, sec("2026-11-13T20:39:00Z")), null);
eq("empty -> null", contractBarAt([], 5), null);

eq("limit stop", dayLimitHit(-250, -250, 500), "stop");
eq("limit below stop", dayLimitHit(-300, -250, 500), "stop");
eq("limit target", dayLimitHit(500, -250, 500), "target");
eq("limit none", dayLimitHit(100, -250, 500), null);
eq("limit nulls", dayLimitHit(-9999, null, null), null);
eq("limit target only", dayLimitHit(600, null, 500), "target");

const win = [
  { start: "06:30", end: "08:30" },
  { start: "11:00", end: "12:00" },
];
eq("window start inclusive", inTradingWindow(390, win), true);
eq("window end exclusive", inTradingWindow(510, win), false);
eq("window last minute", inTradingWindow(509, win), true);
eq("window gap", inTradingWindow(600, win), false);
eq("window second", inTradingWindow(660, win), true);
eq("window none", inTradingWindow(390, []), false);

eq("PT after DST end (PST)", ptMinutesOf("2026-11-13T20:15:00Z"), 735);
eq("PT during DST (PDT)", ptMinutesOf("2026-07-06T19:42:00Z"), 762);
eq("PT midnight", ptMinutesOf("2026-11-13T08:00:00Z"), 0);

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  throw new Error("freeOptions self-check failed");
}
console.log("freeOptions self-check passed");
