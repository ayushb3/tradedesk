/**
 * Self-check for simPosition.ts (synthetic numbers only).
 * Run: npm run check:sim   (esbuild bundle -> node; there is no test runner)
 */
import { stopVerdict, summarizeRealTrade } from "./tradeResult";
import {
  buy,
  closeAll,
  initialState,
  openR,
  ruleCloseFill,
  sell,
  totalPnl,
  setStop,
  sessionRealized as sessionRealizedOf,
  setTarget,
  unrealized,
  type SimState,
} from "./simPosition";

let failures = 0;
function eq(name: string, got: number | boolean | null | undefined, want: number | boolean | null) {
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
const at = (i: number) => `2026-01-02T15:${String(i).padStart(2, "0")}:00Z`;

// 1) Written stop, single contract, full close.
let s: SimState = buy(initialState(), { price: 2.0, ts: at(0) }, 1.5);
eq("written stop used", s.position.stop, 1.5);
eq("written stop not assumed", s.position.stopAssumed, false);
eq("1R = (2.00-1.50)*100", s.position.risk, 50);
eq("open R at +0.50", openR(s.position, 2.5), 1);
eq("open R at stop", openR(s.position, 1.5), -1);
s = closeAll(s, { price: 2.5, ts: at(1) });
eq("flat after close", s.position.qty, 0);
eq("one round trip", s.trips.length, 1);
eq("trip realized", s.trips[0].realized, 50);
eq("trip R", s.trips[0].r, 1);

// 2) No written stop -> assumed -30%.
s = buy(initialState(), { price: 1.0, ts: at(0) }, null);
eq("assumed stop", s.position.stop, 0.7);
eq("assumed flag", s.position.stopAssumed, true);
eq("1R assumed = 30", s.position.risk, 30);
s = buy(initialState(), { price: 1.0, ts: at(0) }, 1.2);
eq("invalid written stop falls back", s.position.stopAssumed, true);
// Free replay (underlying): a caller-chosen assumed fraction, here 0.5% below a $500 buy.
s = buy(initialState(), { price: 500, ts: at(0) }, null, 0.005);
eq("underlying assumed stop", s.position.stop, 497.5);
eq("underlying 1R = 2.50 x 100", s.position.risk, 250);

// 3) Add lower: blended average, flag, R denominator stays fixed.
s = buy(initialState(), { price: 2.0, ts: at(0) }, 1.4); // risk = 60
s = buy(s, { price: 1.6, ts: at(1) }); // add below avg
eq("blended avg", s.position.avg, 1.8);
eq("qty 2", s.position.qty, 2);
eq("added lower flagged", s.position.fills[1].addedLower, true);
eq("risk unchanged by add", s.position.risk, 60);
eq("unrealized at 1.8", unrealized(s.position, 1.8), 0);
eq("open R at 1.4 = -80/60", openR(s.position, 1.4), -80 / 60);
s = buy(s, { price: 2.5, ts: at(2) }); // add above avg
eq("add above avg not flagged", s.position.fills[2].addedLower, false);
eq("avg after 3", s.position.avg, (2.0 + 1.6 + 2.5) / 3);

// 4) Partial exits keep the average and realize at it.
s = buy(initialState(), { price: 1.0, ts: at(0) }, 0.8); // risk = 20
s = buy(s, { price: 1.2, ts: at(1) }); // avg 1.1
s = sell(s, { price: 1.3, ts: at(2) }); // realize (1.3-1.1)*100 = 20
eq("avg kept after partial sell", s.position.avg, 1.1);
eq("qty after partial", s.position.qty, 1);
eq("realized after partial", s.position.realized, 20);
eq("no trip yet", s.trips.length, 0);
eq("open R = (20 + 20)/20", openR(s.position, 1.3), 2);
s = sell(s, { price: 0.9, ts: at(3) }); // realize (0.9-1.1)*100 = -20
eq("trip closed", s.trips.length, 1);
eq("trip realized 0", s.trips[0].realized, 0);
eq("trip R 0", s.trips[0].r, 0);
eq("trip added lower 0", s.trips[0].addedLower, 0);
eq("trip peak qty", s.trips[0].peakQty, 2);
eq("avg exit", s.trips[0].avgExit, 1.1);

// 5) Second round trip starts fresh; sell when flat is a no-op.
s = sell(s, { price: 5, ts: at(4) });
eq("sell when flat ignored", s.trips.length, 1);
s = buy(s, { price: 3.0, ts: at(5) }, 2.0);
eq("new trip avg", s.position.avg, 3.0);
eq("new trip risk", s.position.risk, 100);
s = setStop(s, 2.5);
eq("stop dragged", s.position.stop, 2.5);
eq("drag keeps 1R", s.position.risk, 100);
eq("drag clears assumed", s.position.stopAssumed, false);
s = closeAll(s, { price: 2.5, ts: at(6) });
eq("two trips", s.trips.length, 2);
eq("trip 2 R", s.trips[1].r, -0.5);
eq("trip 2 stop moves", s.trips[1].stopMoves, 1);

// 6) Stop history is recorded: first buy, then each released drag (not mid-drag).
s = buy(initialState(), { price: 2.0, ts: at(0) }, 1.5);
s = setStop(s, 1.7, false, at(1));
eq("mid-drag not in history", s.position.stops.length, 1);
s = setStop(s, 1.8, true, at(2));
eq("released drag in history", s.position.stops.length, 2);
eq("history stop value", s.position.stops[1].stop, 1.8);
s = closeAll(s, { price: 2.2, ts: at(3) });
eq("trip keeps stop history", s.trips[s.trips.length - 1].stops.length, 2);

// 6b) Profit target: plan line only, mirrors the stop's lifecycle.
s = setTarget(initialState(), 3);
eq("target flat no-op", s.position.target, null);
s = buy(initialState(), { price: 2.0, ts: at(0) }, 1.5);
eq("target starts unset", s.position.target, null);
s = setTarget(s, 3.0);
eq("target set", s.position.target, 3.0);
s = buy(s, { price: 2.2, ts: at(1) });
eq("target carried across add", s.position.target, 3.0);
s = setTarget(s, null);
eq("null clears target", s.position.target, null);
s = setTarget(s, 3.5);
s = closeAll(s, { price: 2.4, ts: at(2) });
eq("target cleared on full exit", s.position.target, null);

// 7) Stop verdict. Bars carry only lows here. Position opens at minute 0.
const bar = (i: number, low: number) => ({ ts: at(i), low });
let v = stopVerdict({
  bars: [bar(0, 0.1), bar(1, 1.6), bar(2, 1.55), bar(3, 1.7)],
  openTs: at(0),
  flatTs: at(3),
  stops: [{ ts: at(0), stop: 1.5 }],
});
eq("respected when lows stay above", v.respected, true);
eq("respected has 0 min", v.minutesPast, 0);
eq("entry bar low is ignored", v.firstBreachTs === null, true);

// low exactly at the stop counts; held bars 2,3,4 = 3 min; later bars ignored.
v = stopVerdict({
  bars: [bar(1, 1.6), bar(2, 1.5), bar(3, 1.4), bar(4, 1.45), bar(5, 0.2)],
  openTs: at(0),
  flatTs: at(4),
  stops: [{ ts: at(0), stop: 1.5 }],
});
eq("low at stop is past stop", v.respected, false);
eq("held 3 min (bars 2,3,4)", v.minutesPast, 3);
eq("first breach bar", v.firstBreachTs === new Date(at(2)).toISOString(), true);

v = stopVerdict({ bars: [bar(1, 1.6), bar(2, 1.4)], openTs: at(0), flatTs: at(2), stops: [{ ts: at(0), stop: 1.5 }] });
eq("breach on exit bar = 1 min", v.minutesPast, 1);

// A dragged stop applies from the bar AFTER it was set.
v = stopVerdict({
  bars: [bar(1, 1.6), bar(2, 1.45), bar(3, 1.6)],
  openTs: at(0),
  flatTs: at(3),
  stops: [{ ts: at(0), stop: 1.5 }, { ts: at(1), stop: 1.4 }],
});
eq("lowered stop in force at bar 2 -> respected", v.respected, true);
v = stopVerdict({
  bars: [bar(1, 1.6), bar(2, 1.45), bar(3, 1.6)],
  openTs: at(0),
  flatTs: at(3),
  stops: [{ ts: at(0), stop: 1.5 }, { ts: at(2), stop: 1.4 }],
});
eq("stop moved on bar 2 does not cover bar 2", v.minutesPast, 2);

// 8) Real trade summary: exit hidden unless asked; net $ and R are the server's.
const real = [
  { role: "entry" as const, side: "buy" as const, qty: 2, price: 2.0, ts: at(0) },
  { role: "exit" as const, side: "sell" as const, qty: 2, price: 2.5, ts: at(5) },
];
// Gross would be +100; the server's NET is lower (fees in) and is what shows.
const net = { net_pnl: 97.9, r: 0.65, r_basis: "assumed" };
let rt = summarizeRealTrade(real, 1.5, false, false, net);
eq("exit not built before reveal", rt?.exit === null, true);
eq("written stop not assumed", rt?.stopAssumed, false);
rt = summarizeRealTrade(real, 1.5, true, false, net);
eq("net pnl comes from the server, not gross prices", rt?.exit?.pnl ?? NaN, 97.9);
eq("R comes from the server", rt?.exit?.r ?? NaN, 0.65);
eq("R basis passes through", rt?.exit?.rBasis === "assumed", true);
rt = summarizeRealTrade(real, 1.5, true);
eq("no server result -> pnl null", rt?.exit?.pnl, null);
eq("no server result -> R null", rt?.exit?.r, null);
rt = summarizeRealTrade(real, null, true);
eq("assumed stop flagged", rt?.stopAssumed, true);
rt = summarizeRealTrade(real, 1.5, true, true);
eq("api-assumed flag wins over a usable stop", rt?.stopAssumed, true);
rt = summarizeRealTrade(real, null, true);
eq("assumed stop value", rt?.stop ?? NaN, 1.4);
eq("no entry -> null", summarizeRealTrade([], 1, true) === null, true);

// 9) Fees: buy 2 @1.00 fee 0.04/ct, sell 2 @1.50 fee 0.04/ct -> gross 100, fees 0.16, net 99.84.
s = buy(initialState(), { price: 1.0, ts: at(0), qty: 2, feePerContract: 0.04 }, 0.8);
eq("buy fee on fill", s.position.fills[0].fee ?? NaN, 0.08);
eq("position fees after buy", s.position.fees, 0.08);
eq("totalPnl open subtracts fees", totalPnl(s.position, 1.5), 100 - 0.08);
eq("open R net of fees", openR(s.position, 1.5)!, (100 - 0.08) / 40);
eq("realized stays gross in position", s.position.realized, 0);
s = closeAll(s, { price: 1.5, ts: at(1), feePerContract: 0.04 });
eq("trip gross", s.trips[0].gross, 100);
eq("trip fees", s.trips[0].fees, 0.16);
eq("trip realized net", s.trips[0].realized, 99.84);
eq("trip r uses net", s.trips[0].r, 99.84 / 40);
eq("sell fee on fill", s.trips[0].fills[1].fee ?? NaN, 0.08);
eq("flat fees 0", s.position.fees, 0);
eq("sessionRealized net", sessionRealizedOf(s), 99.84);
// No fee given -> no fee field, numbers unchanged.
s = closeAll(buy(initialState(), { price: 1.0, ts: at(0) }, 0.8), { price: 1.1, ts: at(1) });
eq("no fee -> undefined", s.trips[0].fills[0].fee === undefined, true);
eq("no fee -> fees 0", s.trips[0].fees, 0);
// Option pricing in a pure way: ask = close + 0.01, bid = close - 0.01.
s = buy(initialState(), { price: 1.0 + 0.01, ts: at(0), feePerContract: 0.04 }, null);
s = closeAll(s, { price: 1.0 - 0.01, ts: at(1), feePerContract: 0.04 });
eq("spread round trip net", s.trips[0].realized, -2 - 0.08);

// 10) ruleCloseFill: -50% rule on a long.
const held = buy(initialState(), { price: 2.0, ts: at(0) }).position; // threshold 1.00
eq("rule flat -> null", ruleCloseFill(initialState().position, { open: 1, low: 0.1, ts: at(1) }, 0.5) === null, true);
eq("rule gap -> open", ruleCloseFill(held, { open: 0.8, low: 0.6, ts: at(1) }, 0.5), 0.8);
eq("rule touch -> threshold", ruleCloseFill(held, { open: 1.5, low: 1.0, ts: at(1) }, 0.5), 1.0);
eq("rule no touch -> null", ruleCloseFill(held, { open: 1.5, low: 1.01, ts: at(1) }, 0.5) === null, true);

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  throw new Error("simPosition self-check failed");
}
console.log("simPosition self-check passed");
