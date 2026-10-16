"""Correctness of the pure indicators against HAND-COMPUTED fixtures.

Every expected value here is computed by hand in the comments so the test is an
independent oracle, not a copy of the implementation.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.indicators.ema import ema
from app.indicators.rsi import rsi
from app.indicators.vwap import vwap
from app.models.market import Candle

UTC = ZoneInfo("UTC")


# --------------------------------------------------------------------------- #
# EMA
# --------------------------------------------------------------------------- #
def test_ema_hand_computed():
    # ema([1,2,3,4,5], 3): k = 2/(3+1) = 0.5
    #   out[0]=None, out[1]=None
    #   seed  = SMA(1,2,3) = 6/3 = 2.0            -> out[2]
    #   out[3]= 4*0.5 + 2.0*0.5 = 2.0 + 1.0 = 3.0
    #   out[4]= 5*0.5 + 3.0*0.5 = 2.5 + 1.5 = 4.0
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]


def test_ema_shorter_than_period_all_none():
    assert ema([1, 2], 5) == [None, None]


def test_ema_invalid_period():
    with pytest.raises(ValueError):
        ema([1, 2, 3], 0)


# --------------------------------------------------------------------------- #
# RSI (Wilder)
# --------------------------------------------------------------------------- #
def test_rsi_hand_computed_mixed():
    # xs = [10,11,10,12,13,12], period = 3. Changes: +1, -1, +2, +1, -1
    # Seed at idx 3 from first 3 changes: gains=1+2=3, losses=1
    #   avg_gain=1, avg_loss=1/3, rs=3       -> RSI = 100 - 100/4 = 75.0
    # idx 4 (+1): avg_gain=(1*2+1)/3=1, avg_loss=(1/3*2)/3=2/9, rs=4.5
    #   RSI = 100 - 200/11 = 900/11 = 81.81818...
    # idx 5 (-1): avg_gain=(1*2)/3=2/3, avg_loss=(2/9*2+1)/3=13/27, rs=18/13
    #   RSI = 100 - 1300/31 = 1800/31 = 58.06451...
    out = rsi([10, 11, 10, 12, 13, 12], 3)
    assert out[0] is None and out[1] is None and out[2] is None
    assert out[3] == pytest.approx(75.0, abs=1e-6)
    assert out[4] == pytest.approx(900 / 11, abs=1e-6)
    assert out[5] == pytest.approx(1800 / 31, abs=1e-6)


def test_rsi_all_gains_saturates_to_100():
    # Strictly rising -> avg_loss = 0 -> RSI = 100 at every defined bar.
    out = rsi([1, 2, 3, 4, 5, 6], 3)
    assert out[:3] == [None, None, None]
    assert out[3:] == [100.0, 100.0, 100.0]


def test_rsi_flat_series():
    # Flat series: all changes 0 -> avg_gain == avg_loss == 0. The implementation
    # short-circuits on avg_loss == 0 and returns 100.0 (see DEFECTS: arguably
    # should be 50/None). Asserting the ACTUAL behaviour.
    out = rsi([5, 5, 5, 5, 5], 3)
    assert out == [None, None, None, 100.0, 100.0]


def test_rsi_too_short_all_none():
    # n <= period -> all None (need `period` changes, i.e. period+1 points).
    assert rsi([1, 2, 3], 3) == [None, None, None]


# --------------------------------------------------------------------------- #
# VWAP
# --------------------------------------------------------------------------- #
def _c(ts_utc: datetime, high, low, close, volume) -> Candle:
    return Candle(
        symbol="TEST",
        ts=ts_utc,
        open=close,  # open unused by vwap
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_vwap_hand_computed_with_premarket_none():
    # One premarket bar (09:00 ET) then three regular-session bars.
    # June is EDT (UTC-4): 09:00 ET = 13:00 UTC, 09:30 = 13:30, etc.
    d = 2026, 6, 10
    pre = _c(datetime(*d, 13, 0, tzinfo=UTC), high=100, low=98, close=99, volume=999)
    # B 09:30: tp = (11+9+10)/3 = 10 ; vol 100
    b = _c(datetime(*d, 13, 30, tzinfo=UTC), high=11, low=9, close=10, volume=100)
    # C 09:31: tp = (13+11+12)/3 = 12 ; vol 300
    c = _c(datetime(*d, 13, 31, tzinfo=UTC), high=13, low=11, close=12, volume=300)
    # D 09:32: tp = (22+20+21)/3 = 21 ; vol 200
    dd = _c(datetime(*d, 13, 32, tzinfo=UTC), high=22, low=20, close=21, volume=200)

    out = vwap([pre, b, c, dd])  # include_premarket=False by default

    # Premarket bar is None and does NOT contribute to the running sums.
    assert out[0] is None
    # B: 1000 / 100 = 10.0
    assert out[1] == pytest.approx(10.0, abs=1e-9)
    # C: (1000 + 12*300) / (100+300) = 4600 / 400 = 11.5
    assert out[2] == pytest.approx(11.5, abs=1e-9)
    # D: (4600 + 21*200) / 600 = 8800 / 600 = 44/3
    assert out[3] == pytest.approx(8800 / 600, abs=1e-9)


def test_vwap_include_premarket_anchors_at_first_bar():
    d = 2026, 6, 10
    pre = _c(datetime(*d, 13, 0, tzinfo=UTC), high=11, low=9, close=10, volume=100)
    b = _c(datetime(*d, 13, 30, tzinfo=UTC), high=13, low=11, close=12, volume=300)
    out = vwap([pre, b], include_premarket=True)
    # Premarket now contributes: bar0 = 10.0, bar1 = (10*100 + 12*300)/400 = 11.5
    assert out[0] == pytest.approx(10.0, abs=1e-9)
    assert out[1] == pytest.approx(11.5, abs=1e-9)
