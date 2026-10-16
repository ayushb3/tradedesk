"""FIFO round-trip builder (M3 `app.ingest.imports`).

Drives `import_fills` with a tiny fake provider whose `get_fills()` returns a
hand-built `Fill` list, then reads the rebuilt trades back through the canonical
`app.ingest.query.list_trades`. Every case uses an isolated temp-file engine so
the real DB is never touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ingest.imports import import_fills
from app.ingest.query import list_trades
from app.models.trades import Direction, Fill, Side, TradeStatus

UTC = timezone.utc
# A fixed Wednesday morning (June => EDT, 13:30 UTC == 09:30 ET).
BASE = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)


def _ts(minute_offset: int) -> datetime:
    return BASE + timedelta(minutes=minute_offset)


class FakeProvider:
    """Minimal DataProvider stand-in: only `get_fills` is exercised by import."""

    def __init__(self, fills: list[Fill]) -> None:
        self._fills = fills

    def get_fills(self, since=None) -> list[Fill]:
        return list(self._fills)


def _fill(i: int, symbol: str, side: Side, qty: float, price: float, minute: int,
          fees: float = 0.0, multiplier: float = 1.0,
          option_type: str | None = None, strike: float | None = None,
          expiry: str | None = None) -> Fill:
    return Fill(
        provider_fill_id=f"f-{symbol}-{i:04d}",
        account="TEST",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        ts=_ts(minute),
        fees=fees,
        multiplier=multiplier,
        option_type=option_type,
        strike=strike,
        expiry=expiry,
        simulated=False,
    )


def _import(fills: list[Fill], engine):
    return import_fills(FakeProvider(fills), engine=engine)


def _trades(engine, symbol=None):
    return list_trades(simulated=False, symbol=symbol, engine=engine)


# --------------------------------------------------------------------------- #
# Simple round-trips
# --------------------------------------------------------------------------- #
def test_simple_long(temp_engine):
    fills = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 12.0, 10),
    ]
    res = _import(fills, temp_engine)
    assert res == {"fills_imported": 2, "trades_built": 1}

    (t,) = _trades(temp_engine)
    assert t.direction == Direction.long
    assert t.status == TradeStatus.closed
    assert t.qty == 100
    assert t.avg_entry == 10.0
    assert t.avg_exit == 12.0
    assert t.gross_pnl == 200.0
    assert t.net_pnl == pytest.approx(200.0)  # no fees
    assert t.hold_time_s == 600  # 10 min


def test_simple_short(temp_engine):
    fills = [
        _fill(1, "BBB", Side.sell, 100, 12.0, 0),
        _fill(2, "BBB", Side.buy, 100, 10.0, 10),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)
    assert t.direction == Direction.short
    assert t.status == TradeStatus.closed
    assert t.qty == 100
    assert t.avg_entry == 12.0
    assert t.avg_exit == 10.0
    assert t.gross_pnl == 200.0  # short profits when price falls
    assert t.net_pnl == pytest.approx(200.0)


# --------------------------------------------------------------------------- #
# Scale-in / scale-out
# --------------------------------------------------------------------------- #
def test_scale_in_out(temp_engine):
    # buy 100@10, buy 50@11, sell 50@13, sell 100@14 -> one long, qty 150.
    fills = [
        _fill(1, "CCC", Side.buy, 100, 10.0, 0),
        _fill(2, "CCC", Side.buy, 50, 11.0, 5),
        _fill(3, "CCC", Side.sell, 50, 13.0, 10),
        _fill(4, "CCC", Side.sell, 100, 14.0, 15),
    ]
    res = _import(fills, temp_engine)
    assert res["trades_built"] == 1

    (t,) = _trades(temp_engine)
    assert t.direction == Direction.long
    assert t.status == TradeStatus.closed
    assert t.qty == 150  # peak position size
    # avg_entry = (100*10 + 50*11)/150 = 1550/150
    assert t.avg_entry == pytest.approx(10.3333, abs=1e-4)
    # avg_exit  = (50*13 + 100*14)/150 = 2050/150
    assert t.avg_exit == pytest.approx(13.6667, abs=1e-4)
    # gross = (avg_exit - avg_entry) * 150 ~= 500 (see rounding note in report)
    assert t.gross_pnl == pytest.approx(500.0, abs=0.05)


# --------------------------------------------------------------------------- #
# Cross-through-zero (position flips sign on one fill)
# --------------------------------------------------------------------------- #
def test_cross_through_zero(temp_engine):
    # buy 100@10, sell 150@12, buy 50@11
    #   -> closed long  qty 100, gross (12-10)*100 = 200
    #   -> closed short qty 50,  gross (12-11)*50  = 50
    # The sell 150@12 fill is split: 100 sh close the long, 50 sh open the short.
    # Its $1.50 fee apportions pro-rata: 100/150 -> $1.00 (long exit),
    #                                     50/150 -> $0.50 (short entry).
    fills = [
        _fill(1, "DDD", Side.buy, 100, 10.0, 0, fees=0.0),
        _fill(2, "DDD", Side.sell, 150, 12.0, 10, fees=1.50),
        _fill(3, "DDD", Side.buy, 50, 11.0, 20, fees=0.0),
    ]
    res = _import(fills, temp_engine)
    assert res["trades_built"] == 2

    trades = _trades(temp_engine)
    by_dir = {t.direction: t for t in trades}
    assert set(by_dir) == {Direction.long, Direction.short}

    lng = by_dir[Direction.long]
    assert lng.qty == 100
    assert lng.gross_pnl == 200.0
    # split-fee apportioning: long carries 100/150 of the $1.50 sell fee.
    assert lng.fees == pytest.approx(1.00, abs=1e-6)
    assert lng.net_pnl == pytest.approx(199.00, abs=1e-6)

    sht = by_dir[Direction.short]
    assert sht.qty == 50
    assert sht.gross_pnl == 50.0
    # short entry carries the remaining 50/150 of the $1.50 fee.
    assert sht.fees == pytest.approx(0.50, abs=1e-6)
    assert sht.net_pnl == pytest.approx(49.50, abs=1e-6)


# --------------------------------------------------------------------------- #
# Open tail (position never returns to flat)
# --------------------------------------------------------------------------- #
def test_open_tail(temp_engine):
    fills = [_fill(1, "EEE", Side.buy, 100, 10.0, 0)]
    res = _import(fills, temp_engine)
    assert res["trades_built"] == 1

    (t,) = _trades(temp_engine)
    assert t.status == TradeStatus.open
    assert t.direction == Direction.long
    assert t.qty == 100
    assert t.avg_entry == 10.0
    assert t.avg_exit is None
    assert t.close_ts is None
    assert t.gross_pnl is None
    assert t.net_pnl is None
    assert t.hold_time_s is None


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #
def test_idempotent_reimport(temp_engine):
    fills = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 12.0, 10),
        _fill(3, "BBB", Side.sell, 100, 12.0, 0),
        _fill(4, "BBB", Side.buy, 100, 10.0, 10),
    ]
    first = _import(fills, temp_engine)
    assert first["fills_imported"] == 4
    assert first["trades_built"] == 2

    # Incremental refresh: FakeProvider ignores `since` and keeps returning the
    # same fixed set, but all 4 dedupe on provider_fill_id, so nothing new is
    # inserted. The rebuilt trade set is identical either way — idempotency is
    # about the resulting journal, not the insert count.
    second = _import(fills, temp_engine)
    assert second["fills_imported"] == 0
    assert second["trades_built"] == 2

    after = _trades(temp_engine)
    assert len(after) == 2
    # Values are stable across the rebuild.
    for t in after:
        assert t.gross_pnl == 200.0


def test_reimport_wipes_stale_nonsim_fills(temp_engine):
    """A *full* re-import with a different fill set replaces the first entirely —
    stale non-sim fills/trades from an earlier (e.g. mock-mode) import are gone,
    so demo data can't linger in the journal after the mock→live cutover."""
    stale = [
        _fill(1, "AAA", Side.buy, 100, 10.0, 0),
        _fill(2, "AAA", Side.sell, 100, 12.0, 10),
    ]
    _import(stale, temp_engine)
    assert len(_trades(temp_engine)) == 1

    # Live import returns a completely different, authoritative set. Use
    # full=True to model the mock->live cutover (an ordinary incremental
    # refresh would only ask for fills since the stale cursor and merge).
    live = [
        _fill(3, "ZZZ", Side.buy, 50, 20.0, 0),
        _fill(4, "ZZZ", Side.sell, 50, 22.0, 5),
    ]
    import_fills(FakeProvider(live), engine=temp_engine, full=True)

    after = _trades(temp_engine)
    assert len(after) == 1, "stale trade should be gone after re-import"
    assert after[0].symbol == "ZZZ"


def test_reimport_preserves_sim_trades(temp_engine):
    """Importing live fills must never delete simulated (replay) trades."""
    from app.ingest.sim import list_sim_trades, save_sim_trade
    from app.models.trades import SimTradeCreate

    save_sim_trade(
        SimTradeCreate(
            symbol="SIMX",
            direction=Direction.long,
            session_date=BASE.date(),
            entries=[{"qty": 10, "price": 5.0, "ts": BASE.isoformat()}],
            exits=[{"qty": 10, "price": 6.0, "ts": (BASE + timedelta(minutes=5)).isoformat()}],
        ),
        engine=temp_engine,
    )
    assert len(list_sim_trades(temp_engine)) == 1

    _import([_fill(1, "AAA", Side.buy, 100, 10.0, 0),
             _fill(2, "AAA", Side.sell, 100, 12.0, 10)], temp_engine)

    # Sim trade survives the non-sim wipe/rebuild.
    assert len(list_sim_trades(temp_engine)) == 1
    assert len(_trades(temp_engine)) == 1  # the one real trade


# --------------------------------------------------------------------------- #
# Options contract multiplier
# --------------------------------------------------------------------------- #
def test_option_pnl_uses_contract_multiplier(temp_engine):
    """The acceptance test: SPXW buy 10@3.01 then sell 10@2.46 with multiplier=100
    must yield gross_pnl == -550.00, not -5.47.

    Calculation:
      pnl_per_share = 2.46 - 3.01 = -0.55
      gross_pnl     = -0.55 * 10 * 100 = -550.00
    """
    fills = [
        _fill(1, "SPXW", Side.buy, 10, 3.01, 0, multiplier=100.0),
        _fill(2, "SPXW", Side.sell, 10, 2.46, 10, multiplier=100.0),
    ]
    res = _import(fills, temp_engine)
    assert res == {"fills_imported": 2, "trades_built": 1}

    (t,) = _trades(temp_engine)
    assert t.direction == Direction.long
    assert t.status == TradeStatus.closed
    assert t.qty == 10
    assert t.avg_entry == pytest.approx(3.01, abs=1e-4)
    assert t.avg_exit == pytest.approx(2.46, abs=1e-4)
    assert t.gross_pnl == pytest.approx(-550.00, abs=0.01)
    assert t.net_pnl == pytest.approx(-550.00, abs=0.01)  # no fees
    # pnl_pct is per-share ratio, multiplier-invariant
    assert t.pnl_pct == pytest.approx(-18.2723, abs=0.01)


def test_stock_pnl_unaffected_by_default_multiplier(temp_engine):
    """A pure-stock round-trip with multiplier=1.0 (default) is unchanged.

    buy 100@10, sell 100@12 → gross_pnl = (12-10)*100*1 = 200.0
    """
    fills = [
        _fill(1, "RIVN", Side.buy, 100, 10.0, 0),   # multiplier defaults to 1.0
        _fill(2, "RIVN", Side.sell, 100, 12.0, 10),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)
    assert t.gross_pnl == pytest.approx(200.0, abs=0.01)
    assert t.net_pnl == pytest.approx(200.0, abs=0.01)


def test_option_short_pnl_with_multiplier(temp_engine):
    """Short option: sell 5@4.00 then buy 5@3.00 with multiplier=100.
    gross_pnl = (4.00 - 3.00) * 5 * 100 = 500.00
    """
    fills = [
        _fill(1, "SPY", Side.sell, 5, 4.00, 0, multiplier=100.0),
        _fill(2, "SPY", Side.buy, 5, 3.00, 10, multiplier=100.0),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)
    assert t.direction == Direction.short
    assert t.gross_pnl == pytest.approx(500.00, abs=0.01)


# --------------------------------------------------------------------------- #
# Per-contract grouping — the key regression
# --------------------------------------------------------------------------- #
def test_two_distinct_contracts_same_ticker_are_independent(temp_engine):
    """THE KEY REGRESSION: two distinct SPXW contracts (7500C and 7475P) with
    INTERLEAVED fills must produce TWO separate trades, NOT one netted trade.

    Before the fix, ticker-only grouping matched the CALL buy against the PUT
    sell, cancelling P&L across unrelated instruments.

    Fills:
      t=0  BUY  1 SPXW 7500C @ 3.00  (open long 7500C)
      t=1  BUY  1 SPXW 7475P @ 2.00  (open long 7475P) — interleaved
      t=2  SELL 1 SPXW 7500C @ 2.50  (close 7500C at a loss: -0.50 * 100 = -50)
      t=3  SELL 1 SPXW 7475P @ 2.80  (close 7475P at a gain: +0.80 * 100 = +80)

    Expected: two closed trades with independent P&L:
      7500C: gross_pnl = (2.50 - 3.00) * 1 * 100 = -50.00
      7475P: gross_pnl = (2.80 - 2.00) * 1 * 100 = +80.00
    """
    fills = [
        _fill(1, "SPXW", Side.buy,  1, 3.00, 0,  multiplier=100.0,
              option_type="CALL", strike=7500.0, expiry="2026-07-11"),
        _fill(2, "SPXW", Side.buy,  1, 2.00, 1,  multiplier=100.0,
              option_type="PUT",  strike=7475.0, expiry="2026-07-11"),
        _fill(3, "SPXW", Side.sell, 1, 2.50, 10, multiplier=100.0,
              option_type="CALL", strike=7500.0, expiry="2026-07-11"),
        _fill(4, "SPXW", Side.sell, 1, 2.80, 11, multiplier=100.0,
              option_type="PUT",  strike=7475.0, expiry="2026-07-11"),
    ]
    res = _import(fills, temp_engine)
    assert res["fills_imported"] == 4

    # Must produce exactly two separate trades (not one netted trade).
    assert res["trades_built"] == 2, (
        "Expected 2 separate trades (one per contract), got one netted trade — "
        "ticker-only grouping is still in effect!"
    )

    all_trades = _trades(temp_engine)
    assert len(all_trades) == 2

    # Find each trade by contract_label (or gross_pnl as fallback).
    losses = [t for t in all_trades if t.gross_pnl is not None and t.gross_pnl < 0]
    gains  = [t for t in all_trades if t.gross_pnl is not None and t.gross_pnl > 0]

    assert len(losses) == 1, "Expected exactly one losing trade (the 7500C)"
    assert len(gains)  == 1, "Expected exactly one winning trade (the 7475P)"

    assert losses[0].gross_pnl == pytest.approx(-50.00, abs=0.01), (
        "7500C P&L: (2.50 - 3.00) * 1 * 100 = -50"
    )
    assert gains[0].gross_pnl == pytest.approx(80.00, abs=0.01), (
        "7475P P&L: (2.80 - 2.00) * 1 * 100 = +80"
    )

    # Both trades have the same ticker symbol (for analytics grouping).
    assert all(t.symbol == "SPXW" for t in all_trades)

    # Both trades carry a descriptive contract_label.
    labels = {t.contract_label for t in all_trades}
    assert "SPXW 7500C 07/11" in labels, f"Expected 7500C label in {labels}"
    assert "SPXW 7475P 07/11" in labels, f"Expected 7475P label in {labels}"


def test_short_option_round_trip_correct_sign(temp_engine):
    """Short option SELL_TO_OPEN then BUY_TO_CLOSE yields positive P&L when
    the price drops (standard short-premium trade).

    SPXW 5500C: sell 2 @ 4.00, buy 2 @ 1.50 → gross = (4.00-1.50)*2*100 = 500
    """
    fills = [
        _fill(1, "SPXW", Side.sell, 2, 4.00, 0,  multiplier=100.0,
              option_type="CALL", strike=5500.0, expiry="2026-07-18"),
        _fill(2, "SPXW", Side.buy,  2, 1.50, 30, multiplier=100.0,
              option_type="CALL", strike=5500.0, expiry="2026-07-18"),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)
    assert t.direction == Direction.short
    assert t.status == TradeStatus.closed
    assert t.gross_pnl == pytest.approx(500.00, abs=0.01)
    assert t.net_pnl == pytest.approx(500.00, abs=0.01)
    assert t.contract_label == "SPXW 5500C 07/18"


def test_stock_fills_still_group_by_ticker(temp_engine):
    """Stock fills (no option fields) continue to group by ticker alone.
    Two RIVN round-trips back-to-back must both be captured (regression guard).
    """
    fills = [
        _fill(1, "RIVN", Side.buy,  100, 10.0, 0),
        _fill(2, "RIVN", Side.sell, 100, 12.0, 10),
        _fill(3, "RIVN", Side.buy,  50,  11.0, 20),
        _fill(4, "RIVN", Side.sell, 50,  13.0, 30),
    ]
    res = _import(fills, temp_engine)
    assert res["trades_built"] == 2

    all_trades = _trades(temp_engine)
    assert len(all_trades) == 2
    assert all(t.symbol == "RIVN" for t in all_trades)
    # Stock contract_label == ticker
    assert all(t.contract_label == "RIVN" for t in all_trades)


def test_contract_label_stock_is_just_ticker(temp_engine):
    """A plain stock import produces contract_label == symbol (no option info)."""
    fills = [
        _fill(1, "HOOD", Side.buy,  50, 20.0, 0),
        _fill(2, "HOOD", Side.sell, 50, 22.0, 5),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)
    assert t.contract_label == "HOOD"


# --------------------------------------------------------------------------- #
# FIFO lot-matching P&L — regression tests
# --------------------------------------------------------------------------- #
def test_fifo_lot_matching_diverges_from_avg_cost_on_scale_in_out_in(temp_engine):
    """FIFO lot-matching produces a DIFFERENT gross_pnl than average-cost when
    the position is partially reduced and then scaled back in before final close,
    causing max_size < total_traded_qty.

    Scenario (no multiplier — stock-like to isolate the arithmetic):
      t=0  BUY  5 @ 1.00  (lot A opens, position=+5, max_size=5)
      t=1  SELL 3 @ 3.00  (partial exit, position=+2; lot A has 2 remaining)
      t=2  BUY  3 @ 2.00  (scale back in, position=+5; lot B opens at 2.00)
      t=3  SELL 5 @ 3.00  (full close, position=0)

    FIFO lot-matching:
      SELL3 matches 3 from lot A@1.00:  (3-1)*3 =  +6
      SELL5 matches 2 remaining lot A@1.00: (3-1)*2 = +4
            then 3 from lot B@2.00: (3-2)*3 = +3
      Total: +6 +4 +3 = +13

    Old average-cost approach (uses max_size=5 as qty):
      avg_entry = (5*1 + 3*2) / 8 = 1.375
      avg_exit  = (3*3 + 5*3) / 8 = 3.00
      gross     = (3.00 - 1.375) * 5 * 1 = 8.125  ← WRONG

    FIFO is correct (verified independently: 2 profitable lots + 1 small-gain lot = +13).
    """
    fills = [
        _fill(1, "FFF", Side.buy,  5, 1.0, 0),
        _fill(2, "FFF", Side.sell, 3, 3.0, 1),
        _fill(3, "FFF", Side.buy,  3, 2.0, 2),
        _fill(4, "FFF", Side.sell, 5, 3.0, 3),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)

    # With FIFO lot-matching:
    assert t.gross_pnl == pytest.approx(13.0, abs=0.01), (
        "FIFO should give +13.0; avg-cost with max_size would give ~8.13"
    )
    assert t.net_pnl == pytest.approx(13.0, abs=0.01)
    assert t.direction == Direction.long
    assert t.status == TradeStatus.closed


def test_fifo_lot_matching_option_scale_in_single_exit(temp_engine):
    """FIFO lot-matching on an option scaled into at two prices, closed in one exit.

    For a COMPLETE round-trip (all lots closed), FIFO == avg-cost algebraically.
    This test asserts the exact integer P&L (no fractional cents from float division)
    and confirms the per-lot FIFO wiring is active with multiplier=100.

    Entries:
      buy 3 @ 2.00  (lot A)
      buy 2 @ 5.00  (lot B)
    Exit:
      sell 5 @ 4.00

    FIFO:
      lot A: (4.00-2.00)*3*100 = +600
      lot B: (4.00-5.00)*2*100 = -200
      gross = +400  (exact integer, no float artifact)
    """
    fills = [
        _fill(1, "SPXW", Side.buy,  3, 2.00, 0,  multiplier=100.0,
              option_type="CALL", strike=5500.0, expiry="2026-07-25"),
        _fill(2, "SPXW", Side.buy,  2, 5.00, 5,  multiplier=100.0,
              option_type="CALL", strike=5500.0, expiry="2026-07-25"),
        _fill(3, "SPXW", Side.sell, 5, 4.00, 10, multiplier=100.0,
              option_type="CALL", strike=5500.0, expiry="2026-07-25"),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)

    assert t.gross_pnl == pytest.approx(400.0, abs=0.01)
    assert t.net_pnl == pytest.approx(400.0, abs=0.01)
    assert t.direction == Direction.long
    assert t.avg_entry == pytest.approx(3.20, abs=1e-4)   # (3*2+2*5)/5 = 3.20
    assert t.avg_exit  == pytest.approx(4.00, abs=1e-4)


def test_fifo_lot_matching_option_scale_in_out_in_with_multiplier(temp_engine):
    """FIFO gross_pnl on an option with scale-in, partial exit, scale-back-in,
    full close — same scenario as the stock test but with multiplier=100.

    Entries: 5@1.00, 3@2.00; Exits: 3@3.00, 5@3.00.
    FIFO  = +13 per unit  × 100 = +1300
    avg-cost (with max_size=5 bug) would give 8.125 × 100 = +812.50 ← wrong
    """
    fills = [
        _fill(1, "SPXW", Side.buy,  5, 1.0, 0, multiplier=100.0,
              option_type="PUT", strike=7500.0, expiry="2026-07-11"),
        _fill(2, "SPXW", Side.sell, 3, 3.0, 1, multiplier=100.0,
              option_type="PUT", strike=7500.0, expiry="2026-07-11"),
        _fill(3, "SPXW", Side.buy,  3, 2.0, 2, multiplier=100.0,
              option_type="PUT", strike=7500.0, expiry="2026-07-11"),
        _fill(4, "SPXW", Side.sell, 5, 3.0, 3, multiplier=100.0,
              option_type="PUT", strike=7500.0, expiry="2026-07-11"),
    ]
    _import(fills, temp_engine)
    (t,) = _trades(temp_engine)

    assert t.gross_pnl == pytest.approx(1300.0, abs=0.01), (
        "FIFO should give +1300; avg-cost with max_size bug would give +812.50"
    )
    assert t.net_pnl == pytest.approx(1300.0, abs=0.01)
