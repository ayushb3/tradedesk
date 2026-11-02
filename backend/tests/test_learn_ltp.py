"""LTP detector (levels, trend, patience candle) on synthetic sessions."""
from __future__ import annotations

from datetime import date, timedelta

from app.learn import ltp
from app.learn.outcome import play_out
from tests import learn_synth as S

DAY = date(2026, 10, 27)


def _one(**kw):
    cands = ltp.ltp_pullback(S.ltp_session(DAY, **kw), "SPY", DAY.isoformat())
    assert len(cands) == 1
    return cands[0]


def test_auto_bars_follow_kcu_progression():
    bars = ltp.auto_bars(S.ltp_session(DAY), S.session_open(DAY))
    assert [b.minutes for b in bars[:15]] == [2] * 15            # first 30 minutes
    assert [b.minutes for b in bars[15:27]] == [5] * 12          # next hour
    assert {b.minutes for b in bars[27:]} == {10}                # then 10m
    assert bars[15].ts == S.session_open(DAY) + timedelta(minutes=30)


def test_shallow_pullback_with_contained_candle_is_an_enter_on_the_break():
    c = _one()
    assert c.enter and c.side == "long" and c.detector == "ltp_pullback"
    assert c.pause_at == S.session_open(DAY) + timedelta(minutes=65)   # the 5m candle's close
    f = c.facts
    assert f["bar_minutes"] == 5 and f["retracement"] < ltp.FIB_OFF
    assert c.entry == round(f["candle_high"] + 0.01, 2)                # stop order past the high
    assert c.stop == round(f["candle_low"] - 0.01, 2)                  # other side of the candle
    assert c.stop < c.entry < c.target
    assert (c.target - c.entry) >= ltp.MIN_RR * (c.entry - c.stop)
    assert f["confluence"]


def test_pullback_past_half_the_leg_is_a_wait():
    c = _one(deep=True)
    assert not c.enter and c.wait_reason == "pullback_past_half"
    assert c.entry is None and c.stop is None and c.target is None


def test_detector_never_looks_past_the_pause():
    full = _one()
    bars = [b for b in S.ltp_session(DAY) if b.ts < full.pause_at]
    cut = ltp.ltp_pullback(bars + S.ltp_session(DAY, fills=False)[len(bars):], "SPY", "x")
    assert cut and (cut[0].pause_at, cut[0].entry, cut[0].stop, cut[0].target) == \
        (full.pause_at, full.entry, full.stop, full.target)


def test_context_levels_count_as_confluence_and_targets():
    c = _one()
    ctx = ltp.SessionContext({"Premarket high": round(c.entry + 2.5 * (c.entry - c.stop), 2)})
    with_ctx = ltp.ltp_pullback(S.ltp_session(DAY), "SPY", DAY.isoformat(), ctx)[0]
    assert with_ctx.target == ctx.levels["Premarket high"]
    assert with_ctx.facts["target_name"] == "Premarket high"


def test_the_key_plays_out_from_the_trigger():
    c = _one()
    bars = S.ltp_session(DAY)
    win = play_out(bars, c.pause_at, c.pause_at + timedelta(minutes=90), c.side, c.stop,
                   c.target, c.entry)
    assert win.result == "target" and win.entry == c.entry
    lose = play_out(S.ltp_session(DAY, fills=False), c.pause_at, c.pause_at + timedelta(minutes=90),
                    c.side, c.stop, c.target, c.entry)
    assert lose.result == "no_fill"        # the candle broke the other way: never filled
