"""Trade excursions (#51): pure MAE/MFE/stop/post-exit math, the API route and the MCP block.

Synthetic bars and made-up tickers only; no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.excursions import (
    compute_excursions,
    hold_excursion,
    post_exit_path,
    stop_price,
)
from app.models.market import Candle

from tests.test_desk_api import client, rules_file  # noqa: F401  (synthetic fills fixtures)

UTC = timezone.utc
T0 = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)  # 06:45 PT


def _bar(minute: int, lo: float, hi: float, close: float | None = None) -> Candle:
    return Candle(
        symbol="ZORP_261013_C_25", ts=T0 + timedelta(minutes=minute),
        open=(lo + hi) / 2, high=hi, low=lo,
        close=close if close is not None else (lo + hi) / 2, volume=10.0,
    )


# avg cost 1.50, 2 contracts => $200 per 1.00 of premium; risk $100 => stop at 1.00
KW = dict(avg_in=1.50, qty=2, risk=100.0)
END = T0 + timedelta(minutes=4)


def _hold_bars() -> list[Candle]:
    return [
        _bar(0, 1.45, 1.55, 1.50),   # entry minute, closes at cost
        _bar(1, 1.20, 1.50, 1.30),   # underwater
        _bar(2, 0.95, 1.30, 1.00),   # crosses the 1.00 stop line, underwater
        _bar(3, 1.00, 1.90, 1.80),   # best high 1.90
        _bar(4, 1.70, 1.85, 1.75),   # exit minute
    ]


def test_stop_price_from_risk():
    assert stop_price(1.50, 2, 100.0) == pytest.approx(1.00)
    assert stop_price(1.50, 2, None) is None
    # risk bigger than the whole premium: no premium level to cross
    assert stop_price(1.50, 1, 500.0) is None


def test_mae_mfe_in_dollars_and_r():
    h = hold_excursion(_hold_bars(), **KW, open_ts=T0, close_ts=END)
    assert h is not None
    # MAE: low 0.95 vs 1.50 = -0.55 x 100 x 2 = -$110 => -1.1 R on $100 risk
    assert h.mae_usd == pytest.approx(-110.0)
    assert h.mae_r == pytest.approx(-1.1)
    assert h.mae_ts == T0 + timedelta(minutes=2)
    # MFE: high 1.90 vs 1.50 = +0.40 x 200 = +$80 => +0.8 R
    assert h.mfe_usd == pytest.approx(80.0)
    assert h.mfe_r == pytest.approx(0.8)
    assert h.mfe_ts == T0 + timedelta(minutes=3)
    assert h.hold_bars == 5


def test_time_in_drawdown_counts_bars_closing_below_cost():
    h = hold_excursion(_hold_bars(), **KW, open_ts=T0, close_ts=END)
    # closes below 1.50 at minutes 1 and 2 only (minute 0 closes exactly at cost)
    assert h.drawdown_minutes == 2


def test_stop_crossing_and_time():
    h = hold_excursion(_hold_bars(), **KW, open_ts=T0, close_ts=END)
    assert h.crossed_stop is True
    assert h.crossed_stop_at == T0 + timedelta(minutes=2)


def test_stop_not_crossed():
    bars = [_bar(0, 1.40, 1.60), _bar(1, 1.10, 1.70)]
    h = hold_excursion(bars, **KW, open_ts=T0, close_ts=T0 + timedelta(minutes=1))
    assert h.crossed_stop is False and h.crossed_stop_at is None
    assert h.mae_r == pytest.approx(-0.8)


def test_hold_ignores_bars_outside_the_hold():
    bars = [_bar(-5, 0.10, 3.00), *_hold_bars(), _bar(9, 0.10, 3.00)]
    h = hold_excursion(bars, **KW, open_ts=T0, close_ts=END)
    assert h.mae_usd == pytest.approx(-110.0) and h.mfe_usd == pytest.approx(80.0)


def test_no_loss_gives_zero_mae():
    bars = [_bar(0, 1.50, 1.60), _bar(1, 1.55, 1.70)]
    h = hold_excursion(bars, **KW, open_ts=T0, close_ts=T0 + timedelta(minutes=1))
    assert h.mae_usd == 0 and h.mae_ts is None and h.drawdown_minutes == 0


def test_r_is_none_without_risk():
    h = hold_excursion(_hold_bars(), avg_in=1.5, qty=2, risk=None, open_ts=T0, close_ts=END)
    assert h.mae_r is None and h.mfe_r is None and h.mae_usd == pytest.approx(-110.0)


def test_post_exit_windows():
    bars = _hold_bars() + [
        _bar(10, 1.60, 2.10),   # +6 min after exit: inside 15
        _bar(20, 1.40, 1.60),   # +16: inside 30
        _bar(40, 0.50, 1.55),   # +36: inside 60 only
        _bar(70, 0.10, 9.00),   # outside every window
    ]
    w15, w30, w60 = post_exit_path(bars, **KW, exit_ts=END)
    assert (w15.minutes, w30.minutes, w60.minutes) == (15, 30, 60)
    # the bar opening at the exit (low 1.70, high 1.85) is the first post-exit bar
    assert w15.best_r == pytest.approx((2.10 - 1.50) * 200 / 100)    # +1.2 R
    assert w15.worst_r == pytest.approx((1.60 - 1.50) * 200 / 100)   # +0.2 R
    assert w30.worst_r == pytest.approx((1.40 - 1.50) * 200 / 100)   # -0.2 R
    assert w30.best_r == pytest.approx(1.2)
    assert w60.worst_r == pytest.approx((0.50 - 1.50) * 200 / 100)   # -2.0 R
    assert w60.best_r == pytest.approx(1.2)
    assert [w.bars for w in (w15, w30, w60)] == [2, 3, 4]


def test_post_exit_incomplete_and_empty():
    only_exit = post_exit_path([_bar(4, 1.7, 1.8)], **KW, exit_ts=END)
    assert all(w.bars == 1 and w.complete is False for w in only_exit)
    nothing = post_exit_path([_bar(0, 1.0, 2.0)], **KW, exit_ts=END)
    assert all(w.best_r is None and w.worst_r is None for w in nothing)


def test_compute_excursions_open_position_has_no_post_exit():
    ex = compute_excursions(_hold_bars(), **KW, open_ts=T0, close_ts=None)
    assert ex.post_exit == [] and ex.hold is not None
    assert ex.stop_price == pytest.approx(1.00)


def test_compute_excursions_no_bars_is_graceful():
    ex = compute_excursions([], **KW, open_ts=T0, close_ts=END)
    assert ex.hold is None
    assert all(w.best_r is None for w in ex.post_exit)


# --------------------------------------------------------------------- the API

def _tid(client, day="2026-10-13", idx=0) -> int:
    return client.get(f"/api/days/{day}").json()["positions"][idx]["trade_id"]


def test_route_available_in_mock_provider(client):
    d = client.get("/api/days/2026-10-13").json()["positions"][0]
    res = client.get(f"/api/trades/{d['trade_id']}/excursions")
    assert res.status_code == 200
    j = res.json()
    assert j["available"] is True
    assert j["tz_label"] == "PT" and j["status"] == "closed"
    # same risk/R definition as the ledger: default_risk_pct of premium
    assert j["risk"] == pytest.approx(d["risk"]) and j["risk_source"] == "default_risk_pct"
    assert j["stop_price"] == pytest.approx(1.50 - d["risk"] / 200, abs=1e-3)
    assert [f["side"] for f in j["fills"]] == ["B", "S"]
    assert j["fills"][0]["ts_pt"] == "06:45:00 PT"
    h = j["hold"]
    assert h["mae_usd"] <= 0 <= h["mfe_usd"]
    assert [w["minutes"] for w in j["post_exit"]] == [15, 30, 60]
    # deterministic
    assert client.get(f"/api/trades/{d['trade_id']}/excursions").json() == j


def test_route_has_no_dollars_left_on_table(client):
    j = client.get(f"/api/trades/{_tid(client)}/excursions").json()
    blob = str(j).lower()
    assert "left" not in blob and "table" not in blob


def test_route_unavailable_bars_still_lists_fills(client):
    from app.api.deps import provider_dep
    from app.providers.webull.errors import WebullError

    class NoBars:
        def get_option_bars(self, contract, day, timeframe="1m"):
            return None

        def get_candles(self, symbol, day, timeframe="1m"):
            raise WebullError("no candles")

    client.app.dependency_overrides[provider_dep] = lambda: NoBars()
    try:
        tid = _tid(client, idx=1)
        j = client.get(f"/api/trades/{tid}/excursions").json()
    finally:
        client.app.dependency_overrides.clear()
    assert j["available"] is False and j["reason"] == "option bars unavailable"
    assert len(j["fills"]) == 2 and j["hold"] is None and j["post_exit"] == []
    assert j["risk"] is not None


def test_route_404(client):
    assert client.get("/api/trades/999999/excursions").status_code == 404


# ------------------------------------------------------------------- MCP block

def _mcp_ctx(tmp_path, mode):
    from app.db.connection import get_engine
    from app.desk_mcp import mock, tools

    path = tmp_path / f"{mode}.db"
    mock.build_mock_db(path)
    return tools.Ctx(mode, str(path), get_engine(str(path)), mock.mock_rules())


def test_mcp_get_trade_has_excursions_in_mock_mode(tmp_path):
    from sqlalchemy import func, select

    from app.db.schema import option_bars_cache
    from app.desk_mcp import tools

    ctx = _mcp_ctx(tmp_path, "mock")
    tid = tools.get_day(ctx, "2026-10-08")["positions"][0]["trade_id"]
    ex = tools.get_trade(ctx, tid)["excursions"]
    assert ex["available"] is True and ex["tz_label"] == "PT"
    assert ex["hold"]["mae_usd"] <= 0
    assert tools.get_trade(ctx, tid)["excursions"] == ex  # deterministic
    with ctx.engine.connect() as conn:  # the read tool wrote nothing
        assert conn.execute(select(func.count()).select_from(option_bars_cache)).scalar() == 0


def test_mcp_real_mode_without_cached_bars_degrades(tmp_path):
    from app.desk_mcp import tools

    ctx = _mcp_ctx(tmp_path, "real")
    tid = tools.get_day(ctx, "2026-10-08")["positions"][0]["trade_id"]
    ex = tools.get_trade(ctx, tid)["excursions"]
    assert ex["available"] is False and ex["reason"].startswith("option bars unavailable")
    assert len(ex["fills"]) == 2
