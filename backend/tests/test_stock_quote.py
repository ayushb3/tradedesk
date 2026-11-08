"""Underlying bid/ask for the chart legend: Alpaca historical quotes (synthetic HTTP only,
no network, no keys), the mock quote, and the /replay/quote route."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.providers.alpaca import AlpacaProvider
from app.providers.mock import MockProvider
from tests.test_alpaca_provider import Fake, _keys  # noqa: F401  (autouse fake keys)
from tests.test_desk_api import client, rules_file  # noqa: F401

UTC = timezone.utc
AT = datetime(2026, 6, 19, 14, 30, 15, tzinfo=UTC)


def _alpaca(fake, temp_engine, feed="sip"):
    from app.db.connection import init_db

    init_db(temp_engine)
    return AlpacaProvider(Settings(market_data="alpaca", alpaca_feed=feed), client=fake.client(), engine=temp_engine)


def _quote(t="2026-06-19T14:30:14.5Z", bp=100.01, ap=100.03):
    return {"t": t, "bp": bp, "bs": 3, "ap": ap, "as": 5, "x": "V", "c": ["R"]}


def test_alpaca_quote_asks_for_the_newest_quote_at_or_before_at(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"quotes": [_quote()], "symbol": "SPY"}))
    q = _alpaca(fake, temp_engine).get_stock_quote("spy", AT)
    assert q is not None and q["bid"] == 100.01 and q["ask"] == 100.03
    assert q["ts"] <= AT
    req = fake.calls[0]
    assert req.url.path == "/v2/stocks/SPY/quotes"
    assert req.url.params["sort"] == "desc" and req.url.params["limit"] == "1"
    assert req.url.params["end"] == "2026-06-19T14:30:15Z"


def test_alpaca_quote_is_memoized_for_a_closed_session(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"quotes": [_quote()]}))
    p = _alpaca(fake, temp_engine)
    p.get_stock_quote("SPY", AT)
    p.get_stock_quote("SPY", AT)
    assert len(fake.calls) == 1


def test_alpaca_quote_spx_uses_the_spy_proxy(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"quotes": [_quote()]}))
    q = _alpaca(fake, temp_engine).get_stock_quote("SPX", AT)
    assert fake.calls[0].url.path == "/v2/stocks/SPY/quotes"
    assert q["source_label"] == "SPY (proxy)"


def test_alpaca_quote_sip_403_falls_back_to_iex(temp_engine):
    def handler(req):
        if req.url.params["feed"] == "sip":
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(200, json={"quotes": [_quote()]})

    fake = Fake(handler)
    q = _alpaca(fake, temp_engine).get_stock_quote("SPY", AT)
    assert q is not None and [c.url.params["feed"] for c in fake.calls] == ["sip", "iex"]


def test_alpaca_quote_none_when_nothing_printed_or_refused(temp_engine):
    empty = Fake(lambda r: httpx.Response(200, json={"quotes": []}))
    assert _alpaca(empty, temp_engine).get_stock_quote("SPY", AT) is None
    refused = Fake(lambda r: httpx.Response(422, json={"message": "bad"}))
    assert _alpaca(refused, temp_engine, feed="iex").get_stock_quote("SPY", AT) is None


def test_alpaca_quote_never_returns_a_quote_after_at(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"quotes": [_quote(t="2026-06-19T14:30:16Z")]}))
    assert _alpaca(fake, temp_engine).get_stock_quote("SPY", AT) is None


def test_alpaca_quote_skips_the_delayed_window(temp_engine):
    fake = Fake(lambda r: httpx.Response(200, json={"quotes": [_quote()]}))
    assert _alpaca(fake, temp_engine).get_stock_quote("SPY", datetime.now(UTC)) is None
    assert fake.calls == []


def test_mock_quote_brackets_the_price_and_is_deterministic():
    p = MockProvider(Settings())
    a, b = p.get_stock_quote("ZORP", AT), p.get_stock_quote("ZORP", AT)
    assert a == b and a["bid"] < a["ask"]
    assert a["ts"] == AT


@pytest.mark.usefixtures("rules_file")
def test_quote_route_mock(client):  # noqa: F811
    r = client.get("/api/replay/quote", params={"symbol": "zorp", "at": AT.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["symbol"] == "ZORP"
    assert body["bid"] < body["ask"]


def test_quote_route_without_a_quote_source(client, monkeypatch):  # noqa: F811
    from app.api import deps
    from app.main import create_app
    from fastapi.testclient import TestClient

    class NoQuotes:
        pass

    app = create_app()
    app.dependency_overrides[deps.provider_dep] = lambda: NoQuotes()
    body = TestClient(app).get("/api/replay/quote", params={"symbol": "SPY", "at": AT.isoformat()}).json()
    assert body["available"] is False and body["reason"]
