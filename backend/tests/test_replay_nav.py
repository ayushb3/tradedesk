"""Replay 2: prev/next trade, the day replay, and SMA 200/400. Synthetic data, no network."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.indicators.sma import sma, sma_with_warmup
from app.ingest.imports import import_fills
from app.ingest.query import list_trades
from app.models.trades import Fill, Side

from .conftest import TEST_DAY

UTC = timezone.utc
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)  # 06:30 PT on TEST_DAY
NEXT_BASE = datetime(2026, 6, 11, 13, 30, tzinfo=UTC)


class _FillProvider:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self, since=None):
        return list(self._fills)


def _fill(i, side, price, at, symbol="SPY") -> Fill:
    return Fill(provider_fill_id=f"n-{i}", account="TEST", symbol=symbol, side=side,
                qty=10, price=price, ts=at)


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture()
def ids(isolated_db):
    """Three round trips: two on TEST_DAY (07:00, 08:00 PT), one the next day."""
    from app.db.connection import get_engine

    m = lambda base, n: base + timedelta(minutes=n)  # noqa: E731
    fills = [
        _fill(1, Side.buy, 100.0, m(BASE, 30)), _fill(2, Side.sell, 101.0, m(BASE, 40)),
        _fill(3, Side.buy, 102.0, m(BASE, 90)), _fill(4, Side.sell, 101.5, m(BASE, 95)),
        _fill(5, Side.buy, 99.0, m(NEXT_BASE, 10)), _fill(6, Side.sell, 99.5, m(NEXT_BASE, 20)),
    ]
    import_fills(_FillProvider(fills), engine=get_engine())
    trades = sorted(list_trades(engine=get_engine()), key=lambda t: t.open_ts)
    assert len(trades) == 3
    return [t.id for t in trades]


# ---- SMA -------------------------------------------------------------------
def test_sma_is_none_until_a_full_window_then_the_mean():
    assert sma([1, 2, 3, 4], 3) == [None, None, 2.0, 3.0]
    assert sma([1, 2], 3) == [None, None]


def test_sma_warmup_makes_the_first_bar_valid_and_is_causal():
    warm = [1.0, 2.0]
    vals = [3.0, 4.0, 5.0]
    out = sma_with_warmup(warm, vals, 3)
    assert out == [2.0, 3.0, 4.0]
    # truncating the series never changes earlier values
    assert sma_with_warmup(warm, vals[:2], 3) == out[:2]
    # not enough history: still None, never a partial mean
    assert sma_with_warmup([1.0], vals, 3) == [None, 8 / 3, 4.0]


def test_replay_session_has_sma200_and_sma400_valid_from_the_first_bar(client):
    r = client.get("/api/replay/session", params={"symbol": "SPY", "date": TEST_DAY.isoformat()})
    assert r.status_code == 200
    ind = r.json()["indicators"]
    n = len(r.json()["candles"])
    for k in ("sma200", "sma400"):
        assert len(ind[k]) == n
        assert ind[k][0] is not None  # warmed up from prior sessions
    closes = [c["close"] for c in r.json()["candles"]]
    # deep in the session the value is the plain mean of the last 400 closes
    assert ind["sma400"][-1] == pytest.approx(sum(closes[-400:]) / 400, abs=1e-3)


# ---- neighbors ---------------------------------------------------------------
def test_neighbors_walk_the_day_then_the_next_day(client, ids):
    a, b, c = ids
    got = lambda t: client.get(f"/api/replay/trade/{t}/neighbors").json()  # noqa: E731
    assert (got(a)["prev_id"], got(a)["next_id"]) == (None, b)
    assert (got(b)["prev_id"], got(b)["next_id"]) == (a, c)
    assert (got(c)["prev_id"], got(c)["next_id"]) == (b, None)
    assert client.get("/api/replay/trade/999999/neighbors").status_code == 404


# ---- day replay ----------------------------------------------------------------
def test_day_replay_session_carries_no_trade_times_or_fills(client, ids):
    r = client.get(f"/api/replay/day/{TEST_DAY.isoformat()}")
    assert r.status_code == 200
    j = r.json()
    assert j["symbol"] == "SPY" and j["trade_count"] == 2 and j["symbols"] == ["SPY"]
    assert len(j["session"]["candles"]) > 800
    assert "fills" not in j and "trades" not in j
    assert "pnl" not in r.text.lower()


def test_day_replay_404_without_trades(client, ids):
    assert client.get("/api/replay/day/2026-06-01").status_code == 404
    assert client.get(f"/api/replay/day/{TEST_DAY.isoformat()}", params={"symbol": "QQQ"}).status_code == 404


def test_day_fills_are_gated_by_the_playhead(client, ids):
    a, b, _ = ids

    def at(minute):
        r = client.get(f"/api/replay/day/{TEST_DAY.isoformat()}/fills",
                       params={"playhead": (BASE + timedelta(minutes=minute)).isoformat()})
        assert r.status_code == 200
        return r.json()

    early = at(29)
    assert early["trades"] == [] and early["fills"] == []
    mid = at(30)
    assert [t["trade_id"] for t in mid["trades"]] == [a]
    assert [(f["trade_id"], f["role"]) for f in mid["fills"]] == [(a, "entry")]
    later = at(90)
    assert [t["trade_id"] for t in later["trades"]] == [a, b]
    assert [(f["trade_id"], f["role"]) for f in later["fills"]] == [(a, "entry"), (a, "exit"), (b, "entry")]
    # the second trade's exit price never leaks before its time
    assert "101.5" not in str(later)
    assert date.fromisoformat(later["session_date"]) == TEST_DAY
