"""Desk 1.2: Fill Ledger analysis on a synthetic fixture. Every number is hand-computed.

Rules used: daily_stop -100 (so stop = 100), default_risk_pct 30, window end 08:30 PT,
account size 5000. Prices are per share; contracts are x100.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

from app.desk.ledger import (
    DayInput, Fill, Position, StopUsage, across_days, analyze_day, break_even_win_pct, rr,
)
from app.desk.rules import AccountSize, LossLine, Rules

FIXTURE = Path(__file__).parent / "fixtures" / "desk" / "days.json"
ACCOUNT = 5000.0
approx = pytest.approx


def make_rules() -> Rules:
    return Rules(
        daily_stop=-100, daily_target=200, default_risk_pct=30,
        loss_line=LossLine(amount=1000, start_date=date(2026, 1, 1)),
        account_size=AccountSize(fallback=ACCOUNT),
    )


def load_days() -> list[DayInput]:
    out = []
    for d in json.loads(FIXTURE.read_text(encoding="utf-8"))["days"]:
        ps = []
        for p in d["positions"]:
            fills = [
                Fill(f["side"], f["qty"], f["px"], f["cash"], datetime.fromisoformat(f["ts"]), f["otype"])
                for f in p["fills"]
            ]
            ps.append(Position(
                fills=fills, status=p["status"], swing=p["swing"], ticker=p["ticker"],
                type=p["type"], strike=p["strike"], expiry=p["expiry"], seq=p["seq"],
            ))
        out.append(DayInput(
            date=date.fromisoformat(d["date"]), positions=ps,
            stops=StopUsage(**d["stops"]) if "stops" in d else StopUsage(),
            max_loss=d["max_loss"],
        ))
    return out


@pytest.fixture(scope="module")
def results():
    rules = make_rules()
    return [analyze_day(d, rules, ACCOUNT) for d in load_days()]


def texts(a):
    return [t.text for t in a.tags]


# ---------------------------------------------------------------- day A

def test_added_lower_held_loser_and_fill_rows(results):
    a = results[0].positions[0]
    # cost 200+160 = 360, proceeds 180+100 = 280 -> -80
    assert a.cost == approx(360) and a.pnl == approx(-80)
    assert a.avg_in == approx(0.90) and a.avg_out == approx(0.70)
    assert a.add_lower == 1 and a.add_higher == 0 and a.add_same == 0
    assert a.held_loser and not a.gave_back and not a.runner_paid
    assert texts(a) == ["added lower", "held loser down", "3.6x stop at risk"]
    assert [t.kind for t in a.tags] == ["r", "r", "r"]
    # no written max loss: risk = 360 * 30% = 108
    assert a.risk == approx(108) and a.r == approx(-80 / 108)
    rows = a.fills
    assert rows[0].avg_in_after == approx(1.00) and rows[1].avg_in_after == approx(0.90)
    assert rows[2].pnl == approx(0) and rows[2].pnl_pct == approx(0)
    assert rows[3].pnl == approx(-80) and rows[3].pnl_pct == approx(-80 / 180 * 100)
    assert rows[0].pnl is None and rows[2].avg_in_after is None


def test_scaled_in_runner_paid(results):
    a = results[0].positions[1]
    assert a.pnl == approx(100) and a.add_same == 1 and a.runner_paid
    assert texts(a) == ["scaled in x2", "runner paid", "2.0x stop at risk"]
    assert [t.kind for t in a.tags] == ["a", "g", "w"]
    assert a.r == approx(100 / 60)
    assert a.best_gap == approx(60)  # (1.80 - 1.50) * 2 * 100
    assert [r.pnl for r in a.fills if r.side == "S"] == approx([20, 80])
    assert [r.pnl_pct for r in a.fills if r.side == "S"] == approx([20, 80])


def test_gave_back_no_stop_tag_at_exactly_one_x(results):
    a = results[0].positions[2]
    assert a.cost == approx(100) and a.risk_x == approx(1.0)
    assert a.pnl == approx(40) and a.gave_back and not a.runner_paid
    assert texts(a) == ["gave back"]
    assert a.r == approx(40 / 30)


def test_day_a_stats(results):
    s = results[0].stats
    assert s.net == approx(60) and s.entries == 5 and s.positions == 3
    assert s.win_pct == approx(200 / 3)
    assert s.avg_win == approx(70) and s.avg_loss == approx(-80)
    assert s.rr == approx(0.875) and s.break_even_win_pct == approx(100 / 1.875)
    assert s.max_risk == approx(360) and s.max_risk_x_stop == approx(3.6)
    assert s.max_risk_pct_account == approx(7.2)
    assert (s.add_lower, s.held_loser, s.gave_back, s.runner_paid) == (1, 1, 1, 1)
    assert s.deployed == approx(660) and s.late_fills == 0
    assert s.stops == StopUsage(1, 1, 1, 0)
    assert s.last_fill == datetime.fromisoformat("2026-03-10T08:00:00-07:00")


# ---------------------------------------------------------------- day B

def test_added_higher_written_max_loss(results):
    a = results[1].positions[0]
    assert a.cost == approx(250) and a.pnl == approx(-10) and a.add_higher == 1
    assert texts(a) == ["added higher", "2.5x stop at risk"]
    assert a.risk == approx(50) and a.r == approx(-0.2)  # written max loss wins
    rows = a.fills
    assert rows[1].avg_in_after == approx(1.25)
    assert rows[2].pnl == approx(-10) and rows[2].pnl_pct == approx(-4)


def test_late_fill_and_r_one(results):
    a = results[1].positions[1]
    assert a.pnl == approx(50) and a.r == approx(1.0) and a.tags == []
    assert results[1].stats.late_fills == 1  # the 08:45 PT sell


def test_open_and_overnight(results):
    o = results[1].positions[2]
    assert o.closed is False and o.pnl is None and o.r is None
    assert texts(o) == ["held overnight", "still open"]
    g = results[1].positions[3]
    assert g.pnl == approx(30) and texts(g) == ["held overnight"]
    assert g.r == approx(30 / 50)


def test_day_b_stats(results):
    s = results[1].stats
    assert s.net == approx(70) and s.entries == 5 and s.positions == 4
    assert s.win_pct == approx(200 / 3) and s.n_closed == 3
    assert s.avg_win == approx(40) and s.avg_loss == approx(-10)
    assert s.rr == approx(4) and s.break_even_win_pct == approx(20)
    assert s.max_risk == approx(250) and s.max_risk_x_stop == approx(2.5)
    assert s.max_risk_pct_account == approx(5.0)
    assert s.deployed == approx(500)
    # next-day sell at 09:00 PT is after the window but not on this day: not late
    assert s.late_fills == 1
    assert s.stops == StopUsage(3, 1, 2, 1)


# ---------------------------------------------------------------- day C (PST)

def test_window_edge_in_winter_time(results):
    s = results[2].stats
    # 08:30:00 PST exactly is on time; 08:31 and 08:32 are late
    assert s.late_fills == 2
    assert s.net == approx(-5) and s.rr == approx(0.5)
    assert s.break_even_win_pct == approx(100 / 1.5)
    assert s.stops == StopUsage()
    assert results[2].positions[0].r == approx(5 / 30)


# ---------------------------------------------------------------- across days

def test_across_days(results):
    x = across_days(results)
    assert x.days == 3 and x.net == approx(125)
    assert (x.green_days, x.red_days) == (2, 1)
    assert x.green_sum == approx(130) and x.red_sum == approx(-5)
    assert x.n_closed == 8 and x.n_wins == 5 and x.n_losses == 3
    assert x.win_pct == approx(62.5)
    assert x.avg_win == approx(45) and x.avg_loss == approx(-100 / 3)
    assert x.rr == approx(1.35) and x.break_even_win_pct == approx(100 / 2.35)
    assert x.tag_counts == {
        "added_lower": 1, "held_loser": 1, "stop_risk": 3, "scaled_in": 1,
        "runner_paid": 1, "gave_back": 1, "added_higher": 1, "overnight": 2, "still_open": 1,
    }
    assert x.late_days == [date(2026, 3, 11), date(2026, 1, 12)] and x.late_fills == 3
    assert x.stops == StopUsage(4, 2, 3, 1)


# ---------------------------------------------------------------- helpers/edges

def test_rr_helpers():
    assert rr(10, -5) == 2 and rr(None, -5) is None and rr(10, None) is None and rr(10, 0) is None
    assert break_even_win_pct(1) == 50 and break_even_win_pct(None) is None


def test_empty_day_and_no_account():
    d = DayInput(date=date(2026, 3, 9), positions=[])
    r = analyze_day(d, make_rules())
    s = r.stats
    assert s.net == 0 and s.win_pct is None and s.rr is None and s.max_risk == 0
    assert s.max_risk_pct_account is None and s.last_fill is None
    assert across_days([]).win_pct is None


def test_fixture_has_no_ids_or_account_fields():
    text = FIXTURE.read_text(encoding="utf-8")
    keys = set(re.findall(r'"([^"]+)"\s*:', text))
    banned = {
        k for k in keys
        if k.lower() in {"account", "account_number", "id", "url", "order_id", "href"}
        or k.lower().endswith(("_id", "_url"))
    }
    assert banned == set()
