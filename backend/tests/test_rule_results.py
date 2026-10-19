"""Redesign 6: rule results + the one R definition, on synthetic fixtures.

Rules: daily_stop -100, window end 08:30 PT, default_risk_pct 30. Contracts are x100.
Times are UTC (PT = UTC-7 in October): 13:30Z = 06:30 PT, 15:30Z = 08:30 PT.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.connection import get_engine
from app.desk import ledger, rule_results as rr
from app.desk.ledger import Fill, Position
from app.desk.rules import AccountSize, LossLine, Rules
from app.desk_mcp import mock, tools, writes
from app.models.plans import Plan

UTC = timezone.utc
DAY = date(2026, 10, 14)
approx = pytest.approx


def rules() -> Rules:
    return Rules(
        daily_stop=-100, daily_target=200, default_risk_pct=30,
        loss_line=LossLine(amount=1000, start_date=date(2026, 1, 1)),
        account_size=AccountSize(fallback=5000),
    )


def at(hh: int, mm: int = 0) -> datetime:  # UTC clock
    return datetime(2026, 10, 14, hh, mm, tzinfo=UTC)


def pos(open_at: datetime, buy_px: float, sell_px: float | None, qty: int = 1, hold_min=5,
        plan_max_loss=None, plan_late=False) -> Position:
    fills = [Fill("B", qty, buy_px, -(buy_px * qty * 100), open_at)]
    status = "open"
    if sell_px is not None:
        fills.append(Fill("S", qty, sell_px, sell_px * qty * 100, open_at + timedelta(minutes=hold_min)))
        status = "closed"
    return Position(fills=fills, status=status, plan_max_loss=plan_max_loss, plan_late=plan_late)


def plan(size=300.0, max_loss=60.0, late=False, pid=1) -> Plan:
    return Plan(id=pid, created_at=at(13, 0), created_pt="06:00 PT", session_date=DAY,
                caller="c", symbol="ZZZ", size_usd=size, max_loss_usd=max_loss, late=late)


def ti(tid, p, plan_=None, src=None, max_loss=None) -> rr.TradeInput:
    return rr.TradeInput(tid, ledger.analyze_position(p, rules(), max_loss), plan_, src)


def res(day, code, tid=None):
    src = day.results if tid is None else day.trades[tid].results
    return next(r for r in src if r.code == code)


# ------------------------------------------------------------ one R definition

def test_resolve_r_written_and_assumed():
    assert ledger.resolve_r(-30.0, 200.0, 60.0, 30) == (approx(-0.5), 60.0, "written")
    r, risk, basis = ledger.resolve_r(30.0, 200.0, None, 30)
    assert (r, risk, basis) == (approx(0.5), approx(60.0), "assumed")


def test_resolve_r_open_position_has_no_r_but_has_risk():
    r, risk, basis = ledger.resolve_r(None, 200.0, None, 30)
    assert r is None and risk == approx(60.0) and basis == "assumed"


def test_analysis_carries_r_basis_and_late_plan_is_assumed():
    w = ledger.analyze_position(pos(at(13, 40), 2.0, 2.3, plan_max_loss=50), rules())
    assert w.r_basis == "written" and w.risk == 50 and w.r == approx(30 / 50)
    late = ledger.analyze_position(
        pos(at(13, 40), 2.0, 2.3, plan_max_loss=50, plan_late=True), rules())
    assert late.r_basis == "assumed" and late.risk == approx(60) and late.r == approx(0.5)
    day = ledger.analyze_position(pos(at(13, 40), 2.0, 2.3), rules(), max_loss=40)
    assert day.r_basis == "written" and day.risk == 40


# ------------------------------------------------------------------ per trade

def test_before_cutoff_kept_and_broken():
    d = rr.evaluate_day(DAY, [
        ti(1, pos(at(15, 29), 1.0, 1.1)),   # 08:29 PT
        ti(2, pos(at(15, 31), 1.0, 1.1)),   # 08:31 PT
    ], rules())
    assert res(d, "before_cutoff", 1).kept is True
    broken = res(d, "before_cutoff", 2)
    assert broken.kept is False and "08:31:00 PT" in broken.detail and "08:30 PT" in broken.detail
    day = res(d, "before_cutoff")
    assert day.kept is False and "1 of 2" in day.detail


def test_max_loss_written_kept_broken_late_and_day_level():
    d = rr.evaluate_day(DAY, [
        ti(1, pos(at(13, 40), 1.0, 1.1, plan_max_loss=60), plan()),
        ti(2, pos(at(13, 50), 1.0, 1.1)),
        ti(3, pos(at(14, 0), 1.0, 1.1, plan_max_loss=60, plan_late=True), plan(late=True, pid=2)),
        ti(4, pos(at(14, 10), 1.0, 1.1), max_loss=40),
    ], rules())
    assert res(d, "max_loss_written", 1).kept is True
    assert res(d, "max_loss_written", 2).kept is False
    assert "30%" in res(d, "max_loss_written", 2).detail
    late = res(d, "max_loss_written", 3)
    assert late.kept is False and "after entry" in late.detail
    assert res(d, "max_loss_written", 4).kept is True
    assert [d.trades[i].r_basis for i in (1, 2, 3, 4)] == ["written", "assumed", "assumed", "written"]
    assert res(d, "max_loss_written").kept is False


def test_call_source_kept_broken_unknown():
    d = rr.evaluate_day(DAY, [
        ti(1, pos(at(13, 40), 1.0, 1.1), src="mentor"),
        ti(2, pos(at(13, 50), 1.0, 1.1), src="own"),
        ti(3, pos(at(14, 0), 1.0, 1.1), src=None),
    ], rules())
    assert [res(d, "call_source", i).kept for i in (1, 2, 3)] == [True, False, None]
    assert res(d, "call_source").kept is False
    partly = rr.evaluate_day(DAY, [ti(1, pos(at(13, 40), 1.0, 1.1), src="mentor"),
                                   ti(2, pos(at(13, 50), 1.0, 1.1))], rules())
    assert res(partly, "call_source").kept is None
    all_mentor = rr.evaluate_day(DAY, [ti(1, pos(at(13, 40), 1.0, 1.1), src="mentor")], rules())
    assert res(all_mentor, "call_source").kept is True


def test_size_within_plan_kept_broken_unknown():
    d = rr.evaluate_day(DAY, [
        ti(1, pos(at(13, 40), 2.0, 2.1), plan(size=300)),                # $200 of $300
        ti(2, pos(at(13, 50), 2.0, 2.1, qty=2), plan(size=300, pid=2)),  # $400 of $300
        ti(3, pos(at(14, 0), 2.0, 2.1)),                                 # no plan
        ti(4, pos(at(14, 10), 2.0, 2.1), plan(size=300, late=True, pid=3)),
    ], rules())
    assert [res(d, "size_within_plan", i).kept for i in (1, 2, 3, 4)] == [True, False, None, None]
    assert res(d, "size_within_plan").kept is False
    unk = rr.evaluate_day(
        DAY, [ti(1, pos(at(13, 40), 2.0, 2.1)), ti(2, pos(at(13, 50), 2.0, 2.1), plan())], rules())
    assert res(unk, "size_within_plan").kept is None


# ---------------------------------------------------------------- daily stop

def test_daily_stop_hit_and_trades_after():
    d = rr.evaluate_day(DAY, [
        ti(1, pos(at(13, 40), 2.0, 0.5)),   # -150 realized at 13:45 -> stop (-100) hit
        ti(2, pos(at(13, 50), 1.0, 1.2)),   # opened after the hit
        ti(3, pos(at(14, 0), 1.0, 1.2)),    # opened after the hit
    ], rules())
    s = d.stop
    assert s.hit and s.hit_at == at(13, 45) and s.hit_at_pt == "06:45:00 PT"
    assert s.trades_after == 2 and s.trade_ids_after == [2, 3] and s.limit == -100
    assert res(d, "daily_stop", 1).kept is True        # the trade that crossed it
    assert res(d, "daily_stop", 2).kept is False
    assert "min after the daily stop" in res(d, "daily_stop", 2).detail
    day = res(d, "daily_stop")
    assert day.kept is False and "2 trades after" in day.detail


def test_daily_stop_hit_with_nothing_after_is_kept_and_not_hit_is_kept():
    hit = rr.evaluate_day(DAY, [ti(1, pos(at(13, 40), 2.0, 0.5))], rules())
    assert hit.stop.hit and hit.stop.trades_after == 0
    assert res(hit, "daily_stop").kept is True and "no trades after" in res(hit, "daily_stop").detail
    ok = rr.evaluate_day(DAY, [ti(1, pos(at(13, 40), 1.0, 0.95))], rules())
    assert not ok.stop.hit and res(ok, "daily_stop").kept is True


def test_daily_stop_is_on_net_not_per_trade():
    # -60, +30, -75: the running net (-105) reaches the -100 stop only on the third close.
    d = rr.evaluate_day(DAY, [
        ti(1, pos(at(13, 40), 1.0, 0.4)),
        ti(2, pos(at(13, 50), 1.0, 1.3)),
        ti(3, pos(at(14, 0), 1.0, 0.25)),
        ti(4, pos(at(14, 30), 1.0, 1.1)),
    ], rules())
    assert d.stop.hit and d.stop.hit_at == at(14, 5)
    assert d.stop.trade_ids_after == [4]


def test_open_position_does_not_count_toward_stop():
    d = rr.evaluate_day(
        DAY, [ti(1, pos(at(13, 40), 2.0, None)), ti(2, pos(at(13, 50), 1.0, 1.1))], rules())
    assert not d.stop.hit


def test_empty_day_is_unknown():
    d = rr.evaluate_day(DAY, [], rules())
    assert all(r.kept is None for r in d.results)
    assert [r.code for r in d.results] == list(rr.RULE_CODES)


# -------------------------------------------------- integration on the mock DB

@pytest.fixture()
def ctx(tmp_path):
    path = tmp_path / "mock.db"
    mock.build_mock_db(path)
    return tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())


def test_get_day_and_get_trade_surface_rule_results(ctx):
    day = tools.get_day(ctx, "2026-10-08")
    pos0 = day["positions"][0]
    assert pos0["r_basis"] == "written" and pos0["r"] is not None   # the plan wrote a max loss
    assert [r["code"] for r in pos0["rule_results"]] == list(rr.RULE_CODES)
    codes = {r["code"]: r["kept"] for r in pos0["rule_results"]}
    assert codes["max_loss_written"] is True and codes["call_source"] is None
    assert [r["code"] for r in day["rule_results"]] == list(rr.RULE_CODES)
    assert day["stop_result"]["limit"] == -100

    t = tools.get_trade(ctx, pos0["trade_id"])["trade"]
    assert t["r"] == pos0["r"] and t["rule_results"] == pos0["rule_results"]

    other = tools.get_day(ctx, "2026-10-12")["positions"][0]
    assert other["r_basis"] == "assumed"
    assert {r["code"]: r["kept"] for r in other["rule_results"]}["max_loss_written"] is False


def test_get_day_reports_stop_hit_for_big_loser(ctx):
    day = tools.get_day(ctx, "2026-10-12")
    assert day["stop_result"]["hit"] is True and day["stop_result"]["trades_after"] == 0
    assert tools.list_days(ctx)["days"][0]["rule_results"]


def test_call_source_round_trip_mcp_and_http(isolated_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    mock.build_mock_db(isolated_db)
    c = tools.Ctx("mock", str(isolated_db), get_engine(str(isolated_db)), mock.mock_rules())
    tid = tools.get_day(c, "2026-10-09")["positions"][0]["trade_id"]

    out = writes.annotate_trade(c, tid, call_source="mentor")
    assert out["written"] == ["call_source"] and out["call_source"] == "mentor"
    p = tools.get_trade(c, tid)["trade"]
    assert p["call_source"] == "mentor"
    assert {r["code"]: r["kept"] for r in p["rule_results"]}["call_source"] is True

    client = TestClient(create_app())
    assert client.get(f"/api/trades/{tid}").json()["enrichment"]["call_source"] == "mentor"
    r = client.put(f"/api/trades/{tid}/call-source", json={"call_source": "own"})
    assert r.status_code == 200 and r.json()["enrichment"]["call_source"] == "own"
    day = client.get("/api/days/2026-10-09").json()
    assert {x["code"]: x["kept"] for x in day["positions"][0]["rule_results"]}["call_source"] is False
    assert day["rule_results"][3]["kept"] is False

    assert client.put(f"/api/trades/{tid}/call-source", json={"call_source": "bogus"}).status_code == 422
    cleared = client.put(f"/api/trades/{tid}/call-source", json={"call_source": None}).json()
    assert cleared["enrichment"]["call_source"] is None
    writes.annotate_trade(c, tid, call_source="own")
    writes.annotate_trade(c, tid, call_source="clear")
    assert tools.get_trade(c, tid)["trade"]["call_source"] is None
    with pytest.raises(ValueError, match="call_source"):
        writes.annotate_trade(c, tid, call_source="mom")


def test_http_trade_list_uses_the_one_r(isolated_db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    mock.build_mock_db(isolated_db)
    client = TestClient(create_app())
    trades = client.get("/api/trades").json()
    real = [t for t in trades if not t["simulated"] and t["status"] == "closed"]
    assert real and all(t["r_multiple"] is not None for t in real)
    assert {t["r_basis"] for t in real} == {"written", "assumed"}
    assert all(len(t["rule_results"]) == 5 for t in real)
    assert client.get("/api/analytics/summary").json()["avg_r"] is not None
