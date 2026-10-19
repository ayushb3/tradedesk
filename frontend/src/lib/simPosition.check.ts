/**
 * Self-check for simPosition.ts (synthetic numbers only).
 * Run: npm run check:sim   (esbuild bundle -> node; there is no test runner)
 */
import {
  buy,
  closeAll,
  initialState,
  openR,
  sell,
  setStop,
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

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  throw new Error("simPosition self-check failed");
}
console.log("simPosition self-check passed");
