"""Option premium bars + review-endpoint option population (M7b) — mock mode.

Covers issue #37:
    - BS math: monotonicity + implied-vol round-trip.
    - MockProvider.get_option_bars: deterministic provider path.
    - Synthetic BS fallback: curve passes through the recorded fill prices.
    - `GET /api/trades/{id}/option-bars`: provider path, 404s, cache reuse.
    - Review endpoint: option block + premium_bars + MFE/MAE populated for an
      option trade; underlying-basis MFE/MAE for a share trade.

Option trades are built through the real FIFO importer (option_type/strike/expiry
on the fills) into an isolated engine — the same path the app uses.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.analytics.options import (
    bs_interp_premium_bars,
    bs_price,
    compute_excursion,
    implied_vol,
    synthesize_mock_premium_bars,
    time_to_expiry_years,
)
from app.config import Settings
from app.ingest.imports import import_fills
from app.ingest.option_bars import get_option_bars_for_trade
from app.ingest.query import get_trade, get_trade_option_contract, list_trades
from app.models.market import Candle, OptionContract
from app.models.trades import Direction, Fill, Side
from app.providers.mock import MockProvider

UTC = timezone.utc
DAY = date(2026, 6, 10)  # Wed (EDT); 09:30 ET == 13:30 UTC
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)
EXPIRY = "2026-07-11"


def _ts(minute: int) -> datetime:
    return BASE + timedelta(minutes=minute)


class _FillProvider:
    """DataProvider stand-in for the importer: only get_fills is used."""

    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills

    def get_fills(self, since=None) -> list[Fill]:
        return list(self._fills)


class _NoOptionBarsProvider:
    """Real mock candles for the underlying, but no option-bar source.

    Forces the server-side synthetic BS fallback (mimics the webull provider,
    whose get_option_bars is permanently None).
    """

    def __init__(self) -> None:
        self._mock = MockProvider(Settings())

    def get_candles(self, symbol, day, timeframe="1m"):
        return self._mock.get_candles(symbol, day, timeframe)

    def get_option_bars(self, contract, day, timeframe="1m"):
        return None


def _ofill(i, symbol, side, qty, price, minute, otype, strike, expiry=EXPIRY) -> Fill:
    return Fill(
        provider_fill_id=f"o-{symbol}-{i:04d}",
        account="TEST",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts=_ts(minute),
        fees=0.0,
        multiplier=100.0,
        option_type=otype,
        strike=strike,
        expiry=expiry,
        simulated=False,
    )


def _import(fills, engine) -> int:
    import_fills(_FillProvider(fills), engine=engine)
    trades = list_trades(engine=engine)
    assert len(trades) == 1
    return trades[0].id


# --------------------------------------------------------------------------- #
# Pure BS math
# --------------------------------------------------------------------------- #
def test_bs_call_monotonic_in_underlying():
    T = 5 / 365
    lo = bs_price(90, 100, T, 0.5, "CALL")
    hi = bs_price(110, 100, T, 0.5, "CALL")
    assert 0 <= lo < hi


def test_bs_put_decreasing_in_underlying():
    T = 5 / 365
    assert bs_price(90, 100, T, 0.5, "PUT") > bs_price(110, 100, T, 0.5, "PUT")


def test_implied_vol_round_trips():
    S, K, T = 100.0, 100.0, 10 / 365
    target = bs_price(S, K, T, 0.42, "CALL")
    iv = implied_vol(target, S, K, T, "CALL")
    assert iv == pytest.approx(0.42, abs=1e-3)


def test_time_to_expiry_positive_and_decreasing():
    early = time_to_expiry_years(_ts(0), EXPIRY)
    later = time_to_expiry_years(_ts(60), EXPIRY)
    assert early > later > 0


# --------------------------------------------------------------------------- #
# MFE / MAE
# --------------------------------------------------------------------------- #
def test_compute_excursion_long():
    bars = [
        Candle(symbol="X", ts=_ts(m), open=3.0, high=3.0 + m, low=3.0 - 0.5 * m,
               close=3.0, volume=1.0)
        for m in range(3)
    ]  # high peaks at 3+2=5, low bottoms at 3-1=2
    res = compute_excursion(bars, entry_price=3.0, is_long=True,
                            open_ts=_ts(0), close_ts=_ts(2))
    assert res is not None
    mfe, mae, mfe_pct, mae_pct = res
    assert mfe == pytest.approx(2.0)
    assert mae == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Mock provider path (deterministic, method="provider")
# --------------------------------------------------------------------------- #
def test_mock_provider_option_bars_deterministic():
    contract = OptionContract(underlying="SPY", option_type="CALL", strike=540, expiry=EXPIRY)
    p = MockProvider(Settings())
    a = p.get_option_bars(contract, DAY, "1m")
    b = p.get_option_bars(contract, DAY, "1m")
    underlying = p.get_candles("SPY", DAY, "1m")
    assert a and len(a) == len(underlying)
    assert [c.close for c in a] == [c.close for c in b]  # reproducible
    assert all(c.high >= c.low >= 0 for c in a)
    assert all(c.symbol == contract.contract_key for c in a)


def test_option_bars_endpoint_provider_path(temp_engine):
    tid = _import(
        [
            _ofill(1, "SPY", Side.buy, 5, 3.00, 5, "CALL", 540.0),
            _ofill(2, "SPY", Side.sell, 5, 4.50, 65, "CALL", 540.0),
        ],
        temp_engine,
    )
    ob = get_option_bars_for_trade(tid, MockProvider(Settings()), "1m", engine=temp_engine)
    assert ob is not None
    assert ob.method == "provider"
    assert ob.synthetic is False
    assert len(ob.bars) > 0


# --------------------------------------------------------------------------- #
# Synthetic fallback — curve passes through recorded fills
# --------------------------------------------------------------------------- #
def test_synthetic_fallback_passes_through_fills(temp_engine):
    fills = [
        _ofill(1, "SPY", Side.buy, 5, 3.00, 5, "CALL", 540.0),
        _ofill(2, "SPY", Side.sell, 5, 4.50, 65, "CALL", 540.0),
    ]
    tid = _import(fills, temp_engine)

    ob = get_option_bars_for_trade(tid, _NoOptionBarsProvider(), "1m", engine=temp_engine)
    assert ob is not None
    assert ob.method == "bs_interp"
    assert ob.synthetic is True
    assert ob.bars

    by_ts = {c.ts.replace(tzinfo=UTC): c for c in ob.bars}
    for f in fills:
        bar = by_ts.get(f.ts)
        assert bar is not None, f"no premium bar at fill ts {f.ts}"
        # The curve is anchored to the actual fill premium at each fill's bar.
        assert bar.close == pytest.approx(f.price, abs=0.01)


def test_bs_interp_unit_passes_through_anchor():
    # Underlying flat-ish series around 100; ATM call; two anchors.
    underlying = [
        Candle(symbol="SPY", ts=_ts(m), open=100.0, high=100.5, low=99.5,
               close=100.0 + 0.1 * m, volume=1000.0)
        for m in range(0, 30)
    ]
    contract = OptionContract(underlying="SPY", option_type="CALL", strike=100, expiry=EXPIRY)
    anchors = [(_ts(5), 2.0), (_ts(20), 3.5)]
    bars = bs_interp_premium_bars(underlying, contract, anchors, "1m")
    by_ts = {c.ts: c for c in bars}
    assert by_ts[_ts(5)].close == pytest.approx(2.0, abs=1e-6)
    assert by_ts[_ts(20)].close == pytest.approx(3.5, abs=1e-6)


# --------------------------------------------------------------------------- #
# Regression: index-option scale + sub-minute-hold excursion (issue #37)
# --------------------------------------------------------------------------- #
# An index option (SPXW, strike ~7575) whose mock underlying used to be an
# ~$260 equity-scale random walk produced a deep-ITM ~7300 premium curve
# (~10,000x the recorded sub-$1 fills) and a null MFE/MAE. This locks in that
# the premium series stays on the fills' scale and the excursion is defined.
_SPXW_STRIKE = 7575.0
# Fills mirror trade 138: a long PUT bought at 0.80, sold at 0.55, held ~21s
# entirely inside a single 1-minute bar (the sub-minute case that produced a
# null excursion because no bar OPEN stamp fell strictly inside the window).
_SPXW_ENTRY = 0.80
_SPXW_EXIT = 0.55


def _spxw_fill(i, side, price, ts) -> Fill:
    return Fill(
        provider_fill_id=f"spxw-{i:04d}",
        account="TEST",
        symbol="SPXW",
        side=side,
        qty=1.0,
        price=price,
        ts=ts,
        fees=0.0,
        multiplier=100.0,
        option_type="PUT",
        strike=_SPXW_STRIKE,
        expiry="2026-06-10",  # same-day (0DTE) expiry, like the real trade
        simulated=False,
    )


def test_index_option_premium_on_fill_scale_and_excursion(temp_engine):
    # Sub-minute hold near the close: entry 15:55:19 ET-ish, exit +21s, both
    # inside the same 1-minute bar.
    entry_ts = datetime(2026, 6, 10, 19, 55, 19, tzinfo=UTC)
    exit_ts = datetime(2026, 6, 10, 19, 55, 40, tzinfo=UTC)
    tid = _import(
        [
            _spxw_fill(1, Side.buy, _SPXW_ENTRY, entry_ts),
            _spxw_fill(2, Side.sell, _SPXW_EXIT, exit_ts),
        ],
        temp_engine,
    )

    ob = get_option_bars_for_trade(tid, MockProvider(Settings()), "1m", engine=temp_engine)
    assert ob is not None and ob.bars

    # (1) Scale: the premium series must live on the fills' order of magnitude,
    # NOT ~1000x off (the deep-ITM intrinsic bug was ~7300 vs ~0.68 fills).
    closes = [b.close for b in ob.bars]
    fill_median = 0.5 * (_SPXW_ENTRY + _SPXW_EXIT)  # 0.675
    series_median = sorted(closes)[len(closes) // 2]
    assert series_median < 50 * fill_median, (
        f"premium median {series_median} is off the fills' scale "
        f"(fill median {fill_median})"
    )
    # The gross-mismatch guard should have routed to the fill-anchored series,
    # which is honestly flagged estimated.
    assert ob.synthetic is True
    assert ob.method == "bs_interp"

    # (2) Excursion must be non-null despite the 21s hold falling between bar
    # open stamps, and its magnitudes must be on the premium scale.
    trade = get_trade(tid, engine=temp_engine)
    res = compute_excursion(
        ob.bars,
        entry_price=trade.avg_entry,
        is_long=trade.direction == Direction.long,
        open_ts=trade.open_ts,
        close_ts=trade.close_ts,
    )
    assert res is not None, "excursion must be defined for an in-session hold"
    mfe, mae, _, _ = res
    assert mfe >= 0 and mae >= 0
    assert mfe < 50 * fill_median  # on the premium scale, not intrinsic-scale


def test_compute_excursion_sub_bar_window_uses_containing_bar():
    # 1-minute bars at :00 and :01; hold sits at :00:20 -> :00:40, entirely
    # inside the first bar's interval. No bar OPEN stamp is inside the window,
    # but the containing bar must still define the excursion.
    b0 = Candle(symbol="X", ts=_ts(0), open=1.0, high=1.6, low=0.9, close=1.2, volume=1.0)
    b1 = Candle(symbol="X", ts=_ts(1), open=1.2, high=1.3, low=1.1, close=1.25, volume=1.0)
    open_ts = _ts(0) + timedelta(seconds=20)
    close_ts = _ts(0) + timedelta(seconds=40)
    res = compute_excursion([b0, b1], entry_price=1.0, is_long=True,
                            open_ts=open_ts, close_ts=close_ts)
    assert res is not None
    mfe, mae, _, _ = res
    # Only b0 overlaps the window: high 1.6 -> mfe 0.6, low 0.9 -> mae 0.1.
    assert mfe == pytest.approx(0.6)
    assert mae == pytest.approx(0.1)


# --------------------------------------------------------------------------- #
# Regression: underlying un-fetchable in live mode -> degrade, never 502
# (an index-option root like SPXW is rejected by Webull as US_STOCK -> 417).
# --------------------------------------------------------------------------- #
class _UnfetchableUnderlyingProvider:
    """No option-bar source and get_candles raises WebullError for the underlying.

    Mimics live Webull for an index-option root (SPXW): the BS fallback can't
    fetch the underlying, so there is no premium curve to build.
    """

    def get_option_bars(self, contract, day, timeframe="1m"):
        return None

    def get_candles(self, symbol, day, timeframe="1m"):
        from app.providers.webull.errors import WebullError

        raise WebullError("INVALID_SYMBOL")


def test_option_bars_degrades_when_underlying_unfetchable(temp_engine):
    tid = _import(
        [
            _spxw_fill(1, Side.buy, _SPXW_ENTRY,
                       datetime(2026, 6, 10, 19, 55, 19, tzinfo=UTC)),
            _spxw_fill(2, Side.sell, _SPXW_EXIT,
                       datetime(2026, 6, 10, 19, 55, 40, tzinfo=UTC)),
        ],
        temp_engine,
    )
    # It IS an option trade (has a contract), but the underlying can't be
    # fetched -> no bars rather than a WebullError escaping to a 502.
    assert get_trade_option_contract(tid, engine=temp_engine) is not None
    ob = get_option_bars_for_trade(
        tid, _UnfetchableUnderlyingProvider(), "1m", engine=temp_engine
    )
    assert ob is None


def test_review_http_degrades_unfetchable_option_underlying(isolated_db):
    from app.api.deps import provider_dep

    client = _client(isolated_db)
    client.app.dependency_overrides[provider_dep] = _UnfetchableUnderlyingProvider
    tid = _import_default_engine(
        [
            _spxw_fill(1, Side.buy, _SPXW_ENTRY,
                       datetime(2026, 6, 10, 19, 55, 19, tzinfo=UTC)),
            _spxw_fill(2, Side.sell, _SPXW_EXIT,
                       datetime(2026, 6, 10, 19, 55, 40, tzinfo=UTC)),
        ]
    )
    r = client.get(f"/api/trades/{tid}/review")
    assert r.status_code == 200, r.text  # was 502 before the fix
    body = r.json()
    assert body["option"] is None
    assert body["premium_bars"] is None
    assert body["excursion"] is None


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def test_option_bars_cached_after_first_call(temp_engine):
    tid = _import(
        [
            _ofill(1, "SPY", Side.buy, 5, 3.00, 5, "CALL", 540.0),
            _ofill(2, "SPY", Side.sell, 5, 4.50, 65, "CALL", 540.0),
        ],
        temp_engine,
    )
    first = get_option_bars_for_trade(tid, MockProvider(Settings()), "1m", engine=temp_engine)

    # A provider that would explode if consulted — proves the 2nd call is cached.
    class _Boom:
        def get_option_bars(self, *a, **k):
            raise AssertionError("should have used the cache")

        def get_candles(self, *a, **k):
            raise AssertionError("should have used the cache")

    second = get_option_bars_for_trade(tid, _Boom(), "1m", engine=temp_engine)
    assert [c.close for c in second.bars] == [c.close for c in first.bars]
    assert second.method == first.method

    # Rows actually landed in option_bars_cache.
    from sqlalchemy import func, select

    from app.db.schema import option_bars_cache as cache_t

    with temp_engine.connect() as conn:
        n = conn.execute(select(func.count()).select_from(cache_t)).scalar()
    assert n == len(first.bars)


def test_share_trade_has_no_option_contract(temp_engine):
    tid = _import(
        [
            _stock := Fill(provider_fill_id="s1", account="T", symbol="RIVN",
                           side=Side.buy, qty=100, price=13.4, ts=_ts(0)),
            Fill(provider_fill_id="s2", account="T", symbol="RIVN",
                 side=Side.sell, qty=100, price=13.7, ts=_ts(30)),
        ],
        temp_engine,
    )
    assert get_trade_option_contract(tid, engine=temp_engine) is None
    assert get_option_bars_for_trade(tid, MockProvider(Settings()), "1m",
                                     engine=temp_engine) is None


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
def _client(isolated_db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes.review import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _import_default_engine(fills):
    """Import into the isolated_db's default (settings-derived) engine."""
    from app.db.connection import get_engine

    import_fills(_FillProvider(fills), engine=get_engine())
    trades = list_trades(engine=get_engine())
    return trades[0].id


def test_option_bars_http_provider_path(isolated_db):
    client = _client(isolated_db)
    tid = _import_default_engine(
        [
            _ofill(1, "SPY", Side.buy, 5, 3.00, 5, "CALL", 540.0),
            _ofill(2, "SPY", Side.sell, 5, 4.50, 65, "CALL", 540.0),
        ]
    )
    r = client.get(f"/api/trades/{tid}/option-bars?timeframe=1m")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "provider"
    assert body["synthetic"] is False
    assert len(body["bars"]) > 0
    assert set(body["bars"][0]) >= {"symbol", "ts", "open", "high", "low", "close", "volume"}


def test_option_bars_http_404_for_share_trade(isolated_db):
    client = _client(isolated_db)
    tid = _import_default_engine(
        [
            Fill(provider_fill_id="s1", account="T", symbol="RIVN",
                 side=Side.buy, qty=100, price=13.4, ts=_ts(0)),
            Fill(provider_fill_id="s2", account="T", symbol="RIVN",
                 side=Side.sell, qty=100, price=13.7, ts=_ts(30)),
        ]
    )
    r = client.get(f"/api/trades/{tid}/option-bars")
    assert r.status_code == 404
    assert r.json() == {"detail": "trade has no option contract"}


def test_option_bars_http_404_unknown_trade(isolated_db):
    client = _client(isolated_db)
    r = client.get("/api/trades/999999/option-bars")
    assert r.status_code == 404
    assert r.json() == {"detail": "trade not found"}


def test_review_http_populates_option_and_excursion(isolated_db):
    client = _client(isolated_db)
    tid = _import_default_engine(
        [
            _ofill(1, "SPY", Side.buy, 5, 3.00, 5, "CALL", 540.0),
            _ofill(2, "SPY", Side.sell, 5, 4.50, 65, "CALL", 540.0),
        ]
    )
    body = client.get(f"/api/trades/{tid}/review").json()

    assert body["option"] is not None
    opt = body["option"]
    assert opt["contract_key"] == "SPY_260711_C_540"
    assert opt["descriptor"] == "SPY 540C 07/11"
    assert opt["option_type"] == "CALL"
    assert opt["method"] == "provider"
    assert opt["synthetic"] is False

    assert body["premium_bars"] is not None and len(body["premium_bars"]) > 0
    exc = body["excursion"]
    assert exc is not None
    assert exc["basis"] == "premium"
    assert exc["mfe"] >= 0 and exc["mae"] >= 0
    assert exc["estimated"] is False


def test_review_http_share_trade_underlying_excursion(isolated_db):
    client = _client(isolated_db)
    tid = _import_default_engine(
        [
            Fill(provider_fill_id="s1", account="T", symbol="RIVN",
                 side=Side.buy, qty=100, price=13.4, ts=_ts(0)),
            Fill(provider_fill_id="s2", account="T", symbol="RIVN",
                 side=Side.sell, qty=100, price=13.7, ts=_ts(30)),
        ]
    )
    body = client.get(f"/api/trades/{tid}/review").json()
    assert body["option"] is None
    assert body["premium_bars"] is None
    exc = body["excursion"]
    assert exc is not None
    assert exc["basis"] == "underlying"
    assert exc["estimated"] is False
