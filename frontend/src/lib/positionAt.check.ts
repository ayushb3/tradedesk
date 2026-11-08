/**
 * Self-check for positionAt.ts (synthetic data only).
 * Run: npm run check:sim
 */
import { formatPnl, openPnl, positionAt } from "./positionAt";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  const same =
    typeof got === "number" && typeof want === "number"
      ? Math.abs(got - want) < 1e-9
      : JSON.stringify(got) === JSON.stringify(want);
  if (!same) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const T0 = Date.UTC(2026, 0, 2, 15, 0, 0) / 1000;
const iso = (i: number) => new Date((T0 + i) * 1000).toISOString();
const f = (side: "buy" | "sell", qty: number, price: number, i: number) => ({
  side,
  qty,
  price,
  ts: iso(i),
});

const buys = [f("buy", 1, 2.0, 0), f("buy", 3, 3.0, 10)];
let p = positionAt(buys, T0 + 100);
eq("blended qty", p.qty, 4);
eq("blended avg", p.avg, 2.75);
eq("blended realized", p.realized, 0);

const part = [...buys, f("sell", 2, 3.5, 20)];
p = positionAt(part, T0 + 100);
eq("partial qty", p.qty, 2);
eq("partial avg unchanged", p.avg, 2.75);
eq("partial realized", p.realized, (3.5 - 2.75) * 2 * 100);

p = positionAt([...part, f("sell", 2, 2.0, 30)], T0 + 100);
eq("full exit qty", p.qty, 0);
eq("full exit avg reset", p.avg, 0);
eq("full exit realized", p.realized, 150 + (2.0 - 2.75) * 2 * 100);

p = positionAt([f("buy", 1, 2.0, 0), f("sell", 5, 3.0, 5)], T0 + 100);
eq("oversell clamps qty", p.qty, 0);
eq("oversell clamps realized", p.realized, 100);

p = positionAt(part, T0 + 15);
eq("later fills ignored qty", p.qty, 4);
eq("later fills ignored realized", p.realized, 0);

p = positionAt(part, T0 + 20);
eq("fill at atSec included", p.qty, 2);
eq("before any fill", positionAt(part, T0 - 1).qty, 0);

p = positionAt([f("sell", 2, 3.5, 20), f("buy", 3, 3.0, 10), f("buy", 1, 2.0, 0)], T0 + 100);
eq("unsorted qty", p.qty, 2);
eq("unsorted avg", p.avg, 2.75);

eq("openPnl flat", openPnl({ qty: 0, avg: 0, realized: 0 }, 3), null);
eq("openPnl no mark", openPnl({ qty: 1, avg: 2, realized: 0 }, null), null);
eq("openPnl value", openPnl({ qty: 2, avg: 2.5, realized: 0 }, 2.79), 58);

eq("fmt positive", formatPnl(58), "+$58.00");
eq("fmt negative", formatPnl(-12.5), "-$12.50");
eq("fmt zero", formatPnl(0), "$0.00");
eq("fmt thousands", formatPnl(1234), "+$1,234.00");
eq("fmt neg thousands", formatPnl(-1234.5), "-$1,234.50");

if (failures > 0) {
  console.error(`${failures} check(s) failed`);
  process.exit(1);
}
console.log("positionAt: all checks passed");
