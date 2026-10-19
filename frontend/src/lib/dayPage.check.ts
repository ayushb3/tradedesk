/**
 * Self-check for dayPage.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { DeskDayDetail, DeskPosition, RuleResult } from "./api";
import { clockPt, dayVerdict, linePoints, tradeBands, tradeRows } from "./dayPage";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  const ok =
    typeof got === "number" && typeof want === "number"
      ? Math.abs(got - want) < 1e-9
      : JSON.stringify(got) === JSON.stringify(want);
  if (!ok) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else {
    console.log(`ok   ${name}`);
  }
}

const rr = (code: RuleResult["code"], kept: boolean | null): RuleResult => ({ code, kept, detail: "" });

function pos(
  id: number,
  startIso: string,
  endIso: string,
  pnl: number | null,
  r: number | null,
  basis: string,
  rules: RuleResult[],
): DeskPosition {
  return {
    trade_id: id,
    label: `T${id}`,
    ticker: "XYZ",
    type: "C",
    strike: 100,
    status: pnl == null ? "open" : "closed",
    pnl,
    r,
    r_basis: basis,
    rule_results: rules,
    fills: [
      { side: "B", qty: 1, px: 1, ts: startIso, ts_pt: "06:40:10 PT" },
      { side: "S", qty: 1, px: 1, ts: endIso, ts_pt: "06:45:00 PT" },
    ],
  };
}

const t0 = "2026-01-05T14:30:00Z";
const day: DeskDayDetail = {
  date: "2026-01-05",
  daily_stop: -100,
  daily_target: 200,
  stats: { net: -80, n_closed: 3 },
  rule_results: [],
  stop_result: {
    limit: -100,
    hit: true,
    net: -80,
    hit_at_pt: "06:50:30 PT",
    trades_after: 2,
    trade_ids_after: [3, 2],
    blown_past: true,
  },
  positions: [
    pos(2, "2026-01-05T14:50:00Z", "2026-01-05T14:55:00Z", -30, -0.5, "assumed", [
      rr("before_cutoff", false),
      rr("call_source", true),
      rr("size_within_plan", null),
    ]),
    pos(1, "2026-01-05T14:35:00Z", "2026-01-05T14:40:00Z", 50, 1.25, "written", [
      rr("before_cutoff", true),
      rr("call_source", true),
    ]),
    pos(3, "2026-01-05T15:00:00Z", "2026-01-05T15:00:00Z", null, null, "assumed", [
      rr("before_cutoff", false),
      rr("daily_stop", false),
    ]),
  ],
};

const v = dayVerdict(day);
eq("net", v.net, -80);
eq("sum R skips missing", v.sumR, 0.75);
eq("R count", v.rCount, 2);
eq("assumed count", v.assumedCount, 1);
eq("chip labels", v.chips.map((c) => c.label), [
  "Daily stop hit 06:50 PT",
  "2 trades after stop",
  "Blown past the stop",
  "2 after cutoff",
  "Target not reached",
]);
eq("stop chip broken", v.chips[0].tone, "broken");
eq("target chip grey", v.chips[4].tone, "unknown");

const kept = dayVerdict({
  ...day,
  stats: { net: 250, n_closed: 3 },
  stop_result: {
    limit: -100,
    hit: true,
    net: 250,
    hit_at_pt: "06:50:30 PT",
    trades_after: 0,
    trade_ids_after: [],
    blown_past: false,
  },
  positions: [day.positions[1]],
});
eq("kept stop tone", kept.chips[0].tone, "kept");
eq("target reached", kept.chips[kept.chips.length - 1].label, "Target reached");
eq("no cutoff chip when none late", kept.chips.some((c) => c.key === "cutoff"), false);
eq("no positions -> sum R null", dayVerdict({ ...day, positions: [] }).sumR, null);
eq(
  "not-hit chip",
  dayVerdict({ ...day, stop_result: { ...day.stop_result!, hit: false } }).chips[0].label,
  "Daily stop not hit",
);

const rows = tradeRows(day.positions);
eq("rows in time order", rows.map((r) => r.tradeId), [1, 2, 3]);
eq("row time", rows[0].timePt, "06:40 PT");
eq("row assumed flag", rows.map((r) => r.assumed), [false, true, true]);
eq("broken only", rows[1].broken.map((r) => r.code), ["before_cutoff"]);
eq("kept count", rows[1].keptCount, 1);
eq("open row", rows[2].open, true);
eq("open row R null", rows[2].r, null);

const start = Date.parse(t0);
const bands = tradeBands(day.positions, start, start + 100 * 60_000);
eq("band left", bands[0].leftPct, 20);
eq("band width", bands[0].widthPct, 5);
eq("band tone", bands.map((b) => b.tone), ["loss", "gain", "flat"]);
eq("zero-length hold gets a minimum width", bands[2].widthPct, 0.6);
eq("no span -> no bands", tradeBands(day.positions, start, start), []);

eq("clock strips seconds", clockPt("07:12:30 PT"), "07:12 PT");
eq("clock empty", clockPt(null), "—");
eq(
  "flat line does not divide by zero",
  linePoints([{ ms: 0, close: 5 }, { ms: 10, close: 5 }], 10, 10, 0, 10),
  "0.0,10.0 10.0,10.0",
);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("dayPage checks passed");
