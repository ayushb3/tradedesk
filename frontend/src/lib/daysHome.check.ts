/**
 * Self-check for daysHome.ts (synthetic numbers only).
 * Run: npm run check:sim
 */
import type { DeskDaySummary, RuleResult, Trade } from "./api";
import {
  calendarFromDays,
  calendarMarks,
  groupByDay,
  lastTradingDay,
  ruleBreaks,
  ruleScoreboard,
  visibleColumns,
} from "./daysHome";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else {
    console.log(`ok   ${name}`);
  }
}

const rr = (code: RuleResult["code"], kept: boolean | null): RuleResult => ({ code, kept, detail: "" });
const day = (
  date: string,
  net: number,
  positions: number,
  results: RuleResult[],
  stop: Partial<NonNullable<DeskDaySummary["stop_result"]>> | null = null,
): DeskDaySummary => ({
  date,
  net,
  positions,
  rule_results: results,
  stop_result: stop
    ? { limit: -100, hit: true, net, trades_after: 0, trade_ids_after: [], blown_past: false, ...stop }
    : null,
});

const days = [
  day("2026-10-01", 40, 2, [rr("before_cutoff", true), rr("daily_stop", true), rr("call_source", null)]),
  day("2026-10-02", -120, 3, [rr("before_cutoff", false), rr("daily_stop", false), rr("call_source", true)], { trades_after: 1 }),
  day("2026-10-03", 0, 0, []),
  day("2026-10-04", -20, 1, [rr("before_cutoff", false), rr("daily_stop", true)]),
];

const sb = ruleScoreboard(days);
eq("traded days skip empty days", sb.tradedDays, 3);
const row = (c: string) => sb.rows.find((r) => r.code === c)!;
eq("cutoff kept/broken", [row("before_cutoff").kept, row("before_cutoff").broken], [1, 2]);
eq("stop kept/broken", [row("daily_stop").kept, row("daily_stop").broken], [2, 1]);
eq("call source unknown counted apart", [row("call_source").kept, row("call_source").unknown], [1, 2]);
eq("max loss has no results -> unknown", row("max_loss_written").unknown, 3);
eq("broken headline", sb.brokenTotal, 3);
eq("net rides along", sb.net, -100);

const marks = calendarMarks(days);
eq("marks skip clean and empty days", Object.keys(marks), ["2026-10-02", "2026-10-04"]);
eq("broken codes in rule order", marks["2026-10-02"].broken, ["before_cutoff", "daily_stop"]);
eq("stop breach outline", [marks["2026-10-02"].stopBreach, marks["2026-10-04"].stopBreach], [true, false]);
eq(
  "past the stop tolerance is a breach even without the rule row",
  calendarMarks([day("2026-10-05", -150, 1, [], { blown_past: true, past_tolerance: true })])["2026-10-05"].stopBreach,
  true,
);
eq(
  "past the stop but within tolerance is no breach",
  calendarMarks([day("2026-10-06", -120, 1, [], { blown_past: true, past_tolerance: false })])["2026-10-06"],
  undefined,
);

// A position held across two days is booked on its session day, never the exit day.
const cal = calendarFromDays([day("2026-10-01", 40, 1, []), day("2026-10-02", 0, 0, [])]);
eq("held trade counts on its session_date", Object.keys(cal), ["2026-10-01"]);
eq("calendar month net equals scoreboard net", Object.values(cal).reduce((a, c) => a + c.pnl, 0), ruleScoreboard([day("2026-10-01", 40, 1, []), day("2026-10-02", 0, 0, [])]).net);
eq("all-unknown rule has known 0", ruleScoreboard(days).rows.find((r) => r.code === "max_loss_written")!.known, 0);

const trade = (id: number, date: string, openIso: string, over: Partial<Trade> = {}): Trade =>
  ({
    id,
    symbol: "XYZ",
    contract_label: `XYZ ${id}`,
    session_date: date,
    open_ts: openIso,
    net_pnl: 10,
    avg_exit: null,
    r_multiple: null,
    tags: [],
    enrichment: { grade: null },
    rule_results: [],
    ...over,
  }) as unknown as Trade;

const trades = [
  trade(1, "2026-10-01", "2026-10-01T14:00:00Z"),
  trade(3, "2026-10-02", "2026-10-02T15:00:00Z", { net_pnl: -30, rule_results: [rr("before_cutoff", false)] }),
  trade(2, "2026-10-02", "2026-10-02T14:00:00Z", { net_pnl: 5, rule_results: [rr("before_cutoff", false), rr("call_source", false)] }),
];
const g = groupByDay(trades);
eq("groups newest day first", g.map((x) => x.date), ["2026-10-02", "2026-10-01"]);
eq("rows in time order within a day", g[0].trades.map((t) => t.id), [2, 3]);
eq("day net", g[0].net, -25);
eq("broken per day", [g[0].brokenCount, g[1].brokenCount], [3, 0]);
eq("empty columns dropped", [...visibleColumns(trades)], ["rules"]);
eq(
  "filled columns kept",
  [...visibleColumns([trade(9, "2026-10-01", "2026-10-01T14:00:00Z", { avg_exit: 1.2, r_multiple: -0.5 })])].sort(),
  ["exit", "r"],
);
const rb = ruleBreaks(trades);
eq("breaks ordered by count", rb.map((b) => [b.code, b.trades.length, b.days]), [["before_cutoff", 2, 1], ["call_source", 1, 1]]);
eq("break trades newest first", rb[0].trades.map((t) => t.id), [3, 2]);

// lastTradingDay: latest date that has positions (the zero-position day is skipped)
eq("lastTradingDay skips empty days", lastTradingDay(days), "2026-10-04");
eq("lastTradingDay ignores order", lastTradingDay([days[1], days[0], days[2]]), "2026-10-02");
eq("lastTradingDay none", lastTradingDay([]), null);

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("daysHome checks passed");
