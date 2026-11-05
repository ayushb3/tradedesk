/**
 * Self-check for learnRoutes.ts (synthetic data only).
 * Run: npm run check:sim   (esbuild bundle -> node; there is no test runner)
 */
import {
  becauseFocus,
  becauseRuleText,
  lessonPath,
  practicePath,
  topicPath,
  tradeScenarios,
  trainingRedirect,
} from "./learnRoutes";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
}

// ---- old /training URLs land on their new homes ----
eq("drill player", trainingRedirect("/training/drills/12"), "/learn/practice/12");
eq("drill player keeps query", trainingRedirect("/training/drills/12", "?playlist=3,4&mood=calm"),
  "/learn/practice/12?playlist=3,4&mood=calm");
eq("drill lobby", trainingRedirect("/training/drills"), "/learn");
eq("lobby drops query", trainingRedirect("/training/drills", "?x=1"), "/learn");
eq("scenarios", trainingRedirect("/training/scenarios"), "/learn/scenarios");
eq("new scenario", trainingRedirect("/training/scenarios/new"), "/learn/scenarios/new");
eq("leaks", trainingRedirect("/training/leaks"), "/learn/leaks");
eq("progress trailing slash", trainingRedirect("/training/progress/"), "/learn/progress");
eq("unknown", trainingRedirect("/training/whatever/else"), "/learn");

// ---- paths ----
eq("practice", practicePath(5), "/learn/practice/5");
eq("practice query without ?", practicePath(5, "playlist=6"), "/learn/practice/5?playlist=6");
eq("topic", topicPath("patience-candles"), "/learn/topics/patience-candles");
eq("topic encoded", topicPath("a b"), "/learn/topics/a%20b");
eq("lesson", lessonPath(41), "/knowledge/lessons/41");

// ---- because ----
eq("rule text", becauseRuleText("exited_early"), "you exited early");
eq("unknown rule text", becauseRuleText("some_rule"), "some rule");
eq("focus none", becauseFocus(null), null);
eq("focus late", becauseFocus({ trade_id: 1, date: "2030-01-02", rule: "before_cutoff" }),
  "A setup after your trading window: is it a trade?");

// ---- drills from your trades ----
{
  const s = (id: number, source_type: string, created_at: string, source_trade_id: number | null = null) =>
    ({ id, source_type, created_at, source_trade_id });
  const got = tradeScenarios([
    s(1, "real", "2030-01-01T00:00:00Z"),
    s(2, "debrief", "2030-01-02T00:00:00Z"),
    s(3, "real", "2030-01-03T00:00:00Z", 77),
    s(4, "synthetic", "2030-01-04T00:00:00Z"),
    s(5, "debrief", "2030-01-03T00:00:00Z"),
  ]).map((x) => x.id);
  eq("trade scenarios newest first", got, [5, 3, 2]);
}

if (failures) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("learnRoutes.check: all ok");
