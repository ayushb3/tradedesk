"""Replay on the selected provider + 'replay my trade' (issue #50). Synthetic data, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.ingest.imports import import_fills
from app.ingest.query import list_trades
from app.models.market import Candle
from app.models.trades import Fill, Side
from app.providers.mock import MockProvider

from .conftest import TEST_DAY

UTC = timezone.utc
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # 06:30 PT


class _FillProvider:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self, since=None):
        return list(self._fills)


def _fill(i, side, price, minute) -> Fill:
    return Fill(
        provider_fill_id=f"t-{i}", account="TEST", symbol="SPY", side=side,
        qty=10, price=price, ts=BASE + timedelta(minutes=minute),
    )


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture()
def trade_id(isolated_db):
    from app.db.connection import get_engine

    # entry at +60 (07:30 PT), exit at +90 (08:00 PT)
    fills = [_fill(1, Side.buy, 100.0, 60), _fill(2, Side.sell, 101.0, 90)]
    import_fills(_FillProvider(fills), engine=get_engine())
    trades = list_trades(engine=get_engine())
    assert len(trades) == 1
    return trades[0].id


def _iso(minute: int) -> str:
    return (BASE + timedelta(minutes=minute)).isoformat()


def test_default_playhead_is_start_and_hides_all_fills(client, trade_id):
    r = client.get(f"/api/replay/trade/{trade_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["lead_minutes"] == 15
    assert body["fills"] == []
    # start = entry (BASE+60) - 15
    assert datetime.fromisoformat(body["start_ts"]) == BASE + timedelta(minutes=45)
    first = body["session"]["candles"][body["start_index"]]
    assert datetime.fromisoformat(first["ts"]) >= BASE + timedelta(minutes=45)
    assert len(body["session"]["candles"]) > 800


def test_fills_gated_by_playhead_server_side(client, trade_id):
    def fills_at(minute: int):
        r = client.get(f"/api/replay/trade/{trade_id}", params={"playhead": _iso(minute)})
        assert r.status_code == 200
        return r.json()["fills"]

    assert fills_at(59) == []
    entry_only = fills_at(60)  # >= is inclusive
    assert [f["role"] for f in entry_only] == ["entry"]
    assert fills_at(89) == entry_only
    both = fills_at(90)
    assert [f["role"] for f in both] == ["entry", "exit"]
    # the hidden exit must not leak anywhere in the early payload
    early = client.get(f"/api/replay/trade/{trade_id}", params={"playhead": _iso(59)}).text
    assert "101.0" not in early.split('"session"')[0]


def test_fills_endpoint_gates_and_labels_pt(client, trade_id):
    r = client.get(f"/api/replay/trade/{trade_id}/fills", params={"playhead": _iso(60)})
    assert r.status_code == 200
    fills = r.json()["fills"]
    assert len(fills) == 1
    assert fills[0]["time_pt"] == "07:30:00 PT"
    assert "pnl" not in r.text.lower()
    r2 = client.get(f"/api/replay/trade/{trade_id}/fills", params={"playhead": _iso(30)})
    assert r2.json()["fills"] == []


def test_naive_playhead_treated_as_utc(client, trade_id):
    r = client.get(f"/api/replay/trade/{trade_id}/fills", params={"playhead": "2026-06-10T14:30:00"})
    assert len(r.json()["fills"]) == 1


def test_lead_minutes_changes_start(client, trade_id):
    r = client.get(f"/api/replay/trade/{trade_id}", params={"lead_minutes": 30})
    assert datetime.fromisoformat(r.json()["start_ts"]) == BASE + timedelta(minutes=30)


def test_unknown_trade_404(client, isolated_db):
    assert client.get("/api/replay/trade/9999").status_code == 404
    assert client.get("/api/replay/trade/9999/fills", params={"playhead": _iso(0)}).status_code == 404


class _ProxyProvider:
    """Stand-in for the Alpaca provider: SPX served as SPY with a proxy label."""

    def __init__(self):
        self.asked: list[str] = []
        self._mock = MockProvider(Settings())

    def get_candles(self, symbol, day, timeframe="1m"):
        self.asked.append(symbol)
        return [
            c.model_copy(update={"source_label": "SPY (proxy)"})
            for c in self._mock.get_candles("SPY", day, timeframe)
        ]


def test_replay_uses_selected_provider_and_surfaces_source_label(client, monkeypatch):
    fake = _ProxyProvider()
    monkeypatch.setattr("app.api.deps.get_provider", lambda *_a, **_k: fake)
    r = client.get("/api/replay/session", params={"symbol": "SPX", "date": str(TEST_DAY)})
    assert r.status_code == 200
    assert fake.asked and fake.asked[0] == "SPX"
    assert r.json()["meta"]["source_label"] == "SPY (proxy)"


def test_replay_trade_uses_selected_provider(client, trade_id, monkeypatch):
    fake = _ProxyProvider()
    monkeypatch.setattr("app.api.deps.get_provider", lambda *_a, **_k: fake)
    r = client.get(f"/api/replay/trade/{trade_id}")
    assert r.status_code == 200
    assert fake.asked
    assert r.json()["session"]["meta"]["source_label"] == "SPY (proxy)"


def test_mock_session_has_no_source_label(client):
    r = client.get("/api/replay/session", params={"symbol": "AAPL", "date": str(TEST_DAY)})
    assert "source_label" not in r.json()["meta"]


def test_get_provider_selection_follows_market_data_setting():
    from app.providers.alpaca import AlpacaProvider
    from app.providers.base import get_provider

    assert isinstance(get_provider(Settings(market_data="alpaca")), AlpacaProvider)
    assert isinstance(get_provider(Settings(market_data="mock")), MockProvider)
