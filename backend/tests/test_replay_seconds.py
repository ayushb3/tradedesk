"""Replay Seconds: tick aggregation, the end clamp, the playhead gate, the cache, mock mode.
Synthetic prints and contracts only; no network, no keys."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.db.schema import second_bars_cache, second_bars_coverage
from app.models.market import OptionContract
from app.providers.alpaca import provider as alpaca_mod
from app.providers.alpaca.client import AlpacaDataClient
from app.providers.seconds import (
    SecondsUnavailable,
    Tick,
    aggregate_ticks,
    parse_trade_ts,
    rebucket,
    revealed,
    ticks_from_alpaca,
)
from tests.test_desk_api import client, rules_file  # noqa: F401  (synthetic option-fill fixtures)

UTC = timezone.utc
OPT = OptionContract(underlying="SPY", option_type="CALL", strike=600, expiry="2026-06-19")


def T(h, m, s, us=0) -> datetime:
    return datetime(2026, 6, 19, h, m, s, us, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# tick -> second aggregation
# --------------------------------------------------------------------------- #
def test_ticks_aggregate_into_ohlcv_and_empty_seconds_have_no_bar():
    ticks = [
        Tick(T(14, 0, 0, 100), 1.00, 2),
        Tick(T(14, 0, 0, 900), 1.30, 3),
        Tick(T(14, 0, 0, 500), 0.90, 1),   # out of order: sorted by time first
        Tick(T(14, 0, 3, 0), 1.10, 5),     # seconds 1 and 2 are empty
    ]
    bars = aggregate_ticks(ticks, "X", 1)
    assert [b.ts for b in bars] == [T(14, 0, 0), T(14, 0, 3)]
    b0 = bars[0]
    assert (b0.open, b0.high, b0.low, b0.close, b0.volume) == (1.00, 1.30, 0.90, 1.30, 6)
    assert (bars[1].open, bars[1].close, bars[1].volume) == (1.10, 1.10, 5)
    assert all(b.timeframe == "1s" for b in bars)


def test_nanosecond_timestamps_and_bad_rows():
    assert parse_trade_ts("2026-06-19T14:00:00.123456789Z") == T(14, 0, 0, 123456)
    assert parse_trade_ts("2026-06-19T14:00:01Z") == T(14, 0, 1)
    ticks = ticks_from_alpaca([
        {"t": "2026-06-19T14:00:00.5Z", "p": 2.0, "s": 1},
        {"t": "2026-06-19T14:00:00.6Z", "p": 0, "s": 1},      # no price: dropped
        {"t": "garbage", "p": 2.0, "s": 1},                    # unreadable: dropped
        {"p": 2.0, "s": 1},                                    # no time: dropped
    ])
    assert len(ticks) == 1


def test_five_second_bars_equal_direct_aggregation():
    ticks = [Tick(T(14, 0, i, 250000), 1.0 + (i * 7 % 5) / 10, 1 + i % 3) for i in range(0, 17)]
    direct = aggregate_ticks(ticks, "X", 5)
    via_seconds = rebucket(aggregate_ticks(ticks, "X", 1), 5)

    def key(c):
        return (c.ts, c.open, c.high, c.low, c.close, c.volume)

    assert [key(c) for c in direct] == [key(c) for c in via_seconds]
    assert direct[0].ts == T(14, 0, 0) and direct[1].ts == T(14, 0, 5)


def test_revealed_gate_never_returns_a_later_second():
    bars = aggregate_ticks([Tick(T(14, 0, i), 1.0 + i, 1) for i in range(0, 12)], "X", 1)
    for head_s in range(-1, 14):
        head = T(14, 0, 0) + timedelta(seconds=head_s)
        out = revealed(bars, head, 1)
        assert all(b.ts <= head for b in out)
        assert len(out) == max(0, min(head_s + 1, 12))
    # a 5s bar waits for its fifth second
    five = rebucket(bars, 5)
    assert [b.ts for b in revealed(five, T(14, 0, 3), 5)] == []
    assert [b.ts for b in revealed(five, T(14, 0, 4), 5)] == [T(14, 0, 0)]
    assert [b.ts for b in revealed(five, T(14, 0, 9), 5)] == [T(14, 0, 0), T(14, 0, 5)]


# --------------------------------------------------------------------------- #
# Alpaca provider: end clamp, cache only once the session is closed
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")


class Fake:
    def __init__(self, handler):
        self.calls: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request):
        self.calls.append(request)
        return self.handler(request)

    def client(self):
        return AlpacaDataClient(transport=httpx.MockTransport(self), sleep=lambda s: None)


def _provider(fake, temp_engine, feed="iex"):
    from app.config import Settings
    from app.db.connection import init_db

    init_db(temp_engine)
    return alpaca_mod.AlpacaProvider(
        Settings(market_data="alpaca", alpaca_feed=feed), client=fake.client(), engine=temp_engine
    )


def _rows(n=4):
    return [{"t": f"2026-06-19T14:00:0{i}.250000000Z", "p": 1.0 + i, "s": 2} for i in range(n)]


def _opt_handler(req):
    assert req.url.path == "/v1beta1/options/trades"
    return httpx.Response(200, json={"trades": {"SPY260619C00600000": _rows()}, "next_page_token": None})


def _stock_handler(req):
    assert req.url.path == "/v2/stocks/SPY/trades"
    return httpx.Response(200, json={"trades": _rows(), "symbol": "SPY", "next_page_token": None})


def _set_now(monkeypatch, when: datetime):
    monkeypatch.setattr(alpaca_mod, "_now", lambda: when)


LONG_AFTER = datetime(2026, 6, 22, 12, 0, tzinfo=UTC)  # the session is over (and then some)


def test_option_seconds_fetch_aggregate_and_use_market_data_host_only(temp_engine, monkeypatch):
    _set_now(monkeypatch, LONG_AFTER)
    fake = Fake(_opt_handler)
    bars = _provider(fake, temp_engine).get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0))
    assert [b.ts.second for b in bars] == [0, 1, 2, 3]
    assert bars[0].symbol == "SPY_260619_C_600" and bars[0].open == 1.0
    req = fake.calls[0]
    assert req.url.host == "data.alpaca.markets"
    assert req.url.params["symbols"] == "SPY260619C00600000" and req.url.params["limit"] == "10000"


def test_stock_seconds_index_underlying_is_labelled_spy_proxy(temp_engine, monkeypatch):
    _set_now(monkeypatch, LONG_AFTER)
    fake = Fake(_stock_handler)
    bars = _provider(fake, temp_engine).get_second_bars("SPX", T(13, 55, 0), T(14, 30, 0))
    assert bars and all(b.symbol == "SPX" and b.source_label == "SPY (proxy)" for b in bars)
    assert fake.calls[0].url.params["feed"] == "iex"


def test_end_is_clamped_out_of_the_latest_15_minutes(temp_engine, monkeypatch):
    now = T(14, 40, 0)
    _set_now(monkeypatch, now)
    fake = Fake(_opt_handler)
    p = _provider(fake, temp_engine)
    p.get_option_second_bars(OPT, T(14, 0, 0), T(15, 30, 0))
    end = datetime.fromisoformat(fake.calls[0].url.params["end"].replace("Z", "+00:00"))
    assert end <= now - timedelta(minutes=16)
    # a window that lies entirely inside the delay is not requested at all
    n = len(fake.calls)
    with pytest.raises(SecondsUnavailable):
        p.get_option_second_bars(OPT, T(14, 30, 0), T(15, 30, 0))
    assert len(fake.calls) == n


def test_not_cached_until_the_session_is_closed(temp_engine, monkeypatch):
    # 18:00 ET the same day: the window has long ended, but the session is not closed yet
    _set_now(monkeypatch, datetime(2026, 6, 19, 22, 0, tzinfo=UTC))
    fake = Fake(_opt_handler)
    p = _provider(fake, temp_engine)
    assert len(p.get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0))) == 4
    with temp_engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(second_bars_cache)) == 0
        assert c.scalar(select(func.count()).select_from(second_bars_coverage)) == 0
    p.get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0))
    assert len(fake.calls) == 2  # fetched again: nothing was stored


def test_cached_after_close_and_served_without_the_network(temp_engine, monkeypatch):
    _set_now(monkeypatch, LONG_AFTER)
    fake = Fake(_opt_handler)
    p = _provider(fake, temp_engine)
    first = p.get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0))
    with temp_engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(second_bars_cache)) == 4
        assert c.scalar(select(func.count()).select_from(second_bars_coverage)) == 1
        src = {r[0] for r in c.execute(select(second_bars_cache.c.source))}
    assert src == {"alpaca"}
    again = p.get_option_second_bars(OPT, T(13, 56, 0), T(14, 20, 0))  # inside the covered window
    assert len(fake.calls) == 1
    assert [(b.ts, b.close) for b in again] == [(b.ts, b.close) for b in first]


def test_no_prints_is_cached_as_covered_empty(temp_engine, monkeypatch):
    _set_now(monkeypatch, LONG_AFTER)
    fake = Fake(lambda req: httpx.Response(200, json={"trades": {}, "next_page_token": None}))
    p = _provider(fake, temp_engine)
    assert p.get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0)) == []
    assert p.get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0)) == []
    assert len(fake.calls) == 1


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_plan_errors_become_unavailable(temp_engine, monkeypatch, status):
    _set_now(monkeypatch, LONG_AFTER)
    p = _provider(Fake(lambda req: httpx.Response(status, json={})), temp_engine)
    with pytest.raises(SecondsUnavailable):
        p.get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0))
    with pytest.raises(SecondsUnavailable):
        p.get_second_bars("SPY", T(13, 55, 0), T(14, 30, 0))


def test_pagination_is_followed(temp_engine, monkeypatch):
    _set_now(monkeypatch, LONG_AFTER)

    def handler(req):
        if "page_token" not in req.url.params:
            return httpx.Response(
                200, json={"trades": {"SPY260619C00600000": _rows(2)}, "next_page_token": "n"}
            )
        return httpx.Response(200, json={"trades": {"SPY260619C00600000": [
            {"t": "2026-06-19T14:00:09.0Z", "p": 5.0, "s": 1}]}, "next_page_token": None})

    fake = Fake(handler)
    bars = _provider(fake, temp_engine).get_option_second_bars(OPT, T(13, 55, 0), T(14, 30, 0))
    assert len(fake.calls) == 2 and [b.ts.second for b in bars] == [0, 1, 9]


# --------------------------------------------------------------------------- #
# Mock provider + the API route (playhead gate, incremental, unavailable)
# --------------------------------------------------------------------------- #
ENTRY = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)  # the synthetic trade: entry, exit 10 min later


def _tid(client) -> int:
    return client.get("/api/days/2026-10-13").json()["positions"][0]["trade_id"]


def _secs(client, tid, head, **params):
    r = client.get(f"/api/replay/trade/{tid}/seconds", params={"playhead": head.isoformat(), **params})
    assert r.status_code == 200, r.text
    return r.json()


def _ts(b) -> datetime:
    return datetime.fromisoformat(b["ts"].replace("Z", "+00:00"))


def test_mock_mode_serves_seconds_consistent_with_the_minute_bars(client):
    tid = _tid(client)
    j = _secs(client, tid, ENTRY + timedelta(minutes=40))
    assert j["available"] is True and j["res"] == "1s"
    assert j["contract"] and j["underlying"]
    minute_bars = client.get(f"/api/replay/trade/{tid}/contract").json()["candles"]
    by_min = {
        datetime.fromisoformat(m["ts"].replace("Z", "+00:00")): m for m in minute_bars
    }
    groups: dict[datetime, list[dict]] = {}
    for b in j["contract"]:
        groups.setdefault(_ts(b).replace(second=0), []).append(b)
    checked = 0
    playhead_minute = max(groups)  # only its first second has been revealed
    for minute, bars in groups.items():
        m = by_min.get(minute)
        if m is None or minute == playhead_minute:
            continue
        bars.sort(key=_ts)
        assert bars[0]["open"] == pytest.approx(m["open"])
        assert bars[-1]["close"] == pytest.approx(m["close"])
        assert max(b["high"] for b in bars) == pytest.approx(m["high"])
        assert min(b["low"] for b in bars) == pytest.approx(m["low"])
        assert sum(b["volume"] for b in bars) == pytest.approx(m["volume"])
        assert len(bars) <= 60
        checked += 1
    assert checked > 10
    # some seconds are empty: no filler bars
    assert len(j["contract"]) < 60 * len(groups)


def test_mock_seconds_are_deterministic(client):
    tid = _tid(client)
    a = _secs(client, tid, ENTRY + timedelta(minutes=5))
    b = _secs(client, tid, ENTRY + timedelta(minutes=5))
    assert a["contract"] == b["contract"] and a["underlying"] == b["underlying"]


def test_playhead_gate_no_bar_after_the_playhead(client):
    tid = _tid(client)
    for off in (-20 * 60, -1, 0, 1, 37, 59, 60, 600, 601, 3600):
        head = ENTRY + timedelta(seconds=off)
        j = _secs(client, tid, head)
        for b in j["contract"] + j["underlying"]:
            assert _ts(b) <= head, (off, b["ts"])
    early = _secs(client, tid, ENTRY - timedelta(minutes=16))
    assert early["contract"] == [] and early["underlying"] == []  # before the lead start
    # sub-second playheads floor to the second
    j = _secs(client, tid, ENTRY + timedelta(seconds=5, microseconds=900000))
    assert max(_ts(b) for b in j["contract"]) <= ENTRY + timedelta(seconds=5)


def test_response_carries_nothing_that_reveals_the_exit(client):
    tid = _tid(client)
    j = _secs(client, tid, ENTRY - timedelta(minutes=1))
    assert set(j) == {
        "trade_id", "available", "reason", "res", "playhead", "after", "start_ts", "contract",
        "underlying", "contract_label", "underlying_symbol", "source_label",
    }
    # the exit is 10 minutes after entry: no bar from that far on in an earlier payload
    late = _secs(client, tid, ENTRY + timedelta(minutes=9, seconds=59))
    assert all(_ts(b) < ENTRY + timedelta(minutes=10) for b in late["contract"] + late["underlying"])


def test_incremental_after_returns_only_new_bars(client):
    tid = _tid(client)
    h1, h2 = ENTRY + timedelta(minutes=2), ENTRY + timedelta(minutes=3)
    first = _secs(client, tid, h1)
    last = max(_ts(b) for b in first["contract"])
    more = _secs(client, tid, h2, after=last.isoformat())
    assert more["contract"] and all(last < _ts(b) <= h2 for b in more["contract"])
    whole = _secs(client, tid, h2)
    assert len(first["contract"]) + len(more["contract"]) == len(whole["contract"])


def test_five_second_resolution_only_sends_completed_bars(client):
    tid = _tid(client)
    head = ENTRY + timedelta(minutes=2, seconds=7)
    j = _secs(client, tid, head, res="5s")
    assert j["res"] == "5s" and j["contract"]
    for b in j["contract"]:
        assert _ts(b).second % 5 == 0 and b["timeframe"] == "5s"
        assert _ts(b) + timedelta(seconds=4) <= head


def test_bad_resolution_and_unknown_trade(client):
    tid = _tid(client)
    r = client.get(f"/api/replay/trade/{tid}/seconds", params={"playhead": ENTRY.isoformat(), "res": "2s"})
    assert r.status_code == 400
    r = client.get("/api/replay/trade/99999/seconds", params={"playhead": ENTRY.isoformat()})
    assert r.status_code == 404
    assert client.get(f"/api/replay/trade/{tid}/seconds").status_code == 422  # playhead is required


def test_source_without_prints_says_why_and_sends_no_bars(client):
    from app.api.deps import provider_dep

    class NoTicks:
        def get_option_bars(self, contract, day, timeframe="1m"):
            return None

    client.app.dependency_overrides[provider_dep] = lambda: NoTicks()
    try:
        j = _secs(client, _tid(client), ENTRY + timedelta(minutes=5))
    finally:
        client.app.dependency_overrides.clear()
    assert j["available"] is False and j["reason"]
    assert j["contract"] == [] and j["underlying"] == []


def test_prints_denied_by_the_plan_degrade_with_a_reason(client):
    from app.api.deps import provider_dep
    from app.config import Settings
    from app.providers.mock import MockProvider

    class Denied(MockProvider):
        synthesizes_seconds = False

        def get_second_bars(self, symbol, start, end):
            raise SecondsUnavailable("trade prints are not available on this plan")

        def get_option_second_bars(self, contract, start, end):
            raise SecondsUnavailable("trade prints are not available on this plan")

    client.app.dependency_overrides[provider_dep] = lambda: Denied(Settings())
    try:
        j = _secs(client, _tid(client), ENTRY + timedelta(minutes=5))
    finally:
        client.app.dependency_overrides.clear()
    assert j["available"] is False and "plan" in j["reason"]
    assert j["contract"] == [] and j["underlying"] == []


def test_tick_feed_provider_is_cut_at_the_playhead_and_fetched_once(client):
    """A real-style provider (not mock): the route still cuts at the playhead, and the
    whole-window fetch happens once for many polls."""
    from app.api.deps import provider_dep
    from app.config import Settings
    from app.ingest import second_bars
    from app.providers.mock import MockProvider

    calls = {"n": 0}

    class Ticks(MockProvider):
        synthesizes_seconds = False
        memoize_seconds = True

        def get_second_bars(self, symbol, start, end):
            calls["n"] += 1
            return aggregate_ticks(
                [Tick(start + timedelta(seconds=i), 100 + i / 10, 1) for i in range(0, 3600, 2)],
                symbol, 1, "SPY (proxy)")

        def get_option_second_bars(self, contract, start, end):
            return aggregate_ticks(
                [Tick(start + timedelta(seconds=i), 1 + i / 1000, 1) for i in range(0, 3600, 3)],
                contract.contract_key, 1)

    second_bars.clear_memo()
    client.app.dependency_overrides[provider_dep] = lambda: Ticks(Settings())
    try:
        tid = _tid(client)
        for k in range(5):
            head = ENTRY + timedelta(seconds=30 * k)
            j = _secs(client, tid, head)
            assert all(_ts(b) <= head for b in j["contract"] + j["underlying"])
        assert j["source_label"] == "SPY (proxy)"
        assert calls["n"] == 1
    finally:
        client.app.dependency_overrides.clear()
        second_bars.clear_memo()
