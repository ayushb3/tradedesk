"""GET /api/replay/seconds: generic second bars for any symbol over a short window.
Mock data and fake providers only; neutral example dates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.deps import provider_dep
from app.api.routes import replay as replay_routes
from app.models.market import Candle
from app.providers.seconds import SecondsUnavailable
from tests.test_desk_api import client, rules_file  # noqa: F401

UTC = timezone.utc
START = datetime(2026, 3, 13, 14, 0, tzinfo=UTC)
END = START + timedelta(minutes=10)


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def params(**kw):
    p = {"symbol": "SPY", "start": iso(START), "end": iso(END), "res": "1s"}
    p.update(kw)
    return p


@pytest.fixture(autouse=True)
def _fresh_cache():
    replay_routes.clear_seconds_cache()
    yield
    replay_routes.clear_seconds_cache()


class Fake:
    def __init__(self, bars=None, exc=None):
        self.bars, self.exc, self.calls = bars or [], exc, []

    def get_second_bars(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if self.exc:
            raise self.exc
        return list(self.bars)


def bars_1s(n=120, sym="SPY"):
    return [
        Candle(symbol=sym, ts=START + timedelta(seconds=i), open=100 + i, high=101 + i,
               low=99 + i, close=100.5 + i, volume=10 + i, timeframe="1s")
        for i in range(n)
    ]


def use(client, provider):
    client.app.dependency_overrides[provider_dep] = lambda: provider
    return provider


@pytest.fixture(autouse=True)
def _clear_overrides(client):
    yield
    client.app.dependency_overrides.clear()


@pytest.mark.parametrize("res,secs", [("1s", 1), ("5s", 5), ("15s", 15), ("30s", 30)])
def test_mock_happy_path_and_volume_conserved(client, res, secs):
    base = client.get("/api/replay/seconds", params=params(res="1s")).json()
    assert base["available"] is True and base["candles"]
    r = client.get("/api/replay/seconds", params=params(res=res))
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["res"] == res and body["symbol"] == "SPY"
    assert all(c["timeframe"] == res for c in body["candles"])
    assert all(c["volume"] > 0 for c in body["candles"])
    assert sum(c["volume"] for c in body["candles"]) == pytest.approx(
        sum(c["volume"] for c in base["candles"])
    )
    if secs > 1:
        assert len(body["candles"]) < len(base["candles"])
        assert all(int(datetime.fromisoformat(c["ts"].replace("Z", "+00:00")).timestamp()) % secs == 0
                   for c in body["candles"])


def test_window_cap_and_order(client):
    ok = client.get("/api/replay/seconds", params=params(end=iso(START + timedelta(minutes=30))))
    assert ok.status_code == 200
    over = client.get("/api/replay/seconds", params=params(end=iso(START + timedelta(minutes=30, seconds=1))))
    assert over.status_code == 400
    assert client.get("/api/replay/seconds", params=params(end=iso(START))).status_code == 400
    assert client.get("/api/replay/seconds", params=params(end=iso(START - timedelta(minutes=1)))).status_code == 400


def test_bad_res_and_bad_symbol(client):
    assert client.get("/api/replay/seconds", params=params(res="2s")).status_code == 400
    for bad in ("SPY1", "S PY", "spy;drop", "../x", "TOOLONGSYMBOL1"):
        assert client.get("/api/replay/seconds", params=params(symbol=bad)).status_code == 400
    assert client.get("/api/replay/seconds", params=params(symbol="brk.b")).status_code == 200


@pytest.mark.parametrize("root", ["SPXW", "SPX"])
def test_index_root_serves_spy_with_proxy_label(client, root):
    fake = use(client, Fake(bars_1s()))
    body = client.get("/api/replay/seconds", params=params(symbol=root)).json()
    assert fake.calls[0][0] == "SPY"
    assert body["symbol"] == root and body["source_label"] == "SPY (proxy)"
    assert body["available"] is True


def test_plain_symbol_has_no_label(client):
    use(client, Fake(bars_1s()))
    assert client.get("/api/replay/seconds", params=params()).json()["source_label"] is None


def test_playhead_reveals_only_finished_bars(client):
    use(client, Fake(bars_1s()))
    head = START + timedelta(seconds=44)  # 15s bars: [0-14], [15-29], [30-44] complete
    body = client.get("/api/replay/seconds", params=params(res="15s", playhead=iso(head))).json()
    assert len(body["candles"]) == 3
    assert body["playhead"] is not None
    early = START + timedelta(seconds=43)
    body = client.get("/api/replay/seconds", params=params(res="15s", playhead=iso(early))).json()
    assert len(body["candles"]) == 2
    one = client.get("/api/replay/seconds", params=params(playhead=iso(START + timedelta(seconds=9)))).json()
    assert len(one["candles"]) == 10


def test_rebucketed_volume_matches_fake_total(client):
    fake_bars = bars_1s()
    use(client, Fake(fake_bars))
    body = client.get("/api/replay/seconds", params=params(res="30s")).json()
    assert sum(c["volume"] for c in body["candles"]) == sum(b.volume for b in fake_bars)


@pytest.mark.parametrize("fake", [
    Fake(exc=RuntimeError("boom")),
    Fake(exc=SecondsUnavailable("plan has no trade prints")),
    Fake(bars=[]),
])
def test_failures_and_empty_are_available_false(client, fake):
    use(client, fake)
    r = client.get("/api/replay/seconds", params=params())
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["reason"] and body["candles"] == []


def test_unavailable_reason_is_passed_through(client):
    use(client, Fake(exc=SecondsUnavailable("plan has no trade prints")))
    assert client.get("/api/replay/seconds", params=params()).json()["reason"] == "plan has no trade prints"


def test_past_windows_are_cached_and_failures_are_not(client):
    fake = use(client, Fake(bars_1s()))
    for _ in range(2):
        assert client.get("/api/replay/seconds", params=params()).json()["available"] is True
    assert len(fake.calls) == 1
    # a different resolution reuses the same 1s bars
    client.get("/api/replay/seconds", params=params(res="15s"))
    assert len(fake.calls) == 1

    bad = use(client, Fake(exc=RuntimeError("x")))
    for _ in range(2):
        client.get("/api/replay/seconds", params=params(symbol="QQQ"))
    assert len(bad.calls) == 2
