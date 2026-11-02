"""Learn outcome replay + the builder's outcome and no-repeat gates (synthetic bars only)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.learn import build as B
from app.learn import detectors as D
from app.learn.outcome import play_out
from app.models.market import Candle

T0 = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)


def bars(ohlc: list[tuple[float, float, float, float]]) -> list[Candle]:
    return [Candle(symbol="QQQ", ts=T0 + timedelta(minutes=i), open=o, high=h, low=l, close=c,
                   volume=1.0) for i, (o, h, l, c) in enumerate(ohlc)]


# bar 0 is the setup bar (close 100); the pause is bar 1's open.
PAUSE = T0 + timedelta(minutes=1)
UNTIL = T0 + timedelta(minutes=30)
UP = bars([(100, 100.1, 99.9, 100)] + [(100 + i * .2, 100 + i * .2 + .1, 100 + i * .2 - .1,
                                         100 + (i + 1) * .2) for i in range(10)])


def test_long_plan_hits_target_and_reports_r():
    o = play_out(UP, PAUSE, UNTIL, "long", 99.5, 101.0)
    assert o.result == "target" and o.entry == 100 and o.exit == 101.0 and o.r == 2.0
    assert o.at and o.at.endswith("Z")


def test_short_plan_into_a_rally_is_stopped():
    o = play_out(UP, PAUSE, UNTIL, "short", 100.4, 98.0)
    assert o.result == "stop" and o.r == -1.0


def test_bar_spanning_stop_and_target_counts_as_stop():
    b = bars([(100, 100.1, 99.9, 100), (100, 101.5, 99.0, 100)])
    assert play_out(b, PAUSE, UNTIL, "long", 99.5, 101.0).result == "stop"


def test_open_and_invalid():
    flat = bars([(100, 100.1, 99.9, 100)] * 5)
    assert play_out(flat, PAUSE, UNTIL, "long", 99.0, 102.0).result == "open"
    assert play_out(flat, PAUSE, UNTIL, "long", 101.0, 102.0).result == "invalid"  # stop above entry
    assert play_out(flat, PAUSE, UNTIL, None, None, None).result == "invalid"


def test_bars_after_until_are_ignored():
    o = play_out(UP, PAUSE, T0 + timedelta(minutes=2), "long", 99.5, 101.0)
    assert o.result == "open"


def _cand(enter: bool, side="short", stop=100.4, target=98.0) -> D.Candidate:
    return D.Candidate(detector="break_retest", ticker="QQQ", date="2026-10-14", pause_at=PAUSE,
                       level=100.3, side=side, strength=0.9, enter=enter,
                       stop=stop if enter else None, target=target if enter else None)


def test_builder_drops_an_enter_key_that_gets_stopped_but_keeps_waits():
    assert not B.key_holds_up(_cand(True), UP)                                  # short into a rally
    assert B.key_holds_up(_cand(True, "long", 99.5, 101.0), UP)                 # long to target
    assert B.key_holds_up(_cand(False), UP)                                     # a wait never fails


def test_retest_must_actually_reach_the_level():
    # The QQQ 10-26 shape: the retest bar stops 0.05% short of the level. That is "near" it,
    # not a retest, so no candidate at that bar.
    from tests import learn_synth as S
    day = date(2026, 10, 14)
    base = S.break_retest_session(day, True)
    found = D.break_retest(base, "SPY", day.isoformat())
    assert found, "the synthetic retest itself must still be found"
    c = found[0]
    j, L, b = c.setup_idx, c.level, base[j := c.setup_idx]
    gap = L * 0.0005
    if c.side == "short":
        bar = b.model_copy(update={"high": L - gap, "open": min(b.open, L - gap),
                                   "close": min(b.close, L - gap - 0.01)})
    else:
        bar = b.model_copy(update={"low": L + gap, "open": max(b.open, L + gap),
                                   "close": max(b.close, L + gap + 0.01)})
    moved = base[:j] + [bar] + base[j + 1:]
    assert not [x for x in D.break_retest(moved, "SPY", day.isoformat()) if x.setup_idx == j]
