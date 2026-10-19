"""Replay follow-ups: net result gated like window_end_ts, and the server-filtered
day-so-far for the rule bar. Synthetic fixtures, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.ingest.imports import import_fills
from app.ingest.robinhood import RobinhoodExport
from tests.test_desk_api import _order, rules_file  # noqa: F401
from tests.test_replay_contract import ENTRY, _Bars, _iso, _override, _tid
from tests.test_desk_api import client as day_client  # noqa: F401  (synthetic option-fill day)

UTC = timezone.utc
M = timedelta


# ----------------------------------------------------------------- real_result

def _exit_minute(client, tid) -> int:
    fills = client.get(
        f"/api/replay/trade/{tid}/fills", params={"playhead": _iso(600)}
    ).json()["fills"]
    exit_ts = max(datetime.fromisoformat(f["ts"]) for f in fills if f["role"] == "exit")
    return int((exit_ts - ENTRY).total_seconds() // 60)


def _get(client, tid, path, params):
    """The contract endpoint needs premium bars; the others use the mock provider."""
    if path == "/contract":
        _override(client, _Bars())
    try:
        r = client.get(f"/api/replay/trade/{tid}{path}", params=params)
    finally:
        client.app.dependency_overrides.clear()
    assert r.status_code == 200
    return r


def test_real_result_is_null_before_the_exit_on_every_endpoint(day_client):
    client = day_client
    tid = _tid(client)
    ex = _exit_minute(client, tid)
    for minute in (None, -1, 0, ex - 1):
        params = {} if minute is None else {"playhead": _iso(minute)}
        for path in ("", "/fills", "/contract"):
            if path == "/fills" and minute is None:
                continue  # the fills poll requires a playhead
            r = _get(client, tid, path, params)
            body = r.json()
            assert body["real_result"] is None, (path, minute)
            assert body["window_end_ts"] is None
            assert "net_pnl" not in r.text


def test_real_result_at_and_after_the_exit_equals_the_trades_net(day_client):
    client = day_client
    tid = _tid(client)
    ex = _exit_minute(client, tid)
    trade = client.get(f"/api/trades/{tid}").json()
    assert trade["net_pnl"] == pytest.approx(97.90)
    assert trade["r_multiple"] is not None
    want = {"net_pnl": trade["net_pnl"], "r": trade["r_multiple"], "r_basis": trade["r_basis"]}
    assert want["r_basis"] == "assumed"  # the synthetic day has no written max loss
    for minute in (ex, ex + 45):  # the exit is inclusive
        for path in ("", "/fills", "/contract"):
            got = _get(client, tid, path, {"playhead": _iso(minute)}).json()["real_result"]
            assert got["net_pnl"] == pytest.approx(want["net_pnl"]), (path, minute)
            assert got["r"] == pytest.approx(want["r"])
            assert got["r_basis"] == want["r_basis"]


# ----------------------------------------------------------------- day-so-far

DAY = datetime(2026, 10, 14, 13, 30, tzinfo=UTC)  # 06:30 PT


def _rt(oid, strike, buy_min, sell_min, buy_px, sell_px):
    kw = dict(strike=f"{strike}.0000", expiry="2026-10-14")
    return [
        _order(f"{oid}-b", "buy", DAY + M(minutes=buy_min), buy_px, 1,
               net=buy_px * 100, direction="debit", **kw),
        _order(f"{oid}-s", "sell", DAY + M(minutes=sell_min), sell_px, 1,
               net=sell_px * 100, direction="credit", **kw),
    ]


@pytest.fixture()
def overlap_client(isolated_db, rules_file):  # noqa: F811
    from app.main import create_app

    c = TestClient(create_app())
    orders = (
        _rt("p", 700, 0, 10, 1.00, 1.50)       # closes first: +50
        + _rt("q", 701, 5, 120, 2.00, 1.00)    # opens early, closes late: -100
        + _rt("z", 702, 30, 40, 1.00, 1.20)    # the replayed trade: +20
        + _rt("w", 710, 70, 80, 1.00, 0.50)    # later, still before the cutoff: -50
        + _rt("v", 735, 135, 140, 1.00, 1.30)  # after the 08:30 PT cutoff: +30
    )
    import_fills(RobinhoodExport(orders))
    return c


def _ids(client):
    return [p["trade_id"] for p in client.get("/api/days/2026-10-14").json()["positions"]]


def _sofar(client, tid):
    r = client.get(f"/api/replay/trade/{tid}/day-so-far")
    assert r.status_code == 200
    return r.json()


def test_day_so_far_excludes_later_trades_and_the_trade_itself(overlap_client):
    p, q, z, w, v = _ids(overlap_client)
    j = _sofar(overlap_client, z)
    assert (j["net_so_far"], j["prior_trades"]) == (pytest.approx(50.0), 1)
    # the first trade of the day sees nothing: later trades never count
    first = _sofar(overlap_client, p)
    assert (first["net_so_far"], first["prior_trades"]) == (0, 0)


def test_trade_closing_after_the_entry_is_excluded_until_it_closes(overlap_client):
    p, q, z, w, v = _ids(overlap_client)
    # q is open at z's entry (closes after it), so only p counts for z and for w
    assert _sofar(overlap_client, z)["prior_trades"] == 1
    assert _sofar(overlap_client, w)["prior_trades"] == 2  # p and z; q still open
    after_all = _sofar(overlap_client, v)  # q has closed by now
    assert after_all["prior_trades"] == 4
    assert after_all["net_so_far"] == pytest.approx(50 - 100 + 20 - 50)
    assert after_all["entry"] == "after" and _sofar(overlap_client, z)["entry"] == "before"


def test_day_so_far_carries_rules_and_no_end_of_day_flags(overlap_client):
    p, q, z, w, v = _ids(overlap_client)
    j = _sofar(overlap_client, z)
    assert j["daily_stop"] == -100 and j["daily_target"] == 40
    assert j["cutoff"] == "08:30"
    assert set(j) == {
        "trade_id", "session_date", "net_so_far", "prior_trades", "daily_stop",
        "daily_target", "cutoff", "entry", "call_source",
    }
    text = overlap_client.get(f"/api/replay/trade/{z}/day-so-far").text.lower()
    for leak in ("hit", "blown", "trades_after", "crossed", "stop_result", "positions", "fills"):
        assert leak not in text
    # the final day net must not be derivable: nothing equals the day's full total
    full = overlap_client.get("/api/days/2026-10-14").json()["stats"]["net"]
    assert j["net_so_far"] != pytest.approx(full)


def test_call_source_prefers_the_trade_field(overlap_client):
    p, q, z, w, v = _ids(overlap_client)
    assert _sofar(overlap_client, z)["call_source"] is None
    overlap_client.put(f"/api/trades/{z}/call-source", json={"call_source": "mentor"})
    assert _sofar(overlap_client, z)["call_source"] == "mentor"
    overlap_client.put(f"/api/trades/{z}/call-source", json={"call_source": "own"})
    assert _sofar(overlap_client, z)["call_source"] == "own"


def test_day_so_far_unknown_trade_404(overlap_client):
    assert overlap_client.get("/api/replay/trade/999999/day-so-far").status_code == 404
