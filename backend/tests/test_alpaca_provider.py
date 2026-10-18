"""Alpaca market-data provider: synthetic HTTP only, no network, no keys."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.models.market import OptionContract
from app.providers.alpaca import AlpacaConfigError, AlpacaProvider, occ_symbol
from app.providers.alpaca.client import AlpacaDataClient
from app.providers.base import get_provider

PAST = date(2026, 6, 19)
OPT = OptionContract(underlying="SPY", option_type="CALL", strike=600, expiry="2026-06-19")


def _bar(t, o=1.0):
    return {"t": t, "o": o, "h": o + 1, "l": o - 0.5, "c": o + 0.5, "v": 100, "n": 3, "vw": o}


class Fake:
    def __init__(self, handler):
        self.calls: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request):
        self.calls.append(request)
        return self.handler(request)

    def client(self, sleeps=None):
        return AlpacaDataClient(
            transport=httpx.MockTransport(self),
            sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        )


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")


def _provider(fake, temp_engine, sleeps=None, feed="iex", **kw):
    from app.db.connection import init_db

    init_db(temp_engine)
    s = Settings(market_data="alpaca", alpaca_feed=feed)
    return AlpacaProvider(s, client=fake.client(sleeps), engine=temp_engine, **kw)


def test_occ_symbol_builder():
    assert occ_symbol(OPT) == "SPY260619C00600000"
    spxw = OptionContract(underlying="SPXW", option_type="CALL", strike=6000, expiry="2026-06-19")
    assert occ_symbol(spxw) == "SPXW260619C06000000"
    put = OptionContract(underlying="SPY", option_type="PUT", strike=600.5, expiry="2026-06-19")
    assert occ_symbol(put) == "SPY260619P00600500"


def test_stock_bars_paginate_and_map(temp_engine):
    def handler(req):
        if "page_token" not in req.url.params:
            return httpx.Response(200, json={"bars": [_bar("2026-06-19T13:30:00Z")], "next_page_token": "p2"})
        assert req.url.params["page_token"] == "p2"
        return httpx.Response(200, json={"bars": [_bar("2026-06-19T13:31:00Z")], "next_page_token": None})

    fake = Fake(handler)
    bars = _provider(fake, temp_engine).get_candles("spy", PAST)
    assert len(fake.calls) == 2
    assert [b.ts.minute for b in bars] == [30, 31]
    assert bars[0].symbol == "SPY" and bars[0].source_label is None
    req = fake.calls[0]
    assert req.url.host == "data.alpaca.markets" and req.url.path == "/v2/stocks/SPY/bars"
    assert req.url.params["feed"] == "iex" and req.url.params["adjustment"] == "raw"
    assert req.headers["APCA-API-KEY-ID"] == "test-id"


def test_spx_served_as_spy_proxy(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"bars": [_bar("2026-06-19T13:30:00Z")]}))
    bars = _provider(fake, temp_engine).get_candles("SPX", PAST)
    assert fake.calls[0].url.path == "/v2/stocks/SPY/bars"
    assert bars[0].symbol == "SPX" and bars[0].source_label == "SPY (proxy)"


def test_sip_403_falls_back_to_iex(temp_engine):
    def handler(req):
        if req.url.params["feed"] == "sip":
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(200, json={"bars": [_bar("2026-06-19T13:30:00Z")]})

    fake = Fake(handler)
    p = _provider(fake, temp_engine, feed="sip")
    assert len(p.get_candles("SPY", PAST)) == 1
    assert [c.url.params["feed"] for c in fake.calls] == ["sip", "iex"]
    assert p.last_feed_used == "iex"


def test_429_backoff_then_success(temp_engine):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] <= 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"bars": [_bar("2026-06-19T13:30:00Z")]})

    sleeps: list[float] = []
    bars = _provider(Fake(handler), temp_engine, sleeps=sleeps).get_candles("SPY", PAST)
    assert len(bars) == 1 and len(sleeps) == 2


def test_cache_hit_avoids_http(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"bars": [_bar("2026-06-19T13:30:00Z")]}))
    p = _provider(fake, temp_engine)
    first = p.get_candles("SPY", PAST)
    assert len(fake.calls) == 1
    again = p.get_candles("SPY", PAST)
    assert len(fake.calls) == 1
    assert [c.close for c in again] == [c.close for c in first]


def test_open_session_not_cached(temp_engine):
    from datetime import datetime, timezone

    from app.providers.alpaca import provider as mod

    assert mod._is_closed(PAST) is True
    now = datetime(2026, 6, 19, 18, 0, tzinfo=timezone.utc)  # 14:00 ET, session open
    assert mod._is_closed(PAST, now) is False


def test_option_bars_map_and_cache(temp_engine):
    def handler(req):
        assert req.url.path == "/v1beta1/options/bars"
        assert req.url.params["symbols"] == "SPY260619C00600000"
        return httpx.Response(200, json={"bars": {"SPY260619C00600000": [_bar("2026-06-19T13:30:00Z", 2.0)]}})

    fake = Fake(handler)
    p = _provider(fake, temp_engine)
    bars = p.get_option_bars(OPT, PAST)
    assert bars and bars[0].open == 2.0 and bars[0].symbol == OPT.contract_key
    assert p.get_option_bars(OPT, PAST)  # cache
    assert len(fake.calls) == 1


def test_option_bars_unavailable_returns_none(temp_engine):
    for status in (403, 404):
        fake = Fake(lambda r, s=status: httpx.Response(s))
        assert _provider(fake, temp_engine).get_option_bars(OPT, PAST) is None
    fake = Fake(lambda r: httpx.Response(200, json={"bars": {}}))
    assert _provider(fake, temp_engine).get_option_bars(OPT, PAST) is None


def test_missing_keys_error_on_first_use_not_import(monkeypatch, temp_engine):
    monkeypatch.delenv("APCA_API_KEY_ID")
    monkeypatch.delenv("APCA_API_SECRET_KEY")
    from app.db.connection import init_db

    init_db(temp_engine)
    p = AlpacaProvider(Settings(market_data="alpaca"), engine=temp_engine)  # no crash
    with pytest.raises(AlpacaConfigError) as ei:
        p.get_candles("SPY", PAST)
    assert "APCA_API_KEY_ID" in str(ei.value)


def test_http_error_message_has_no_keys(temp_engine):
    from app.providers.alpaca.errors import AlpacaHTTPError

    fake = Fake(lambda r: httpx.Response(500))
    with pytest.raises(AlpacaHTTPError) as ei:
        _provider(fake, temp_engine).get_candles("SPY", PAST)
    assert "test-secret" not in str(ei.value) and "test-id" not in str(ei.value)


def test_selection_default_is_mock_and_alpaca_opt_in(monkeypatch):
    from app.providers.mock import MockProvider

    assert isinstance(get_provider(Settings()), MockProvider)
    assert Settings().market_data == "mock"
    assert isinstance(get_provider(Settings(market_data="alpaca")), AlpacaProvider)
    monkeypatch.setenv("TRADEDESK_MARKET_DATA", "alpaca")
    assert Settings().market_data == "alpaca"


def test_fills_delegate_to_mode_provider():
    p = AlpacaProvider(Settings(market_data="alpaca"))
    assert p.get_fills()  # mock fills


def test_provider_package_is_read_only():
    pkg = Path(__file__).resolve().parents[1] / "app" / "providers" / "alpaca"
    forbidden = [
        "api.alpaca.markets", "paper-api", "/v2/orders", "/v2/positions", "/v2/account",
        "alpaca_trade_api", "alpaca.trading", "alpaca-py", "TradingClient",
    ]
    for f in pkg.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for bad in forbidden:
            assert bad not in text, f"{bad!r} found in {f.name}"


def test_default_feed_is_sip_and_recent_window_uses_iex(temp_engine):
    from datetime import datetime, timezone

    assert Settings().alpaca_feed == "sip"
    fake = Fake(lambda r: httpx.Response(200, json={"bars": []}))
    p = _provider(fake, temp_engine, feed="sip")
    p.get_candles("SPY", PAST)
    assert fake.calls[-1].url.params["feed"] == "sip"
    now = datetime.now(timezone.utc)
    p.get_candles_range("SPY", now.replace(year=now.year) - __import__("datetime").timedelta(hours=1), now)
    assert fake.calls[-1].url.params["feed"] == "iex"


def test_option_history_before_2024_is_empty_without_request(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"bars": {}}))
    assert _provider(fake, temp_engine).get_option_bars(OPT, date(2023, 6, 15)) == []
    assert fake.calls == []
