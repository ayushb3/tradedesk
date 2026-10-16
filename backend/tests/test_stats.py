"""Journal analytics (M3 `app.analytics.stats`).

The number-crunching lives in pure helpers (`compute_summary`) that take plain
`Trade` lists, so most cases need no DB. `by_dimension` and `calendar` read
through the canonical `list_trades`, so those cases seed a temp DB via
`save_sim_trade` and point the app at it with the `isolated_db` fixture.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.analytics.stats import by_dimension, calendar, compute_summary
from app.ingest.sim import save_sim_trade
from app.models.trades import (
    Direction,
    SimTradeCreate,
    Trade,
    TradeStatus,
)

UTC = timezone.utc


def _trade(net_pnl, *, r=None, hold=None, status=TradeStatus.closed,
           symbol="AAA", open_ts=None, session_date=date(2026, 6, 10)) -> Trade:
    """Hand-built Trade with just the fields the stat helpers read."""
    return Trade(
        symbol=symbol,
        direction=Direction.long,
        open_ts=open_ts or datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
        session_date=session_date,
        qty=100,
        avg_entry=10.0,
        avg_exit=None if net_pnl is None else 11.0,
        net_pnl=net_pnl,
        r_multiple=r,
        hold_time_s=hold,
        status=status,
    )


# --------------------------------------------------------------------------- #
# compute_summary — profit factor
# --------------------------------------------------------------------------- #
def test_profit_factor_basic():
    trades = [_trade(100), _trade(-50), _trade(200), _trade(-50)]
    s = compute_summary(trades)
    assert s["trade_count"] == 4
    assert s["win_count"] == 2
    assert s["loss_count"] == 2
    assert s["gross_profit"] == 300.0
    assert s["gross_loss"] == -100.0
    assert s["total_pnl"] == 200.0
    # profit_factor = 300 / |−100| = 3.0
    assert s["profit_factor"] == 3.0
    assert s["best_trade"] == 200.0
    assert s["worst_trade"] == -50.0


def test_profit_factor_none_when_no_losses():
    trades = [_trade(100), _trade(50), _trade(25)]
    s = compute_summary(trades)
    assert s["loss_count"] == 0
    assert s["profit_factor"] is None  # inf-guard: no losses -> None


# --------------------------------------------------------------------------- #
# compute_summary — win rate / expectancy / avg_r
# --------------------------------------------------------------------------- #
def test_win_rate_expectancy_avg_r():
    # 3 wins (+100 each), 1 loss (-100). win_rate = 0.75.
    trades = [
        _trade(100, r=2.0),
        _trade(100, r=1.0),
        _trade(100, r=None),   # excluded from avg_r
        _trade(-100, r=-1.0),
    ]
    s = compute_summary(trades)
    assert s["win_rate"] == 0.75
    # avg_win = 100, avg_loss = -100
    assert s["avg_win"] == 100.0
    assert s["avg_loss"] == -100.0
    # expectancy = 0.75*100 - 0.25*100 = 50
    assert s["expectancy"] == 50.0
    # avg_r over the three trades that have one: mean(2.0, 1.0, -1.0) = 0.6667
    assert s["avg_r"] == pytest.approx(0.6667, abs=1e-4)


def test_avg_r_none_when_no_r():
    s = compute_summary([_trade(100), _trade(-50)])
    assert s["avg_r"] is None


def test_avg_hold_time_over_trades_with_hold():
    s = compute_summary([_trade(100, hold=600), _trade(-50, hold=1200)])
    assert s["avg_hold_time_s"] == 900.0


# --------------------------------------------------------------------------- #
# compute_summary — edges
# --------------------------------------------------------------------------- #
def test_empty_set_zeros_no_crash():
    s = compute_summary([])
    assert s["trade_count"] == 0
    assert s["win_count"] == 0
    assert s["loss_count"] == 0
    assert s["win_rate"] == 0.0
    assert s["gross_profit"] == 0.0
    assert s["gross_loss"] == 0.0
    assert s["total_pnl"] == 0.0
    assert s["profit_factor"] is None
    assert s["expectancy"] == 0.0
    assert s["avg_r"] is None
    assert s["best_trade"] is None
    assert s["worst_trade"] is None


def test_open_trades_excluded():
    # Open trades (no realized net_pnl) never enter the stats.
    trades = [_trade(100), _trade(None, status=TradeStatus.open)]
    s = compute_summary(trades)
    assert s["trade_count"] == 1
    assert s["total_pnl"] == 100.0


def test_breakeven_is_neither_win_nor_loss():
    s = compute_summary([_trade(100), _trade(0.0), _trade(-50)])
    assert s["trade_count"] == 3
    assert s["win_count"] == 1
    assert s["loss_count"] == 1  # the breakeven counts as neither


# --------------------------------------------------------------------------- #
# by_dimension('tod') — ET hour buckets (DB-backed via sim seeding)
# --------------------------------------------------------------------------- #
def _seed(symbol, session_date, entry_iso, exit_iso, entry_px, exit_px,
          qty=100, engine=None):
    return save_sim_trade(
        SimTradeCreate(
            symbol=symbol,
            direction=Direction.long,
            session_date=session_date,
            entries=[{"ts": entry_iso, "qty": qty, "price": entry_px}],
            exits=[{"ts": exit_iso, "qty": qty, "price": exit_px}],
        ),
        engine=engine,
    )


def test_by_dimension_tod_buckets_by_et_hour(isolated_db):
    from app.db.connection import get_engine

    engine = get_engine()  # settings now point at the temp DB
    # 13:30 UTC == 09:30 ET -> "09:00" bucket ; 15:15 UTC == 11:15 ET -> "11:00".
    _seed("AAA", date(2026, 6, 10), "2026-06-10T13:30:00Z", "2026-06-10T13:45:00Z",
          10.0, 11.0, engine=engine)
    _seed("BBB", date(2026, 6, 10), "2026-06-10T15:15:00Z", "2026-06-10T15:30:00Z",
          10.0, 9.0, engine=engine)

    rows = by_dimension("tod")
    buckets = {r["key"]: r for r in rows}
    assert set(buckets) == {"09:00", "11:00"}
    assert buckets["09:00"]["trade_count"] == 1
    assert buckets["09:00"]["pnl"] == 100.0
    assert buckets["11:00"]["pnl"] == -100.0
    # ranked by pnl desc
    assert rows[0]["key"] == "09:00"


def test_by_dimension_unknown_raises(isolated_db):
    with pytest.raises(ValueError):
        by_dimension("nonsense")


# --------------------------------------------------------------------------- #
# calendar — groups by session_date
# --------------------------------------------------------------------------- #
def test_calendar_groups_by_session_date(isolated_db):
    from app.db.connection import get_engine

    engine = get_engine()
    # Two trades on 06-10, one on 06-11.
    # All trades open AND close on the same ET day, so session_date == close_date.
    _seed("AAA", date(2026, 6, 10), "2026-06-10T13:30:00Z", "2026-06-10T13:45:00Z",
          10.0, 11.0, engine=engine)  # +100 win
    _seed("BBB", date(2026, 6, 10), "2026-06-10T14:30:00Z", "2026-06-10T14:45:00Z",
          10.0, 9.0, engine=engine)   # -100 loss
    _seed("CCC", date(2026, 6, 11), "2026-06-11T13:30:00Z", "2026-06-11T13:45:00Z",
          10.0, 12.0, engine=engine)  # +200 win

    cal = calendar("2026-06")
    assert set(cal) == {"2026-06-10", "2026-06-11"}
    assert cal["2026-06-10"]["trades"] == 2
    assert cal["2026-06-10"]["wins"] == 1
    assert cal["2026-06-10"]["losses"] == 1
    assert cal["2026-06-10"]["pnl"] == 0.0  # +100 - 100
    assert cal["2026-06-11"]["trades"] == 1
    assert cal["2026-06-11"]["wins"] == 1
    assert cal["2026-06-11"]["losses"] == 0
    assert cal["2026-06-11"]["pnl"] == 200.0
    # A date outside the seeded set is absent.
    assert "2026-06-12" not in cal


def test_calendar_attributes_to_close_date_not_open_date(isolated_db):
    """A trade opened on Jul 8 and closed on Jul 9 must appear in Jul 9's cell,
    not Jul 8's — calendar groups by exit date (ET), matching Webull behaviour."""
    from app.db.connection import get_engine

    engine = get_engine()
    # Open 2026-07-08 at 14:00 UTC (10:00 ET = Jul 8)
    # Close 2026-07-09 at 13:45 UTC (09:45 ET = Jul 9)
    _seed(
        "SPXW",
        date(2026, 7, 8),                      # session_date = open date
        "2026-07-08T14:00:00Z",               # entry on Jul 8 ET
        "2026-07-09T13:45:00Z",               # exit on Jul 9 ET  ← key!
        10.0, 11.0,
        engine=engine,
    )

    cal = calendar("2026-07")

    # Must appear under Jul 9 (close date), NOT Jul 8 (open / session date).
    assert "2026-07-09" in cal, (
        "Trade closed Jul 9 ET must appear in the Jul 9 calendar cell"
    )
    assert "2026-07-08" not in cal, (
        "Jul 8 must be empty — the trade closed on Jul 9, not Jul 8"
    )
    assert cal["2026-07-09"]["trades"] == 1
    assert cal["2026-07-09"]["wins"] == 1
    assert cal["2026-07-09"]["pnl"] == 100.0
