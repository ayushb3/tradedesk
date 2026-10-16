"""Reference-level correctness against independently computed expectations.

Uses deterministic MockProvider candles for a known day. Expected values are
computed here by an independent pass over the candle set (filtering on ET
minute-of-day), not by calling the same helpers the implementation uses.
"""
from __future__ import annotations

from app.indicators import levels as lv
from app.indicators._et import REG_CLOSE_MIN, REG_OPEN_MIN, et_minute_of_day

from .conftest import PRIOR_DAY, TEST_DAY, mock_candles

PREMARKET_OPEN_MIN = 4 * 60  # 04:00 ET


def _reg_hi_lo(candles):
    hi = lo = None
    for c in candles:
        m = et_minute_of_day(c.ts)
        if REG_OPEN_MIN <= m < REG_CLOSE_MIN:
            hi = c.high if hi is None else max(hi, c.high)
            lo = c.low if lo is None else min(lo, c.low)
    return hi, lo


def test_pdh_pdl_equal_prior_regular_extremes_and_constant():
    candles = mock_candles("AAPL", TEST_DAY)
    prior = mock_candles("AAPL", PRIOR_DAY)
    exp_hi, exp_lo = _reg_hi_lo(prior)

    pdh = lv.pdh(candles, prior)
    pdl = lv.pdl(candles, prior)

    assert len(pdh) == len(candles) == len(pdl)
    assert set(pdh) == {exp_hi}, "pdh must be constant == prior regular high"
    assert set(pdl) == {exp_lo}, "pdl must be constant == prior regular low"


def test_pmh_pml_equal_premarket_extremes_and_frozen_after_open():
    candles = mock_candles("AAPL", TEST_DAY)

    # Independent premarket (04:00-09:30 ET) high/low.
    exp_hi = exp_lo = None
    last_pre_idx = None
    for i, c in enumerate(candles):
        m = et_minute_of_day(c.ts)
        if PREMARKET_OPEN_MIN <= m < REG_OPEN_MIN:
            exp_hi = c.high if exp_hi is None else max(exp_hi, c.high)
            exp_lo = c.low if exp_lo is None else min(exp_lo, c.low)
            last_pre_idx = i

    pmh = lv.pmh(candles)
    pml = lv.pml(candles)

    # Value at the final premarket bar equals the full premarket extreme...
    assert pmh[last_pre_idx] == exp_hi
    assert pml[last_pre_idx] == exp_lo
    # ...and it is frozen for the entire regular + after-hours session.
    for i, c in enumerate(candles):
        if et_minute_of_day(c.ts) >= REG_OPEN_MIN:
            assert pmh[i] == exp_hi, f"pmh not frozen at idx {i}"
            assert pml[i] == exp_lo, f"pml not frozen at idx {i}"


def test_opening_range_equals_first_5min_and_freezes():
    candles = mock_candles("AAPL", TEST_DAY)
    or_minutes = 5
    or_end = REG_OPEN_MIN + or_minutes

    exp_hi = exp_lo = None
    for c in candles:
        m = et_minute_of_day(c.ts)
        if REG_OPEN_MIN <= m < or_end:
            exp_hi = c.high if exp_hi is None else max(exp_hi, c.high)
            exp_lo = c.low if exp_lo is None else min(exp_lo, c.low)

    orh = lv.or_high(candles, or_minutes)
    orl = lv.or_low(candles, or_minutes)

    for i, c in enumerate(candles):
        m = et_minute_of_day(c.ts)
        if m < REG_OPEN_MIN:
            assert orh[i] is None and orl[i] is None, f"OR must be None pre-open at {i}"
        elif m >= or_end:
            # Fully frozen at the window's extreme after 09:35.
            assert orh[i] == exp_hi, f"or_high not frozen at idx {i}"
            assert orl[i] == exp_lo, f"or_low not frozen at idx {i}"

    # Final frozen value equals the independently computed OR extreme.
    assert orh[-1] == exp_hi
    assert orl[-1] == exp_lo


def test_day_high_low_running_and_none_before_open():
    candles = mock_candles("AAPL", TEST_DAY)
    dh = lv.day_high(candles)
    dl = lv.day_low(candles)

    run_hi = run_lo = None
    for i, c in enumerate(candles):
        m = et_minute_of_day(c.ts)
        if m < REG_OPEN_MIN:
            assert dh[i] is None and dl[i] is None, f"day_high/low must be None pre-open at {i}"
            continue
        if REG_OPEN_MIN <= m < REG_CLOSE_MIN:
            run_hi = c.high if run_hi is None else max(run_hi, c.high)
            run_lo = c.low if run_lo is None else min(run_lo, c.low)
        # regular: running; after-hours: frozen at final regular extreme.
        assert dh[i] == run_hi, f"day_high mismatch at idx {i}"
        assert dl[i] == run_lo, f"day_low mismatch at idx {i}"
