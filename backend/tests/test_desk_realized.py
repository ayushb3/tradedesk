"""Desk 4.4: realized (before fees), fees and net on synthetic fills. Every number is hand-computed.

Day A (2026-10-20): an index-option position (2 contracts, $0.98/contract/side) exited in two
fills, a cheap equity-option position, and one still-open buy. Day B (2026-10-21): one loser.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.connection import get_engine, init_db
from app.desk import days as desk_days
from app.desk.ledger import (
    DayInput, Fill, Position, analyze_day, analyze_position, fill_fees,
)
from app.desk_mcp import mock, snapshot, tools
from app.desk_mcp.mock import _order
from app.ingest.imports import import_fills
from app.ingest.robinhood import RobinhoodExport

UTC = timezone.utc
approx = pytest.approx
NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
DAY_A, DAY_B = date(2026, 10, 20), date(2026, 10, 21)


def _orders() -> list[dict]:
    m = timedelta
    out: list[dict] = []

    def leg(tag, day, minute, side, sym, strike, px, qty, fee):
        at = datetime(2026, 10, day, 13, 30, tzinfo=UTC) + m(minutes=minute)
        gross = px * qty * 100
        out.append(_order(
            tag, side, at, px, qty,
            net=gross + fee if side == "buy" else gross - fee,
            direction="debit" if side == "buy" else "credit",
            sym=sym, strike=strike, expiry=f"2026-10-{day:02d}",
        ))

    # index option, 2 contracts, 0.98 per contract per side
    leg("a1", 20, 10, "buy", "IDXA", 5000, 5.00, 2, 1.96)
    leg("a2", 20, 20, "sell", "IDXA", 5000, 6.00, 1, 0.98)
    leg("a3", 20, 30, "sell", "IDXA", 5000, 7.00, 1, 0.98)
    # cheap equity option
    leg("b1", 20, 40, "buy", "CHEP", 10, 0.50, 1, 0.04)
    leg("b2", 20, 50, "sell", "CHEP", 10, 0.30, 1, 0.04)
    # still open
    leg("c1", 20, 55, "buy", "OPEN", 20, 1.00, 1, 0.04)
    # day B loser
    leg("d1", 21, 10, "buy", "CHEP", 11, 2.00, 1, 0.05)
    leg("d2", 21, 20, "sell", "CHEP", 11, 1.00, 1, 0.05)
    return out


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    path = tmp_path_factory.mktemp("realized") / "r.db"
    engine = get_engine(str(path))
    init_db(engine)
    import_fills(RobinhoodExport(_orders()), engine, full=True)
    rules = mock.mock_rules()
    rules.loss_line.start_date = DAY_A
    return tools.Ctx("mock", str(path), engine, rules)


def _pos(day, ticker):
    return next(p for p in day["positions"] if p["ticker"] == ticker)


# ------------------------------------------------------------------ ledger

def test_fill_fees_formula_clamp_and_rounding():
    ts = datetime(2026, 10, 20, 14, tzinfo=UTC)
    assert fill_fees(Fill("B", 2, 5.0, -1001.96, ts)) == approx(1.96)
    assert fill_fees(Fill("S", 1, 6.0, 599.02, ts)) == approx(0.98)
    assert fill_fees(Fill("S", 1, 6.0, 600.0, ts)) == 0.0
    assert fill_fees(Fill("S", 1, 6.0, 601.0, ts)) == 0.0  # cash above gross: clamped, never negative
    assert fill_fees(Fill("B", 3, 0.33, -99.1234567, ts)) == approx(0.12)  # rounded to cents


def test_position_per_fill_and_totals(ctx):
    day = tools.get_day(ctx, "2026-10-20")
    idx = _pos(day, "IDXA")
    f0, f1, f2 = idx["fills"]
    assert (f0["fees"], f0["realized_before_fees"], f0["pnl"]) == (approx(1.96), None, None)
    assert (f1["fees"], f1["realized_before_fees"], f1["pnl"]) == (
        approx(0.98), approx(100.00), approx(98.04))
    assert (f2["fees"], f2["realized_before_fees"], f2["pnl"]) == (
        approx(0.98), approx(200.00), approx(198.04))
    assert idx["fees"] == approx(3.92)
    assert idx["realized_before_fees"] == approx(300.00)
    assert idx["pnl"] == approx(296.08)
    assert idx["realized_before_fees"] - idx["fees"] == approx(idx["pnl"])

    chp = _pos(day, "CHEP")
    assert (chp["fees"], chp["realized_before_fees"], chp["pnl"]) == (
        approx(0.08), approx(-20.00), approx(-20.08))


def test_open_position_has_fees_but_no_realized(ctx):
    opn = _pos(tools.get_day(ctx, "2026-10-20"), "OPEN")
    assert opn["status"] == "open" and opn["pnl"] is None
    assert opn["realized_before_fees"] is None
    assert opn["fees"] == approx(0.04)


def test_day_stats_count_closed_positions_only(ctx):
    s = tools.get_day(ctx, "2026-10-20")["stats"]
    assert s["net"] == approx(276.00)
    assert s["realized_before_fees"] == approx(280.00)
    assert s["fees"] == approx(4.00)  # the open buy's 0.04 is not in it
    assert s["realized_before_fees"] - s["fees"] == approx(s["net"])
    b = tools.get_day(ctx, "2026-10-21")["stats"]
    assert (b["net"], b["realized_before_fees"], b["fees"]) == (
        approx(-100.10), approx(-100.00), approx(0.10))


def test_list_days_carry_the_fields(ctx):
    rows = {r["date"]: r for r in tools.list_days(ctx)["days"]}
    a = rows["2026-10-20"]
    assert (a["net"], a["realized_before_fees"], a["fees"]) == (
        approx(276.00), approx(280.00), approx(4.00))
    assert a["stats"] == tools.get_day(ctx, "2026-10-20")["stats"]


def test_patterns_range_totals(ctx):
    p = tools.patterns(ctx, "2026-10-20", "2026-10-21")
    assert p["net"] == approx(175.90)
    assert p["realized_before_fees"] == approx(180.00)
    assert p["fees"] == approx(4.10)
    assert p["realized_before_fees"] - p["fees"] == approx(p["net"])


def test_line_status_over_the_same_trades_the_line_counts(ctx):
    ls = tools.line_status(ctx)
    raw = desk_days.line_status(now=NOW, rules=ctx.rules, engine=ctx.engine)
    assert raw.pnl == approx(175.90)  # closed trades only; the open buy is not counted
    assert raw.fees == approx(4.10)
    assert raw.pnl_before_fees == approx(180.00)
    assert raw.trades_counted == 3
    assert {"fees", "pnl_before_fees"} <= set(ls)


def test_stop_and_target_checks_stay_on_net(ctx):
    rules = ctx.rules.model_copy(deep=True)
    # Day B: net -100.10, before fees -100.00. A stop of -100.05 trips on net only.
    rules.daily_stop = -100.05
    assert desk_days.line_status(now=NOW, rules=rules, engine=ctx.engine).stop_hit is True
    rules.daily_stop = -100.20
    assert desk_days.line_status(now=NOW, rules=rules, engine=ctx.engine).stop_hit is False
    # Day A: net 276.00, before fees 280.00. A target of 278 is NOT met on net.
    at_a = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    rules.daily_target = 278.0
    assert desk_days.line_status(now=at_a, rules=rules, engine=ctx.engine).target_hit is False
    rules.daily_target = 276.0
    assert desk_days.line_status(now=at_a, rules=rules, engine=ctx.engine).target_hit is True
    # The loss line is on net as well: 175.90 up, so nothing is used.
    assert desk_days.line_status(now=NOW, rules=ctx.rules, engine=ctx.engine).left == approx(
        ctx.rules.loss_line.amount)


def test_blown_max_loss_uses_net():
    ts = datetime(2026, 10, 20, 14, tzinfo=UTC)
    # gross loss 100.00 would not pass a 100.00 max loss, but the net loss 100.10 does.
    p = Position(
        fills=[Fill("B", 1, 2.0, -200.05, ts), Fill("S", 1, 1.0, 99.95, ts + timedelta(minutes=5))],
        ticker="X", type="C", strike=1.0, expiry="2026-10-20",
    )
    a = analyze_position(p, mock.mock_rules(), max_loss=100.0)
    assert a.realized_before_fees == approx(-100.00) and a.pnl == approx(-100.10)
    assert a.blown_max_loss is True


def test_day_input_ledger_identity():
    ts = datetime(2026, 10, 20, 14, tzinfo=UTC)
    p = Position(
        fills=[Fill("B", 2, 5.0, -1001.96, ts), Fill("S", 2, 6.5, 1298.04, ts + timedelta(minutes=9))],
        ticker="I", type="C", strike=1.0, expiry="2026-10-20",
    )
    r = analyze_day(DayInput(date=DAY_A, positions=[p]), mock.mock_rules())
    s = r.stats
    assert (s.net, s.fees, s.realized_before_fees) == (approx(296.08), approx(3.92), approx(300.00))


# --------------------------------------------------------------- snapshot

def test_snapshot_carries_the_new_fields(ctx):
    out = snapshot.push_phone_snapshot(ctx, date="2026-10-20", now=NOW)
    docs = {(w["collection"], w["doc_id"]): w["data"] for w in out["writes"]}
    day = docs[("snapshot_days", "2026-10-20")]
    assert day["stats"]["fees"] == approx(4.00) and day["stats"]["realized_before_fees"] == approx(280.00)
    idx = next(p for p in day["positions"] if p["ticker"] == "IDXA")
    assert idx["fees"] == approx(3.92) and idx["realized_before_fees"] == approx(300.00)
    assert [f["fees"] for f in idx["fills"]] == approx([1.96, 0.98, 0.98])
    assert idx["fills"][1]["realized_before_fees"] == approx(100.00)
    meta = docs[("snapshot", "meta")]
    assert meta["line"]["fees"] == approx(4.10)
    assert meta["line"]["pnl_before_fees"] == approx(180.00)
    week = docs[("snapshot_week", "current")]
    assert week["fees"] == approx(4.00) and week["realized_before_fees"] == approx(280.00)  # to 10-20
