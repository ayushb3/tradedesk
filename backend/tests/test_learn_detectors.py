"""Learn setup detectors on hand-built candles (synthetic)."""
from __future__ import annotations

from datetime import date, timedelta

from app.learn import detectors as D
from tests import learn_synth as S

DAY = date(2026, 10, 14)
DS = DAY.isoformat()


def test_level_touches_finds_return_to_twice_touched_resistance():
    for reject in (True, False):
        out = D.level_touches(S.resistance_session(DAY, reject), "SPY", DS)
        assert len(out) == 1
        c = out[0]
        assert (c.detector, c.ticker, c.date, c.side) == ("level_touches", "SPY", DS, "short")
        assert c.setup_idx == 62 and abs(c.level - 600.05) < 0.01
        assert c.facts["touches"] == 2
        assert c.enter is reject
        # the pause is right after the setup bar
        assert c.pause_at == S.session_open(DAY) + timedelta(minutes=63)


def test_level_touches_answer_key_geometry_when_entering():
    c = D.level_touches(S.resistance_session(DAY, True), "SPY", DS)[0]
    assert c.stop > c.level > c.target          # short: stop above, target below
    assert (c.level - c.target) >= D.MIN_RR * (c.stop - c.level) - 1e-9
    assert D.level_touches(S.resistance_session(DAY, False), "SPY", DS)[0].stop is None


def test_level_touches_needs_two_touches():
    # a single peak at 600 then a return: not a twice-touched level
    wp = [(0, 597.0), (30, 600.0), (45, 597.5), (62, 599.6), (63, 598.0), (80, 594.0), (389, 594.0)]
    out = D.level_touches(S.full_day(wp, DAY), "SPY", DS)
    assert [c for c in out if c.facts["level_source"] == "pivot" and c.facts["touches"] < 2] == []


def test_break_retest_finds_hold_and_confirmation():
    out = D.break_retest(S.break_retest_session(DAY, True), "SPY", DS)
    assert len(out) == 1
    c = out[0]
    assert (c.side, c.setup_idx, c.enter) == ("long", 76, True)
    assert c.facts["break_idx"] == 58
    assert c.stop < c.level < c.target
    wait = D.break_retest(S.break_retest_session(DAY, False), "SPY", DS)[0]
    assert wait.enter is False and wait.stop is None and wait.target is None


def test_break_retest_ignores_a_failed_break():
    # breaks above 600 then falls straight back under it: no retest that holds
    wp = [(0, 597.0), (20, 600.0), (30, 598.0), (40, 600.0), (50, 597.8), (58, 600.4),
          (60, 599.0), (80, 596.0), (389, 596.0)]
    assert D.break_retest(S.full_day(wp, DAY), "SPY", DS) == []


def test_patience_candle_at_level_inside_bar_holding_support():
    out = D.patience_candle_at_level(S.patience_session(DAY, True), "SPY", DS)
    assert len(out) == 1
    c = out[0]
    assert (c.side, c.setup_idx, c.enter) == ("long", 50, True)
    assert c.facts["inside_bar"] and c.facts["holds_level"]
    assert c.stop < c.level < c.target


def test_patience_candle_that_loses_the_level_is_a_wait():
    c = D.patience_candle_at_level(S.patience_session(DAY, False), "SPY", DS)[0]
    assert c.enter is False and c.facts["holds_level"] is False


def test_patience_needs_a_small_candle():
    closes = S.path([(0, 598.0), (12, 595.2), (22, 597.5), (34, 599.5), (47, 596.0), (49, 595.4),
                     (51, 596.0), (70, 600.0), (389, 606.0)], 390)
    big = S.candles(closes, DAY, overrides={50: (595.4, 596.4, 594.9, 595.9)})  # wide bar
    assert D.patience_candle_at_level(big, "SPY", DS) == []


def test_candidates_never_use_future_bars():
    """Truncating the session right after the setup bar yields the same candidate."""
    full = S.resistance_session(DAY, True)
    a = D.level_touches(full, "SPY", DS)[0]
    cut = full[: a.setup_idx + 1 + D.LEAVE_AFTER]
    b = D.level_touches(cut, "SPY", DS)
    assert [(x.setup_idx, x.level, x.stop, x.target) for x in b] == \
        [(a.setup_idx, a.level, a.stop, a.target)]


def test_candidate_dict_shape():
    d = D.level_touches(S.resistance_session(DAY, True), "SPY", DS)[0].to_dict()
    for k in ("detector", "ticker", "date", "pause_at", "level", "side", "strength"):
        assert k in d
    assert d["pause_at"].endswith("Z") and 0 <= d["strength"] <= 1


def test_flat_session_has_no_candidates():
    flat = S.candles([600.0] * 390, DAY)
    assert D.detect_all(flat, "SPY", DS) == []


def _stop_buffer_ok(c):
    ext = c.facts["setup_high"] if c.side == "short" else c.facts["setup_low"]
    anchor = max(ext, c.level) if c.side == "short" else min(ext, c.level)
    buf = abs(c.stop - anchor)
    want = max(D.ATR_MULT * c.facts["atr14"], D.BUFFER_PCT * c.facts["entry"])
    assert abs(buf - want) < 0.011                      # rounding to cents only
    assert buf >= D.BUFFER_PCT * c.facts["entry"] - 0.011
    return buf


def test_stop_sits_past_the_further_of_extreme_and_level_plus_a_real_buffer():
    for c in (D.level_touches(S.resistance_session(DAY, True), "SPY", DS)[0],
              D.break_retest(S.break_retest_session(DAY, True), "SPY", DS)[0],
              D.patience_candle_at_level(S.patience_session(DAY, True), "SPY", DS)[0]):
        assert _stop_buffer_ok(c) > 0.1                 # never "2 cents past the level"
        assert abs(c.level - c.stop) > 0.1


def test_degenerate_stop_regression_extreme_barely_past_the_level():
    # the setup candle's high is only 2 cents above the level: the stop must still be a buffer away
    sess = S.resistance_session(DAY, True)
    b = sess[62]
    L = D.level_touches(sess, "SPY", DS)[0].level
    sess[62] = b.model_copy(update={"high": round(L + 0.02, 2), "open": L - 0.1,
                                    "low": L - 0.9, "close": L - 0.8})
    c = D.level_touches(sess, "SPY", DS)[0]
    assert c.enter and c.stop - L >= 0.18


def test_candidate_rejected_when_reward_to_risk_after_the_buffer_is_too_small(monkeypatch):
    assert D.level_touches(S.resistance_session(DAY, True), "SPY", DS)       # baseline: fires
    monkeypatch.setattr(D, "MIN_RR", 40.0)                                   # no level pays 40R
    assert D.level_touches(S.resistance_session(DAY, True), "SPY", DS) == []
    # a wait candidate has no target, so it is not subject to the R/R rule
    assert D.level_touches(S.resistance_session(DAY, False), "SPY", DS)


def test_wait_flag_and_reasons():
    ok = D.patience_candle_at_level(S.patience_session(DAY, True), "SPY", DS)[0]
    assert ok.enter and not ok.is_wait and ok.wait_reason is None
    thru = D.patience_candle_at_level(S.patience_session(DAY, False), "SPY", DS)[0]
    assert thru.is_wait and thru.wait_reason == "closed_through_level"
    assert D.level_touches(S.resistance_session(DAY, False), "SPY", DS)[0].wait_reason == "no_rejection"
    assert D.break_retest(S.break_retest_session(DAY, False), "SPY", DS)[0].wait_reason == "no_confirmation"


def test_patience_at_a_single_touch_level_is_a_wait():
    wp = [(0, 598.0), (6, 593.0), (16, 597.5), (26, 595.2), (36, 599.5), (57, 596.0), (58, 595.5),
          (59, 595.4), (60, 595.5), (61, 596.0), (80, 600.0), (110, 602.0), (389, 606.0)]
    over = {59: (595.9, 596.2, 595.05, 595.4), 60: (595.44, 595.55, 595.33, 595.5)}
    out = D.patience_candle_at_level(S.full_day(wp, DAY, overrides=over), "SPY", DS)
    assert len(out) == 1
    c = out[0]
    assert c.facts["touches"] == 1 and c.facts["holds_level"] and c.facts["inside_bar"]
    assert c.is_wait and c.wait_reason == "single_touch" and c.stop is None


def test_candidate_rejected_when_the_next_level_is_absurdly_far(monkeypatch):
    c = D.level_touches(S.resistance_session(DAY, True), "SPY", DS)[0]
    rr = abs(c.target - c.facts["entry"]) / abs(c.facts["entry"] - c.stop)
    assert D.MIN_RR <= rr <= D.MAX_RR
    monkeypatch.setattr(D, "MAX_RR", rr - 0.5)
    assert D.level_touches(S.resistance_session(DAY, True), "SPY", DS) == []
