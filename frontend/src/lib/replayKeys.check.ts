/**
 * Self-check for the shared replay keymap (pure; no DOM).
 * Run: npm run check:sim
 */
import { replayKeyOf } from "./replayKeys";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

eq("space toggles", replayKeyOf({ key: " " }), { kind: "toggle" });
eq("right = one minute", replayKeyOf({ key: "ArrowRight" }), { kind: "step", dir: 1, fine: false });
eq("left = one minute back", replayKeyOf({ key: "ArrowLeft" }), { kind: "step", dir: -1, fine: false });
eq("shift+right = one second", replayKeyOf({ key: "ArrowRight", shiftKey: true }), { kind: "step", dir: 1, fine: true });
eq("shift+left = one second back", replayKeyOf({ key: "ArrowLeft", shiftKey: true }), { kind: "step", dir: -1, fine: true });
eq("up doubles", replayKeyOf({ key: "ArrowUp" }), { kind: "speed", dir: 1 });
eq("down halves", replayKeyOf({ key: "ArrowDown" }), { kind: "speed", dir: -1 });
eq("end skips", replayKeyOf({ key: "End" }), { kind: "end" });
eq("] skips a candle forward, [ back", [replayKeyOf({ key: "]" }), replayKeyOf({ key: "[" })], [
  { kind: "skip", dir: 1 },
  { kind: "skip", dir: -1 },
]);
eq("ctrl+[ passes through", replayKeyOf({ key: "[", ctrlKey: true }), null);
eq("ctrl/cmd/alt pass through", [
  replayKeyOf({ key: "ArrowRight", ctrlKey: true }),
  replayKeyOf({ key: " ", metaKey: true }),
  replayKeyOf({ key: "ArrowUp", altKey: true }),
], [null, null, null]);
eq("letters pass through", replayKeyOf({ key: "b" }), null);

if (failures) {
  console.error(`${failures} replayKeys check(s) failed`);
  process.exit(1);
}
console.log("replayKeys: all checks passed");
