/**
 * Self-check for the Legend-look helpers (pure; no DOM).
 * Run: npm run check:sim
 */
import { closeAtOrBefore, formatVolume, optionTag, optionTagFromLabel, pillPctText, pillQtyText, positionPct, volumeBars } from "./chartLook";

let failures = 0;
function eq(name: string, got: unknown, want: unknown) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failures++;
    console.error(`FAIL ${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  } else console.log(`ok   ${name}`);
}

const look = { up: "U", down: "D", grey: "G" };
const rows = [
  { time: 60, open: 1, close: 2 },
  { time: 120 },
  { time: 180, open: 3, close: 2 },
  { time: 240, open: 2, close: 2 },
];
const vol = new Map([[60, 100], [180, 50]]);
eq(
  "volume: one bar per drawn candle, colored by direction, missing volume = 0",
  volumeBars(rows, (t) => vol.get(t), look),
  [
    { time: 60, value: 100, color: "U" },
    { time: 180, value: 50, color: "D" },
    { time: 240, value: 0, color: "U" },
  ],
);
eq(
  "volume: grey after the exit",
  volumeBars(rows, (t) => vol.get(t), { ...look, greyAfterSec: 60 }).map((b) => b.color),
  ["U", "G", "G"],
);
eq("volume: NaN and negatives read 0", volumeBars([{ time: 1, open: 1, close: 1 }], () => NaN, look)[0].value, 0);

eq("fmt 870", formatVolume(870), "870");
eq("fmt 34.5K", formatVolume(34_520), "34.5K");
eq("fmt 1.20M", formatVolume(1_204_000), "1.20M");
eq("fmt null", formatVolume(null), "—");

eq("pct long up", positionPct(2, 2.5, 1), 25);
eq("pct long down", positionPct(2, 1.5, 3), -25);
eq("pct short gains when price falls", positionPct(10, 9, -1), 10);
eq("pct no mark", positionPct(2, null, 1), null);
eq("pct flat", positionPct(2, 2, 0), null);
eq("pill pct up", pillPctText(17.0812), "▲17.08%");
eq("pill pct down", pillPctText(-3.2), "▼3.20%");
eq("pill pct zero", pillPctText(0.001), "0.00%");
eq("pill pct none", pillPctText(null), "—");
eq("pill qty", [pillQtyText(10), pillQtyText(-2)], ["+10", "-2"]);
eq("option tag", [optionTag("2026-03-13", "call"), optionTag("2026-03-13", "put")], ["03/13 Call", "03/13 Put"]);

eq("tag from label", [optionTagFromLabel("SPY 600C 03/13"), optionTagFromLabel("SPXW 7025P 03/13"), optionTagFromLabel("SPY"), optionTagFromLabel(null)], ["03/13 Call", "03/13 Put", null, null]);

const cs = [
  { ts: "2026-03-13T14:30:00Z", close: 1 },
  { ts: "2026-03-13T14:31:00Z", close: 2 },
  { ts: "2026-03-13T14:32:00Z", close: 3 },
];
const s31 = Date.parse("2026-03-13T14:31:00Z") / 1000;
eq("close at or before", closeAtOrBefore(cs, s31 + 30), 2);
eq("close before first", closeAtOrBefore(cs, s31 - 120), null);

if (failures) {
  console.error(`${failures} chartLook check(s) failed`);
  process.exit(1);
}
console.log("chartLook checks passed");
