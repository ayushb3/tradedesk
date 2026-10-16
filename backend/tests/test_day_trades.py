"""Journal day-drill-in tests (M7a) — mock mode (conftest forces it).

Two layers:
    - `downsample_closes` unit tests: window slicing, <=MAX_SPARK_POINTS bound,
      first/last preserved, empty-window -> None.
    - Day-trades assembly: list ordered by open_ts ASC, sparklines built from
      mock candles over the hold window, empty day -> [] (200).

The assembly is exercised through the same query + analytics functions the
route handler uses, against an isolated temp-file engine.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.analytics.review import MAX_SPARK_POINTS, downsample_closes
from app.ingest.imports import import_fills
from app.ingest.query import list_trades
from app.models.market import Candle
from app.models.trades import Fill, Side

UTC = timezone.utc
DAY = date(2026, 6, 10)
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # Wed, 09:30 ET


def _ts(minute: int) -> datetime:
    return BASE + timedelta(minutes=minute)


def _candle(minute: int, close: float, symbol: str = "RIVN") -> Candle:
    ts = _ts(minute)
    return Candle(
        symbol=symbol,
        ts=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000.0,
        timeframe="1m",
    )


# --------------------------------------------------------------------------- #
# downsample_closes unit tests
# --------------------------------------------------------------------------- #
def test_downsample_empty_candles_returns_none():
    assert downsample_closes([], BASE, _ts(10)) is None


def test_downsample_window_out_of_range_returns_none():
    # Candles all before the window -> zero in-window -> None.
    candles = [_candle(m, 10.0 + m) for m in range(5)]
    assert downsample_closes(candles, _ts(100), _ts(200)) is None


def test_downsample_all_when_under_max():
    candles = [_candle(m, 10.0 + m) for m in range(10)]
    spark = downsample_closes(candles, _ts(0), _ts(9))
    assert spark is not None
    assert len(spark.values) == 10
    assert spark.values[0] == 10.0
    assert spark.values[-1] == 19.0
    assert spark.symbol == "RIVN"
    assert spark.timeframe == "1m"


def test_downsample_bounds_to_max_points():
    # 500 candles in-window must collapse to <= MAX_SPARK_POINTS.
    candles = [_candle(m, 10.0 + m * 0.01) for m in range(500)]
    spark = downsample_closes(candles, _ts(0), _ts(499))
    assert spark is not None
    assert 0 < len(spark.values) <= MAX_SPARK_POINTS
    # First and last candle closes are preserved.
    assert spark.values[0] == pytest.approx(10.0)
    assert spark.values[-1] == pytest.approx(10.0 + 499 * 0.01)


def test_downsample_slices_to_hold_window():
    # Candles 0..59; hold window 10..20 -> only those 11 closes.
    candles = [_candle(m, float(m)) for m in range(60)]
    spark = downsample_closes(candles, _ts(10), _ts(20))
    assert spark is not None
    assert spark.values == [float(m) for m in range(10, 21)]


def test_downsample_open_trade_runs_through_last_candle():
    candles = [_candle(m, float(m)) for m in range(30)]
    spark = downsample_closes(candles, _ts(15), None)
    assert spark is not None
    assert spark.values[0] == 15.0
    assert spark.values[-1] == 29.0


# --------------------------------------------------------------------------- #
# Day-trades assembly
# --------------------------------------------------------------------------- #
class _FakeProvider:
    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills

    def get_fills(self, since=None) -> list[Fill]:
        return list(self._fills)


def _fill(i: int, symbol: str, side: Side, qty: float, price: float, minute: int) -> Fill:
    return Fill(
        provider_fill_id=f"f-{symbol}-{i:04d}",
        account="TEST",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts=_ts(minute),
        fees=0.0,
        simulated=False,
    )


def _import(fills: list[Fill], engine) -> None:
    import_fills(_FakeProvider(fills), engine=engine)


def _day_cards(engine):
    """Assemble day cards the way the route handler does, using mock candles."""
    from app.analytics.review import downsample_closes as _ds
    from app.config import Settings
    from app.models.review import DayTradeCard
    from app.providers.mock import MockProvider

    provider = MockProvider(Settings())
    trades = sorted(list_trades(from_=DAY, to=DAY, engine=engine), key=lambda t: t.open_ts)
    candle_cache: dict[str, list] = {}
    cards = []
    for t in trades:
        if t.symbol not in candle_cache:
            candle_cache[t.symbol] = provider.get_candles(t.symbol, DAY, "1m")
        cards.append(
            DayTradeCard(
                id=t.id,
                symbol=t.symbol,
                contract_label=t.contract_label,
                direction=t.direction,
                net_pnl=t.net_pnl,
                pnl_pct=t.pnl_pct,
                r_multiple=t.r_multiple,
                hold_time_s=t.hold_time_s,
                grade=t.enrichment.grade if t.enrichment else None,
                open_ts=t.open_ts,
                close_ts=t.close_ts,
                simulated=t.simulated,
                sparkline=_ds(candle_cache[t.symbol], t.open_ts, t.close_ts),
            )
        )
    return cards


def test_day_trades_ordered_by_open_ts_asc(temp_engine):
    # HOOD opens later than RIVN; both close the same day.
    _import(
        [
            _fill(1, "RIVN", Side.buy, 100, 13.40, 0),
            _fill(2, "RIVN", Side.sell, 100, 13.72, 20),
            _fill(3, "HOOD", Side.buy, 50, 20.0, 40),
            _fill(4, "HOOD", Side.sell, 50, 21.0, 60),
        ],
        temp_engine,
    )
    cards = _day_cards(temp_engine)
    assert [c.symbol for c in cards] == ["RIVN", "HOOD"]
    assert cards[0].open_ts <= cards[1].open_ts


def test_day_trades_sparkline_within_bound(temp_engine):
    _import(
        [
            _fill(1, "RIVN", Side.buy, 100, 13.40, 0),
            _fill(2, "RIVN", Side.sell, 100, 13.72, 120),  # 2h hold -> many 1m bars
        ],
        temp_engine,
    )
    cards = _day_cards(temp_engine)
    assert len(cards) == 1
    spark = cards[0].sparkline
    assert spark is not None
    assert 0 < len(spark.values) <= MAX_SPARK_POINTS


def test_empty_day_returns_no_cards(temp_engine):
    # No fills imported -> no trades on the day.
    cards = _day_cards(temp_engine)
    assert cards == []


def test_empty_day_http(isolated_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes.review import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    resp = client.get("/api/journal/days/2026-06-10/trades")
    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-06-10"
    assert body["trades"] == []


# --------------------------------------------------------------------------- #
# Regression: a symbol the provider can't fetch must degrade, not 502
# (live-only bug — an index-option root like SPXW is rejected as US_STOCK).
# --------------------------------------------------------------------------- #
class _RaisingProvider:
    """Serves normal mock candles, but raises WebullError for one symbol."""

    def __init__(self, bad_symbol: str) -> None:
        self._bad = bad_symbol
        from app.config import Settings
        from app.providers.mock import MockProvider

        self._mock = MockProvider(Settings())

    def get_candles(self, symbol: str, day, timeframe: str = "1m"):
        from app.providers.webull.errors import WebullError

        if symbol == self._bad:
            raise WebullError("INVALID_SYMBOL")
        return self._mock.get_candles(symbol, day, timeframe)


def test_day_trades_degrades_unfetchable_symbol(isolated_db):
    """One un-fetchable symbol -> 200 with that card's sparkline None, others OK."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import provider_dep
    from app.api.routes.review import router

    # Two trades on the same day: SPXW (un-fetchable) and RIVN (fine).
    import_fills(
        _FakeProvider(
            [
                _fill(1, "SPXW", Side.buy, 1, 5.0, 0),
                _fill(2, "SPXW", Side.sell, 1, 6.0, 20),
                _fill(3, "RIVN", Side.buy, 100, 13.40, 40),
                _fill(4, "RIVN", Side.sell, 100, 13.72, 60),
            ]
        )
    )

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[provider_dep] = lambda: _RaisingProvider("SPXW")
    client = TestClient(app)

    resp = client.get("/api/journal/days/2026-06-10/trades")
    assert resp.status_code == 200
    cards = {c["symbol"]: c for c in resp.json()["trades"]}
    assert set(cards) == {"SPXW", "RIVN"}
    assert cards["SPXW"]["sparkline"] is None  # degraded, not 502
    assert cards["RIVN"]["sparkline"] is not None  # unaffected


def test_trade_review_degrades_unfetchable_share_symbol(isolated_db):
    """Share-trade cockpit: un-fetchable underlying -> 200 with excursion None."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.deps import provider_dep
    from app.api.routes.review import router
    from app.ingest.query import list_trades

    import_fills(
        _FakeProvider(
            [
                _fill(1, "SPXW", Side.buy, 1, 5.0, 0),
                _fill(2, "SPXW", Side.sell, 1, 6.0, 20),
            ]
        )
    )
    trade_id = list_trades(from_=DAY, to=DAY)[0].id

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[provider_dep] = lambda: _RaisingProvider("SPXW")
    client = TestClient(app)

    resp = client.get(f"/api/trades/{trade_id}/review")
    assert resp.status_code == 200
    assert resp.json()["excursion"] is None
