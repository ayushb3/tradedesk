"""What a trade plan actually did after the pause, on the drill's own 1m bars.

Used twice, both strictly AFTER the fact: the builder drops an enter drill whose answer key
would have been stopped out (a drill must never teach a losing key as "correct"), and the
answer endpoint shows the key's and your plan's results on the reveal.

Entry is the close of the last bar before the pause (the price you were looking at), or, when a
``trigger`` is given, a stop order at that price: it fills on the first bar that trades through
it within TRIGGER_MIN, and the plan is ``no_fill`` if the stop side trades first or nothing
crosses in time (the patience candle broke the other way). Each bar after the entry is checked
stop first, then target, so a bar that spans both counts as a stop (the conservative read of
1m data); that includes the bar that fills the trigger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Sequence

from pydantic import BaseModel

from app.models.market import Candle

Result = Literal["target", "stop", "open", "no_fill", "invalid"]
TRIGGER_MIN = 10   # a stop-order entry not filled within this many minutes is cancelled


class Outcome(BaseModel):
    result: Result
    at: str | None = None          # ISO UTC open time of the bar that settled it
    entry: float | None = None
    exit: float | None = None      # target / stop price, or the last close when still open
    r: float | None = None         # result in R (|entry - stop| = 1R)


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def play_out(bars: Sequence[Candle], pause_at: datetime, until: datetime, side: str | None,
             stop: float | None, target: float | None, trigger: float | None = None) -> Outcome:
    """Walk the bars in [pause_at, until] and report which of stop / target hit first."""
    pause, end = _utc(pause_at), _utc(until)
    before = [b for b in bars if _utc(b.ts) < pause]
    after = [b for b in bars if pause <= _utc(b.ts) <= end]
    if not before or side not in ("long", "short") or stop is None or target is None:
        return Outcome(result="invalid")
    entry = before[-1].close if trigger is None else trigger
    long_ = side == "long"
    if (long_ and not stop < entry < target) or (not long_ and not target < entry < stop):
        return Outcome(result="invalid", entry=round(entry, 2))
    risk = abs(entry - stop)

    def r_of(px: float) -> float:
        return round(((px - entry) if long_ else (entry - px)) / risk, 2)

    if trigger is not None:
        fill = None
        for i, b in enumerate(after):
            if (_utc(b.ts) - pause).total_seconds() >= TRIGGER_MIN * 60:
                break
            crossed = b.high >= trigger if long_ else b.low <= trigger
            if not crossed and (b.low <= stop if long_ else b.high >= stop):
                break
            if crossed:
                fill = i
                break
        if fill is None:
            return Outcome(result="no_fill", entry=round(entry, 2))
        after = after[fill:]

    for b in after:
        stopped = b.low <= stop if long_ else b.high >= stop
        hit = b.high >= target if long_ else b.low <= target
        at = _utc(b.ts).isoformat().replace("+00:00", "Z")
        if stopped:
            return Outcome(result="stop", at=at, entry=round(entry, 2), exit=stop, r=r_of(stop))
        if hit:
            return Outcome(result="target", at=at, entry=round(entry, 2), exit=target,
                           r=r_of(target))
    last = after[-1].close if after else entry
    return Outcome(result="open", entry=round(entry, 2), exit=round(last, 2), r=r_of(last))
