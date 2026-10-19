"""Replay my trade, Contract mode: premium bars, stop line, fill gating. Synthetic, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.excursions import stop_price
from app.models.market import Candle
from tests.test_desk_api import client, rules_file  # noqa: F401  (synthetic option-fill fixtures)

UTC = timezone.utc
ENTRY = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)  # 06:45 PT; exit 10 minutes later


def _tid(client, idx=0) -> int:
    return client.get("/api/days/2026-10-13").json()["positions"][idx]["trade_id"]


def _iso(minutes_from_entry: int) -> str:
    return (ENTRY + timedelta(minutes=minutes_from_entry)).isoformat()


def _override(client, provider):
    from app.api.deps import provider_dep

    client.app.dependency_overrides[provider_dep] = lambda: provider


class _Bars:
    """Provider returning flat premium bars around the entry price."""

    def __init__(self, price=1.50):
        self.price = price

    def get_option_bars(self, contract, day, timeframe="1m"):
        start = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)
        return [
            Candle(
                symbol=contract.contract_key, ts=start + timedelta(minutes=i),
                open=self.price, high=self.price + 0.1, low=self.price - 0.1,
                close=self.price, volume=10, timeframe="1m",
            )
            for i in range(120)
        ]

    def get_candles(self, symbol, day, timeframe="1m"):
        raise AssertionError("must not be needed")


class _NoBars:
    def get_option_bars(self, contract, day, timeframe="1m"):
        return None

    def get_candles(self, symbol, day, timeframe="1m"):
        from app.providers.webull.errors import WebullError

        raise WebullError("no candles")


def test_contract_mode_returns_premium_bars_and_matching_stop(client):
    tid = _tid(client)
    pos = client.get("/api/days/2026-10-13").json()["positions"][0]
    r = client.get(f"/api/replay/trade/{tid}/contract")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True and j["reason"] is None
    assert len(j["candles"]) > 60
    # starts 15 min before entry
    assert datetime.fromisoformat(j["candles"][j["start_index"]]["ts"]) >= ENTRY - timedelta(minutes=15)
    assert datetime.fromisoformat(j["candles"][j["start_index"] - 1]["ts"]) < ENTRY - timedelta(minutes=15)
    # same stop and risk as the ledger: avg_in 1.50, qty 2
    assert j["stop_price"] == pytest.approx(stop_price(1.50, 2, pos["risk"]))
    assert j["method"] in {"provider", "bs_interp"}
    assert "pnl" not in r.text.lower()


def test_contract_fills_gated_before_at_after_playhead(client):
    tid = _tid(client)
    _override(client, _Bars())
    try:
        def at(m):
            r = client.get(f"/api/replay/trade/{tid}/contract", params={"playhead": _iso(m)})
            assert r.status_code == 200
            return r.json()

        assert at(-1)["fills"] == []
        entry_only = at(0)["fills"]  # inclusive
        assert [f["role"] for f in entry_only] == ["entry"]
        assert entry_only[0]["time_pt"] == "06:45:00 PT"
        assert [f["role"] for f in at(9)["fills"]] == ["entry"]
        both = at(10)["fills"]
        assert [f["role"] for f in both] == ["entry", "exit"]
        assert both[1]["time_pt"] == "06:55:00 PT"
        # the hidden exit price must not appear anywhere in an early payload
        early = client.get(f"/api/replay/trade/{tid}/contract", params={"playhead": _iso(-1)}).json()
        assert early["fills"] == []
        assert all(f["price"] != 2.0 for f in early["fills"])
    finally:
        client.app.dependency_overrides.clear()


def test_default_playhead_is_start_and_hides_fills(client):
    tid = _tid(client)
    j = client.get(f"/api/replay/trade/{tid}/contract").json()
    assert j["fills"] == []
    assert datetime.fromisoformat(j["start_ts"]) == ENTRY - timedelta(minutes=15)


def test_unavailable_bars_degrade(client):
    tid = _tid(client, idx=1)
    _override(client, _NoBars())
    try:
        r = client.get(f"/api/replay/trade/{tid}/contract", params={"playhead": _iso(60)})
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is False
    assert j["reason"] == "option bars unavailable"
    assert j["candles"] == [] and j["stop_price"] is None and j["fills"] == []


def test_estimated_flag_passed_through(client):
    tid = _tid(client)
    # provider bars on the fills' scale: real prints, not estimated
    _override(client, _Bars(price=1.50))
    try:
        real = client.get(f"/api/replay/trade/{tid}/contract").json()
    finally:
        client.app.dependency_overrides.clear()
    assert real["method"] == "provider"
    assert real["synthetic"] is False and real["estimated"] is False

    # provider has no option bars: fill-anchored BS fallback, flagged estimated
    class _Fallback(_NoBars):
        def get_candles(self, symbol, day, timeframe="1m"):
            from app.providers.mock import MockProvider
            from app.config import Settings

            return MockProvider(Settings()).get_candles("SPY", day, timeframe)

    from app.api.deps import provider_dep

    client.app.dependency_overrides[provider_dep] = lambda: _Fallback()
    try:
        est = client.get(f"/api/replay/trade/{tid}/contract").json()
    finally:
        client.app.dependency_overrides.clear()
    assert est["available"] is True
    assert est["method"] == "bs_interp"
    assert est["synthetic"] is True and est["estimated"] is True


def test_unknown_trade_404(client):
    assert client.get("/api/replay/trade/999999/contract").status_code == 404
