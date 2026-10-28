"""Option-bar scale check at the fills, the SPX-on-SPY-proxy guard, and the v11 purge.

Synthetic fixtures only. Times are UTC; 13:30Z = market open (EDT) on the test day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text

from app.db.connection import init_db
from app.db.schema import option_bars_cache as cache_t
from app.ingest.imports import import_fills
from app.ingest.option_bars import _provider_series_matches_fills, get_option_bars_for_trade
from app.ingest.query import list_trades
from app.models.market import Candle
from app.models.trades import Fill, Side

UTC = timezone.utc
DAY = date(2026, 6, 10)
OPEN = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)


def _ts(minute: int) -> datetime:
    return OPEN + timedelta(minutes=minute)


def _bar(minute: int, o: float, h: float, l: float, c: float, sym="X", label=None) -> Candle:
    return Candle(symbol=sym, ts=_ts(minute), open=o, high=h, low=l, close=c, volume=10.0,
                  timeframe="1m", source_label=label)


class _Fills:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self, since=None):
        return list(self._fills)


class _Provider:
    """Fixed option bars (or None) and fixed underlying candles."""

    cache_source = "fake"

    def __init__(self, option_bars, underlying):
        self._ob, self._u = option_bars, underlying

    def get_option_bars(self, contract, day, timeframe="1m"):
        return self._ob

    def get_candles(self, symbol, day, timeframe="1m"):
        return list(self._u)


def _fill(i, symbol, side, price, minute, otype, strike, expiry="2026-06-10") -> Fill:
    return Fill(
        provider_fill_id=f"s-{symbol}-{i}", account="TEST", symbol=symbol, side=side,
        qty=1.0, price=price, ts=_ts(minute) + timedelta(seconds=20), fees=0.0,
        multiplier=100.0, option_type=otype, strike=strike, expiry=expiry, simulated=False,
    )


def _trade(engine, fills) -> int:
    import_fills(_Fills(fills), engine=engine)
    (t,) = list_trades(engine=engine)
    return t.id


# A 0DTE contract worth dollars all morning, cents by the late-day fills.
LATE = 380  # 19:50Z
_MORNING = [_bar(m, 15.0, 15.5, 14.5, 15.0) for m in range(0, 360)]
_LATE = [_bar(m, 0.40, 0.60, 0.30, 0.45) for m in range(360, 390)]
_LATE_FILLS = [(_ts(LATE), 0.35), (_ts(LATE + 2), 0.55)]


def test_cheap_late_fills_on_a_morning_expensive_contract_keep_provider_bars():
    # Whole-day median close is 15.0 vs fills ~0.45 (33x): the old median check
    # threw this real series away. Judged at the fills it is on scale.
    assert _provider_series_matches_fills(_MORNING + _LATE, _LATE_FILLS)


def test_series_off_scale_at_the_fills_is_rejected():
    # Deep-ITM-looking curve at the fills (~300 vs ~0.45): not the trade's contract scale.
    off = [_bar(m, 300.0, 301.0, 299.0, 300.0) for m in range(360, 390)]
    assert not _provider_series_matches_fills(off, _LATE_FILLS)


def test_no_provider_bar_near_any_fill_is_not_trusted():
    assert not _provider_series_matches_fills(_MORNING, _LATE_FILLS)


def test_provider_path_end_to_end_keeps_real_bars(temp_engine):
    tid = _trade(temp_engine, [
        _fill(1, "QQQ", Side.buy, 0.35, LATE, "PUT", 500.0),
        _fill(2, "QQQ", Side.sell, 0.55, LATE + 2, "PUT", 500.0),
    ])
    und = [_bar(m, 501.0, 501.5, 500.5, 501.0, sym="QQQ") for m in range(0, 390)]
    ob = get_option_bars_for_trade(tid, _Provider(_MORNING + _LATE, und), "1m", engine=temp_engine)
    assert ob is not None and ob.method == "provider" and ob.synthetic is False


def test_spx_contract_on_spy_proxy_gets_no_synthetic_series(temp_engine):
    # No option bars from the provider; the underlying comes back as the SPY proxy
    # (~1/10 of SPX). Pricing a 6000 put off it would give ~5,400 of intrinsic value.
    tid = _trade(temp_engine, [
        _fill(1, "SPXW", Side.buy, 0.35, LATE, "PUT", 6000.0),
        _fill(2, "SPXW", Side.sell, 0.55, LATE + 2, "PUT", 6000.0),
    ])
    proxy = [_bar(m, 600.0, 600.5, 599.5, 600.0, sym="SPXW", label="SPY (proxy)")
             for m in range(0, 390)]
    ob = get_option_bars_for_trade(tid, _Provider(None, proxy), "1m", engine=temp_engine)
    assert ob is None  # "no premium data", never an intrinsic-value curve
    with temp_engine.connect() as conn:
        assert conn.execute(select(cache_t)).fetchall() == []


def test_spx_contract_on_index_scale_underlying_still_gets_fallback(temp_engine):
    tid = _trade(temp_engine, [
        _fill(1, "SPXW", Side.buy, 0.35, LATE, "PUT", 6000.0),
        _fill(2, "SPXW", Side.sell, 0.55, LATE + 2, "PUT", 6000.0),
    ])
    index = [_bar(m, 6010.0, 6011.0, 6009.0, 6010.0, sym="SPXW") for m in range(0, 390)]
    ob = get_option_bars_for_trade(tid, _Provider(None, index), "1m", engine=temp_engine)
    assert ob is not None and ob.method == "bs_interp"
    assert max(b.close for b in ob.bars) < 100  # a premium, not intrinsic value


def test_v11_purges_synthetic_cache_rows_once(temp_engine):
    init_db(temp_engine)
    row = dict(contract_key="K", timeframe="1m", open=1.0, high=1.0, low=1.0, close=1.0,
               volume=1.0, source="fake")
    with temp_engine.begin() as conn:
        conn.execute(text("DELETE FROM schema_migrations WHERE version >= 11"))
        conn.execute(cache_t.insert().values(
            ts_utc=datetime(2026, 6, 10, 19, 50), synthetic=True, method="bs_interp", **row))
        conn.execute(cache_t.insert().values(
            ts_utc=datetime(2026, 6, 10, 19, 51), synthetic=False, method="provider", **row))
    init_db(temp_engine, force=True)  # applies the v11 purge
    with temp_engine.begin() as conn:
        left = conn.execute(select(cache_t.c.method)).scalars().all()
        assert left == ["provider"]
        conn.execute(cache_t.insert().values(
            ts_utc=datetime(2026, 6, 10, 19, 52), synthetic=True, method="bs_interp", **row))
    init_db(temp_engine, force=True)  # already at v11: new synthetic rows are kept
    with temp_engine.connect() as conn:
        assert sorted(conn.execute(select(cache_t.c.method)).scalars().all()) == [
            "bs_interp", "provider"]
