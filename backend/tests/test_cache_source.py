"""Market-data caches are keyed on source: one provider's rows are never served to
another. Synthetic HTTP only (httpx.MockTransport), no network, no keys."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select, text

from app.config import Settings
from app.db.connection import init_db
from app.db.schema import candles_cache, option_bars_cache
from app.ingest.imports import import_fills
from app.ingest.option_bars import get_cached_option_bars_for_trade, get_option_bars_for_trade
from app.ingest.query import get_trade_option_contract, list_trades
from app.models.trades import Fill, Side
from app.providers.alpaca import AlpacaProvider, occ_symbol
from app.providers.alpaca.client import AlpacaDataClient
from app.providers.mock import MockProvider

UTC = timezone.utc
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)
ALPACA_CLOSE = 3.9  # distinctive: the mock/BS series never lands exactly here


class _Fills:
    def __init__(self, fills):
        self._f = fills

    def get_fills(self, since=None):
        return list(self._f)


def _fill(i, side, price, minute):
    return Fill(
        provider_fill_id=f"cs-{i}", account="TEST", symbol="SPY", side=side, qty=5,
        price=price, ts=BASE + timedelta(minutes=minute), fees=0.0, multiplier=100.0,
        option_type="CALL", strike=540.0, expiry="2026-07-11", simulated=False,
    )


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")


@pytest.fixture()
def trade(temp_engine):
    init_db(temp_engine)
    import_fills(_Fills([_fill(1, Side.buy, 3.0, 5), _fill(2, Side.sell, 4.5, 65)]),
                 engine=temp_engine)
    return list_trades(engine=temp_engine)[0].id


class _NoOptionBars(MockProvider):
    """Mock candles but no option-bar source -> synthetic bs_interp fallback."""

    def get_option_bars(self, *a, **k):
        return None


class Http:
    def __init__(self, contract):
        self.calls = 0
        self.occ = occ_symbol(contract)

    def __call__(self, request):
        self.calls += 1
        bars = [
            {"t": (BASE + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "o": ALPACA_CLOSE, "h": ALPACA_CLOSE + 0.1, "l": ALPACA_CLOSE - 0.1,
             "c": ALPACA_CLOSE, "v": 10, "n": 1, "vw": ALPACA_CLOSE}
            for m in range(5, 66)
        ]
        return httpx.Response(200, json={"bars": {self.occ: bars}, "next_page_token": None})


def _alpaca(handler, engine):
    client = AlpacaDataClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    return AlpacaProvider(Settings(market_data="alpaca", alpaca_feed="iex"),
                          client=client, engine=engine)


def _contract(tid, engine):
    return get_trade_option_contract(tid, engine=engine)


def test_mock_rows_not_served_to_alpaca(temp_engine, trade):
    mock = get_option_bars_for_trade(trade, MockProvider(Settings()), "1m", engine=temp_engine)
    assert mock.method == "provider" and mock.bars
    http = Http(_contract(trade, temp_engine))
    ob = get_option_bars_for_trade(trade, _alpaca(http, temp_engine), "1m", engine=temp_engine)
    assert http.calls >= 1
    assert ob.synthetic is False and ob.method == "provider"
    assert {c.close for c in ob.bars} == {ALPACA_CLOSE}


def test_synthetic_bs_rows_not_served_to_alpaca(temp_engine, trade):
    syn = get_option_bars_for_trade(trade, _NoOptionBars(Settings()), "1m", engine=temp_engine)
    assert syn.synthetic is True and syn.method == "bs_interp"
    http = Http(_contract(trade, temp_engine))
    ob = get_option_bars_for_trade(trade, _alpaca(http, temp_engine), "1m", engine=temp_engine)
    assert http.calls >= 1
    assert ob.synthetic is False
    assert {c.close for c in ob.bars} == {ALPACA_CLOSE}


def test_alpaca_rows_not_served_to_mock(temp_engine, trade):
    http = Http(_contract(trade, temp_engine))
    get_option_bars_for_trade(trade, _alpaca(http, temp_engine), "1m", engine=temp_engine)
    ob = get_option_bars_for_trade(trade, MockProvider(Settings()), "1m", engine=temp_engine)
    assert ALPACA_CLOSE not in {c.close for c in ob.bars}
    # a cache-only read for a third source sees nothing
    assert get_cached_option_bars_for_trade(trade, "1m", temp_engine, source="webull") is None


def test_same_source_hit_avoids_http(temp_engine, trade):
    http = Http(_contract(trade, temp_engine))
    first = get_option_bars_for_trade(trade, _alpaca(http, temp_engine), "1m", engine=temp_engine)
    n = http.calls
    second = get_option_bars_for_trade(trade, _alpaca(http, temp_engine), "1m", engine=temp_engine)
    assert http.calls == n
    assert [c.close for c in second.bars] == [c.close for c in first.bars]
    cached = get_cached_option_bars_for_trade(trade, "1m", temp_engine, source="alpaca")
    assert cached is not None and not cached.synthetic


def test_provider_rows_preferred_over_synthetic_same_source(temp_engine, trade):
    http = Http(_contract(trade, temp_engine))
    get_option_bars_for_trade(trade, _alpaca(http, temp_engine), "1m", engine=temp_engine)
    key = _contract(trade, temp_engine).contract_key
    with temp_engine.begin() as conn:
        conn.execute(option_bars_cache.insert().values(
            contract_key=key, timeframe="1m",
            ts_utc=(BASE + timedelta(minutes=1)).replace(tzinfo=None),
            open=1, high=1, low=1, close=1, volume=0, synthetic=True, method="bs_interp",
            source="alpaca"))
    cached = get_cached_option_bars_for_trade(trade, "1m", temp_engine, source="alpaca")
    assert cached.synthetic is False and 1 not in {c.close for c in cached.bars}


def test_legacy_rows_without_source_are_never_served(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with eng.begin() as conn:  # pre-fix shape: no `source` column
        conn.execute(text(
            "CREATE TABLE option_bars_cache (contract_key TEXT, timeframe TEXT, ts_utc DATETIME,"
            " open FLOAT, high FLOAT, low FLOAT, close FLOAT, volume FLOAT,"
            " synthetic BOOLEAN NOT NULL, method TEXT NOT NULL,"
            " PRIMARY KEY (contract_key, timeframe, ts_utc))"))
        conn.execute(text(
            "INSERT INTO option_bars_cache VALUES ('SPY_260711_C_540','1m','2026-06-10 13:35:00',"
            "1,1,1,1,0,0,'provider')"))
    init_db(eng)
    init_db(eng)  # idempotent
    with eng.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(option_bars_cache)"))}
        row = conn.execute(select(option_bars_cache)).fetchone()
    assert "source" in cols and row._mapping["source"] is None
    from app.ingest.option_bars import _read_cache

    for src in ("alpaca", "mock"):
        with eng.connect() as conn:
            assert _read_cache(conn, "SPY_260711_C_540", "1m", date(2026, 6, 10), src) is None


def test_alpaca_candles_cache_ignores_other_providers(temp_engine):
    init_db(temp_engine)
    day = date(2026, 6, 19)
    with temp_engine.begin() as conn:
        conn.execute(candles_cache.insert().values(
            symbol="SPY", timeframe="1m", ts_utc=datetime(2026, 6, 19, 13, 30), open=1, high=1,
            low=1, close=1, volume=1, provider="mock", session_date=day))
        conn.execute(candles_cache.insert().values(
            symbol="SPY", timeframe="1m", ts_utc=datetime(2026, 6, 19, 13, 31), open=1, high=1,
            low=1, close=1, volume=1, provider=None, session_date=day))
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"bars": [
            {"t": "2026-06-19T13:30:00Z", "o": 7, "h": 8, "l": 6, "c": 7.5, "v": 1}],
            "next_page_token": None})

    bars = _alpaca(handler, temp_engine).get_candles("SPY", day)
    assert calls and [b.close for b in bars] == [7.5]
