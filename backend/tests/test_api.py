"""TestClient smoke tests for the M2 API surface (isolated temp DB)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import TEST_DAY


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def test_health_ok_mock(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"


def test_candles_returns_full_session(client):
    r = client.get("/api/candles", params={"symbol": "AAPL", "date": str(TEST_DAY)})
    assert r.status_code == 200
    candles = r.json()
    assert len(candles) > 800  # ~960 extended-hours bars
    assert candles[0]["symbol"] == "AAPL"


def test_replay_session_arrays_aligned(client):
    r = client.get("/api/replay/session", params={"symbol": "AAPL", "date": str(TEST_DAY)})
    assert r.status_code == 200
    body = r.json()
    n = len(body["candles"])
    assert n > 800
    for name, arr in body["indicators"].items():
        assert len(arr) == n, f"indicator '{name}' length {len(arr)} != {n}"
    for name, arr in body["levels"].items():
        assert len(arr) == n, f"level '{name}' length {len(arr)} != {n}"
    # Expected series present.
    assert set(body["indicators"]) == {"ema9", "ema21", "ema50", "ema200", "vwap", "rsi14"}
    assert set(body["levels"]) == {"pdh", "pdl", "pmh", "pml", "day_high", "day_low", "or_high", "or_low"}
    # Default timeframe is echoed in meta.
    assert body["meta"]["timeframe"] == "1m"


def test_replay_session_timeframe_reduces_bar_count(client):
    """Coarser timeframes aggregate 1m bars → fewer candles, arrays stay aligned."""
    one = client.get("/api/replay/session", params={"symbol": "AAPL", "date": str(TEST_DAY)})
    five = client.get(
        "/api/replay/session",
        params={"symbol": "AAPL", "date": str(TEST_DAY), "timeframe": "5m"},
    )
    assert one.status_code == 200 and five.status_code == 200
    n1 = len(one.json()["candles"])
    n5 = len(five.json()["candles"])
    # ~960 1m bars → ~192 5m bars. Allow slack, just require a real reduction.
    assert n5 < n1 / 3
    body = five.json()
    assert body["meta"]["timeframe"] == "5m"
    for arr in body["indicators"].values():
        assert len(arr) == n5
    for arr in body["levels"].values():
        assert len(arr) == n5


def test_replay_session_custom_indicator_params(client):
    """EMA periods + RSI period drive the (dynamic) indicator keys."""
    r = client.get(
        "/api/replay/session",
        params={
            "symbol": "AAPL",
            "date": str(TEST_DAY),
            "ema_periods": "8,20",
            "rsi_period": 7,
        },
    )
    assert r.status_code == 200, r.text
    keys = set(r.json()["indicators"])
    assert keys == {"ema8", "ema20", "vwap", "rsi7"}


def test_replay_session_or_minutes_override(client):
    """or_minutes query param overrides the settings default in meta + OR levels."""
    r = client.get(
        "/api/replay/session",
        params={"symbol": "AAPL", "date": str(TEST_DAY), "or_minutes": 15},
    )
    assert r.status_code == 200
    assert r.json()["meta"]["or_minutes"] == 15


def test_replay_session_rejects_bad_timeframe(client):
    r = client.get(
        "/api/replay/session",
        params={"symbol": "AAPL", "date": str(TEST_DAY), "timeframe": "3m"},
    )
    assert r.status_code == 400


def test_sim_trades_post_then_get(client):
    payload = {
        "symbol": "AAPL",
        "direction": "long",
        "session_date": str(TEST_DAY),
        "entries": [{"ts": "2026-06-10T13:30:00Z", "qty": 100, "price": 10.00}],
        "exits": [{"ts": "2026-06-10T14:00:00Z", "qty": 100, "price": 11.50}],
        "stop_price": 9.00,
    }
    post = client.post("/api/sim/trades", json=payload)
    assert post.status_code == 200, post.text
    created = post.json()
    assert created["gross_pnl"] == 150.0
    assert created["r_multiple"] == 1.5

    get = client.get("/api/sim/trades")
    assert get.status_code == 200
    trades = get.json()
    assert any(t["id"] == created["id"] for t in trades)
    assert all(t["simulated"] for t in trades)


# --------------------------------------------------------------------------- #
# Phase C — WebullTokenPending → HTTP 428 mapping
# --------------------------------------------------------------------------- #
def test_webull_token_pending_maps_to_428(isolated_db, monkeypatch):
    """When provider raises WebullTokenPending, the API returns 428 with the
    actionable message body (must contain 'webull_auth.py')."""
    from app.providers.webull.errors import WebullTokenPending

    def _raise_pending(*_args, **_kwargs):
        raise WebullTokenPending(
            "Run `python backend/scripts/webull_auth.py` and verify via the "
            "Webull mobile app. Token dir: /tmp/fake-dir."
        )

    # Patch get_provider so any provider call raises WebullTokenPending.
    monkeypatch.setattr(
        "app.api.deps.get_provider",
        _raise_pending,
    )

    from app.main import create_app
    test_client = TestClient(create_app(), raise_server_exceptions=False)

    r = test_client.get("/api/candles", params={"symbol": "AAPL", "date": str(TEST_DAY)})
    assert r.status_code == 428, f"expected 428 Precondition Required, got {r.status_code}"
    body = r.json()
    assert "webull_auth.py" in body.get("detail", ""), (
        f"actionable message not in response body: {body}"
    )
