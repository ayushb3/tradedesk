/**
 * Skip: move the playhead one whole candle of the chart's interval, landing on that candle's
 * close (its last minute on a minute player, its last second on a seconds clock). Step stays
 * the player's base unit (one minute; Shift: one second). Pure, so check:sim can run it.
 *
 * Candles are clock-aligned (a 5m candle opens on :00, :05, …), like the chart's roll-up.
 */

/** The second the playhead sits on at a candle's close: the candle's last `unitSec` slot. */
export const candleCloseSec = (open: number, barSec: number, unitSec: number): number =>
  open + barSec - Math.min(unitSec, barSec);

/**
 * Where a skip lands. `posSec` is the playhead (a minute open on a minute player, a second on
 * a seconds clock); `unitSec` is that player's unit (60 or 1). Forward: the close of the
 * current candle, or of the next one when already on a close. Back: the previous candle's
 * close, so `[` then → steps through the candle you skipped back over.
 */
export function skipTargetSec(posSec: number, barSec: number, dir: 1 | -1, unitSec: number): number {
  const bar = Math.max(barSec, unitSec);
  const open = Math.floor(posSec / bar) * bar;
  const close = candleCloseSec(open, bar, unitSec);
  if (dir > 0) return posSec >= close ? candleCloseSec(open + bar, bar, unitSec) : close;
  return candleCloseSec(open - bar, bar, unitSec);
}

/** "5m", "15s", "1h": the button label for a bar size. */
export function sizeLabel(sec: number): string {
  if (sec < 60) return `${sec}s`;
  if (sec % 3600 === 0) return `${sec / 3600}h`;
  return `${Math.round(sec / 60)}m`;
}

/**
 * A skip target on a seconds clock, kept inside the session: no earlier than the first bar's
 * open, no later than the last bar's last second (a clock that rides on minute bars would
 * otherwise land on the wrong side of the edge).
 */
export function clampSkipSec(sec: number, firstOpen: number, lastOpen: number, lastBarSec = 60): number {
  return Math.max(firstOpen, Math.min(sec, lastOpen + lastBarSec - 1));
}

/**
 * A skip target on a bar player: the last bar opening at or before `targetSec`, but always
 * at least one bar in the skip's direction, so a gap in the bars cannot stall it.
 */
export function skipIndex(opens: readonly number[], cur: number, targetSec: number, dir: 1 | -1): number {
  const n = opens.length;
  if (n === 0) return 0;
  let i = 0;
  while (i + 1 < n && opens[i + 1] <= targetSec) i++;
  if (dir > 0 && i <= cur) i = Math.min(cur + 1, n - 1);
  if (dir < 0 && i >= cur) i = Math.max(cur - 1, 0);
  return i;
}
