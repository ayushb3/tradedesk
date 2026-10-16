"""Sim-trade persistence: P&L / R-multiple math, status, and round-tripping."""
from __future__ import annotations

from app.ingest.sim import list_sim_trades, save_sim_trade
from app.models.trades import Direction, TradeStatus, SimTradeCreate

from .conftest import TEST_DAY


def _payload(**over) -> SimTradeCreate:
    base = dict(
        symbol="AAPL",
        direction=Direction.long,
        session_date=TEST_DAY,
        entries=[{"ts": "2026-06-10T13:30:00Z", "qty": 100, "price": 10.00}],
        exits=[{"ts": "2026-06-10T14:00:00Z", "qty": 100, "price": 11.50}],
        stop_price=9.00,
        notes=None,
    )
    base.update(over)
    return SimTradeCreate(**base)


def test_long_plus_150_and_1_5R(temp_engine):
    # Long 100 @ 10.00 -> 11.50, stop 9.00.
    #   pnl/share = 11.50 - 10.00 = 1.50 ; gross = 1.50 * 100 = $150.00
    #   risk/share = |10.00 - 9.00| = 1.00 ; R = 1.50 / 1.00 = 1.5
    #   pnl_pct = 1.50 / 10.00 * 100 = 15.0 ; hold = 30 min = 1800 s
    t = save_sim_trade(_payload(), engine=temp_engine)
    assert t.direction == Direction.long
    assert t.gross_pnl == 150.0
    assert t.net_pnl == 150.0
    assert t.r_multiple == 1.5
    assert t.pnl_pct == 15.0
    assert t.hold_time_s == 1800
    assert t.status == TradeStatus.closed
    assert t.id is not None


def test_short_pnl_sign(temp_engine):
    # Short 50 @ 20.00 covered @ 22.00 -> price rose, a short LOSES.
    #   pnl/share = entry - exit = 20 - 22 = -2 ; gross = -2 * 50 = -$100
    losing = save_sim_trade(
        _payload(
            direction=Direction.short,
            entries=[{"ts": "2026-06-10T13:30:00Z", "qty": 50, "price": 20.00}],
            exits=[{"ts": "2026-06-10T13:45:00Z", "qty": 50, "price": 22.00}],
            stop_price=None,
        ),
        engine=temp_engine,
    )
    assert losing.direction == Direction.short
    assert losing.gross_pnl == -100.0
    assert losing.net_pnl < 0

    # Short winner: cover lower (20 -> 18) => +$100.
    winning = save_sim_trade(
        _payload(
            direction=Direction.short,
            entries=[{"ts": "2026-06-10T13:30:00Z", "qty": 50, "price": 20.00}],
            exits=[{"ts": "2026-06-10T13:45:00Z", "qty": 50, "price": 18.00}],
            stop_price=None,
        ),
        engine=temp_engine,
    )
    assert winning.gross_pnl == 100.0
    assert winning.net_pnl > 0


def test_partial_exit_stays_open(temp_engine):
    # Exit qty (40) < entry qty (100) -> position remains open.
    t = save_sim_trade(
        _payload(
            entries=[{"ts": "2026-06-10T13:30:00Z", "qty": 100, "price": 10.00}],
            exits=[{"ts": "2026-06-10T13:45:00Z", "qty": 40, "price": 11.00}],
            stop_price=None,
        ),
        engine=temp_engine,
    )
    assert t.status == TradeStatus.open
    assert t.qty == 100
    assert t.avg_exit == 11.0  # avg of the partial exit leg(s)


def test_r_multiple_none_without_stop(temp_engine):
    t = save_sim_trade(_payload(stop_price=None), engine=temp_engine)
    assert t.r_multiple is None
    # P&L still computed even without a stop.
    assert t.gross_pnl == 150.0


def test_list_sim_trades_round_trips(temp_engine):
    a = save_sim_trade(_payload(), engine=temp_engine)
    b = save_sim_trade(
        _payload(symbol="TSLA", stop_price=None),
        engine=temp_engine,
    )
    listed = list_sim_trades(engine=temp_engine)
    ids = {t.id for t in listed}
    assert a.id in ids and b.id in ids
    assert len(listed) == 2
    assert all(t.simulated for t in listed)
    symbols = {t.symbol for t in listed}
    assert symbols == {"AAPL", "TSLA"}
