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
