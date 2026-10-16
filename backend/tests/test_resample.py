"""Unit tests for mock-provider timeframe resampling (replay polish)."""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from app.providers.mock.synthetic import generate_candles, resample_candles

TEST_DAY = date(2026, 6, 10)  # Wednesday
ET = ZoneInfo("America/New_York")


def _base():
    return generate_candles("AAPL", TEST_DAY, timeframe="1m", tz="America/New_York")


def test_1m_passthrough_relabels_only():
    base = _base()
    out = resample_candles(base, "1m")
    assert len(out) == len(base)
    assert all(c.timeframe == "1m" for c in out)


def test_5m_buckets_align_to_clock_and_reduce_count():
    base = _base()
    out = resample_candles(base, "5m", tz="America/New_York")
    # 960 1m bars → 192 5m bars.
    assert len(out) == 192
    assert all(c.timeframe == "5m" for c in out)
    # Every bucket opens on a 5-minute ET boundary.
    for c in out:
        et_min = c.ts.astimezone(ET).minute
        assert et_min % 5 == 0


def test_ohlcv_aggregation_matches_constituent_1m_bars():
    base = _base()
    out = resample_candles(base, "5m", tz="America/New_York")
    # Reconstruct the first 5m bar from the first 5 one-minute bars.
    first5 = base[:5]
    agg = out[0]
    assert agg.open == first5[0].open
    assert agg.close == first5[-1].close
    assert agg.high == max(b.high for b in first5)
    assert agg.low == min(b.low for b in first5)
    assert agg.volume == sum(b.volume for b in first5)
    assert agg.ts == first5[0].ts


def test_1d_collapses_to_single_bar():
    base = _base()
    out = resample_candles(base, "1d", tz="America/New_York")
    assert len(out) == 1
    assert out[0].open == base[0].open
    assert out[0].close == base[-1].close
    assert out[0].high == max(b.high for b in base)
    assert out[0].low == min(b.low for b in base)
