"""Plan <-> trade matching (issue #10). Fixtures are synthetic."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.desk import days as desk_days
from app.desk import ledger
from app.desk.matching import match_day
from app.desk.plans import add_plan, get_plan, list_plans
from app.desk.rules import load_rules
from app.ingest.imports import import_fills
from app.models.trades import Fill, Side

D = date(2026, 10, 14)
T0 = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)  # 06:30 PT
MIN = timedelta(minutes=1)

RULES_YAML = """\
timezone: America/Los_Angeles
window: {start: "06:30", end: "08:30"}
daily_stop: -100
daily_target: 40
loss_line: {amount: 1000, start_date: 2026-10-01}
default_risk_pct: 30
account_size: {source_path: does/not/exist.json, fallback: 5000}
rules: []
lessons: {}
"""


@pytest.fixture()
def rules(tmp_path, monkeypatch):
    p = tmp_path / "rules.yaml"
    p.write_text(RULES_YAML, encoding="utf-8")
    monkeypatch.setenv("TRADEDESK_RULES_PATH", str(p))
    return load_rules()


class Static:
    def __init__(self, fills):
        self.fills = fills

    def get_fills(self, since=None):
        return list(self.fills)


def trade(tag, strike, opened, buy_px, sell_px, *, minutes_held=5):
    """One round trip on SPY <strike>C expiring D: buy then sell 1 contract."""
    common = dict(account="TEST", symbol="SPY", qty=1, multiplier=100.0, fees=0.0,
                  option_type="CALL", strike=float(strike), expiry=D.isoformat(),
                  simulated=False)
    return [
        Fill(provider_fill_id=f"syn-{tag}-b", side=Side.buy, price=buy_px, ts=opened, **common),
        Fill(provider_fill_id=f"syn-{tag}-s", side=Side.sell, price=sell_px,
             ts=opened + timedelta(minutes=minutes_held), **common),
    ]


def plan(engine, strike, *, at=T0, max_loss=100, size=500, caller="mentor"):
    return add_plan(caller=caller, contract=f"SPY {strike}C", size_usd=size,
                    max_loss_usd=max_loss, session_date=D, created_at=at, engine=engine)


def day(engine, rules):
    d = desk_days.get_day(D, rules, engine)
    assert d is not None
    return d


def test_two_plans_three_trades(temp_engine, rules):
    fills = (
        trade("a", 580, T0 + 5 * MIN, 1.00, 1.50)        # +50
        + trade("b", 585, T0 + 10 * MIN, 1.00, 0.40)     # -60
        + trade("c", 590, T0 + 15 * MIN, 1.00, 1.20)     # +20, no plan
    )
    import_fills(Static(fills), temp_engine)
    pa = plan(temp_engine, 580, max_loss=100)
    pb = plan(temp_engine, 585, max_loss=80)
    match_day(D, temp_engine, rules=rules)

    d = day(temp_engine, rules)
    a, b, c = d.result.positions
    lp_a, lp_b, lp_c = d.loaded
    assert lp_a.plan.id == pa.id and lp_b.plan.id == pb.id and lp_c.plan is None
    assert [lp.off_plan for lp in d.loaded] == [False, False, True]
    # R is on the written max loss, not default risk
    assert (a.risk, a.r) == (100, pytest.approx(0.5))
    assert (b.risk, b.r) == (80, pytest.approx(-0.75))
    assert c.risk == pytest.approx(100 * 0.30)  # off-plan: default risk
    assert d.off_plan_count == 1
    assert d.plans_not_taken == 0
    assert d.size_written_first is False  # one trade has no plan
    assert {p.status for p in d.plans} == {"matched"}


def test_planned_not_taken(temp_engine, rules):
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 1.00, 1.50)), temp_engine)
    plan(temp_engine, 580)
    skipped = plan(temp_engine, 600)
    match_day(D, temp_engine, rules=rules)
    d = day(temp_engine, rules)
    assert d.plans_not_taken == 1
    assert d.off_plan_count == 0
    assert d.size_written_first is True
    assert [p.status for p in d.plans if p.id == skipped.id] == ["unmatched"]
    assert get_plan(skipped.id, temp_engine).matched_trade_id is None


def test_plan_only_day_is_visible(temp_engine, rules):
    plan(temp_engine, 580)
    d = day(temp_engine, rules)  # past day, no trades: the plan reads as not taken
    assert (d.plans_not_taken, d.off_plan_count, d.size_written_first) == (1, 0, None)


def test_today_stays_open_until_window_ends(temp_engine, rules):
    plan(temp_engine, 580)
    in_window = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)  # 07:00 PT
    after = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)      # 09:00 PT
    match_day(D, temp_engine, rules=rules, now=in_window)
    assert list_plans(D, temp_engine)[0].status == "open"
    match_day(D, temp_engine, rules=rules, now=after)
    assert list_plans(D, temp_engine)[0].status == "unmatched"


def test_late_plan_matches_but_is_flagged(temp_engine, rules):
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 1.00, 1.50)), temp_engine)
    p = plan(temp_engine, 580, at=T0 + 8 * MIN)  # written after the opening fill
    match_day(D, temp_engine, rules=rules)
    got = get_plan(p.id, temp_engine)
    assert (got.status, got.late) == ("matched", True)
    d = day(temp_engine, rules)
    assert d.loaded[0].plan.late is True
    assert d.loaded[0].off_plan is False
    assert d.size_written_first is False


def test_nearest_before_wins(temp_engine, rules):
    import_fills(Static(trade("a", 580, T0 + 10 * MIN, 1.00, 1.50)), temp_engine)
    far = plan(temp_engine, 580, at=T0)
    near = plan(temp_engine, 580, at=T0 + 8 * MIN)
    match_day(D, temp_engine, rules=rules)
    assert get_plan(near.id, temp_engine).status == "matched"
    assert get_plan(far.id, temp_engine).status == "unmatched"


def test_each_plan_matches_one_trade_in_order(temp_engine, rules):
    fills = trade("a", 580, T0 + 10 * MIN, 1.00, 1.50) + trade("b", 580, T0 + 30 * MIN, 1.00, 0.8)
    import_fills(Static(fills), temp_engine)
    first = plan(temp_engine, 580, at=T0)
    second = plan(temp_engine, 580, at=T0 + 20 * MIN)
    match_day(D, temp_engine, rules=rules)
    d = day(temp_engine, rules)
    assert [lp.plan.id for lp in d.loaded] == [first.id, second.id]
    assert d.size_written_first is True
    # a lone plan serves only one of two trades; the other is off-plan
    for p in list_plans(D, temp_engine):
        if p.id == second.id:
            from app.db.schema import plans as plans_t

            with temp_engine.begin() as c:
                c.execute(plans_t.delete().where(plans_t.c.id == p.id))
    match_day(D, temp_engine, rules=rules)
    assert [lp.off_plan for lp in day(temp_engine, rules).loaded] == [False, True]


def test_late_plan_does_not_steal_a_proper_match(temp_engine, rules):
    fills = trade("a", 580, T0 + 10 * MIN, 1.00, 1.50) + trade("b", 580, T0 + 30 * MIN, 1.00, 0.8)
    import_fills(Static(fills), temp_engine)
    p = plan(temp_engine, 580, at=T0 + 20 * MIN)  # before trade b, after trade a
    match_day(D, temp_engine, rules=rules)
    d = day(temp_engine, rules)
    assert [lp.off_plan for lp in d.loaded] == [True, False]
    assert get_plan(p.id, temp_engine).late is False


def test_different_contract_never_matches(temp_engine, rules):
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 1.00, 1.50)), temp_engine)
    plan(temp_engine, 581)
    match_day(D, temp_engine, rules=rules)
    d = day(temp_engine, rules)
    assert d.off_plan_count == 1 and d.plans_not_taken == 1


def test_match_survives_full_reimport(temp_engine, rules):
    fills = trade("a", 580, T0 + 5 * MIN, 1.00, 1.50) + trade("b", 585, T0 + 10 * MIN, 1.00, 0.4)
    import_fills(Static(fills), temp_engine)
    plan(temp_engine, 580)
    plan(temp_engine, 600)
    match_day(D, temp_engine, rules=rules)

    def snapshot():
        d = day(temp_engine, rules)
        return (
            [(lp.trade_id, lp.plan.id if lp.plan else None, lp.off_plan) for lp in d.loaded],
            [(p.id, p.status, p.matched_trade_id, p.late) for p in d.plans],
        )

    before = snapshot()
    import_fills(Static(fills), temp_engine, full=True)
    assert snapshot() == before
    import_fills(Static(fills), temp_engine)
    assert snapshot() == before


def test_plan_added_after_import_matches_immediately(temp_engine, rules):
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 1.00, 1.50)), temp_engine)
    p = plan(temp_engine, 580)
    assert (p.status, p.matched_trade_id is not None) == ("matched", True)


def test_blown_max_loss_flag(temp_engine, rules):
    # lose 150 against a written max loss of 100
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 2.00, 0.50)), temp_engine)
    plan(temp_engine, 580, max_loss=100)
    match_day(D, temp_engine, rules=rules)
    d = day(temp_engine, rules)
    a = d.result.positions[0]
    assert a.pnl == pytest.approx(-150)
    assert a.blown_max_loss is True and a.written_max_loss == 100
    assert a.r == pytest.approx(-1.5)
    assert "blown_max_loss" in [t.code for t in a.tags]
    assert d.blown_max_loss is True


def test_loss_within_max_loss_is_not_blown(temp_engine, rules):
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 1.00, 0.20)), temp_engine)  # -80
    plan(temp_engine, 580, max_loss=100)
    match_day(D, temp_engine, rules=rules)
    d = day(temp_engine, rules)
    assert d.blown_max_loss is False and d.result.positions[0].blown_max_loss is False


# -- ledger R source ------------------------------------------------------- #
def _pos(plan_max_loss=None):
    f = [
        ledger.Fill("B", 1, 2.0, -200.0, T0),
        ledger.Fill("S", 1, 1.0, 100.0, T0 + 5 * MIN),
    ]
    return ledger.Position(fills=f, plan_max_loss=plan_max_loss)


def test_r_source_precedence(rules):
    # plan beats day beats default
    assert ledger.analyze_position(_pos(50), rules, max_loss=80).risk == 50
    assert ledger.analyze_position(_pos(None), rules, max_loss=80).risk == 80
    assert ledger.analyze_position(_pos(None), rules, None).risk == pytest.approx(60)
    # day's written max loss alone also flags a blown loss (-100 vs 80)
    assert ledger.analyze_position(_pos(None), rules, max_loss=80).blown_max_loss is True
    assert ledger.analyze_position(_pos(None), rules, None).blown_max_loss is False


def test_days_api_exposes_plan_fields(isolated_db, rules):
    from fastapi.testclient import TestClient

    from app.main import create_app

    c = TestClient(create_app())
    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 2.00, 0.50)))
    for contract in ("SPY 580C", "SPY 600C"):
        r = c.post("/api/plans", json={
            "caller": "mentor", "contract": contract, "size_usd": 500, "max_loss_usd": 100,
            "session_date": D.isoformat(),
        })
        assert r.status_code == 200, r.text
    body = c.get(f"/api/days/{D}").json()
    assert body["plans_not_taken"] == 1 and body["off_plan_count"] == 0
    assert body["blown_max_loss"] is True and body["size_written_first"] is False
    pos = body["positions"][0]
    assert pos["off_plan"] is False and pos["blown_max_loss"] is True
    assert pos["plan"]["max_loss"] == 100 and pos["plan"]["late"] is True  # created now, after fill
    assert sorted(p["status"] for p in body["plans"]) == ["matched", "unmatched"]
    summary = c.get("/api/days").json()["days"][0]
    assert summary["blown_max_loss"] is True



def test_size_written_first_falls_back_to_day_fields_without_plans(temp_engine, rules):
    """No plan cards that day: the self-reported day field is the answer."""
    from app.desk.day_fields import upsert_day_fields

    import_fills(Static(trade("a", 580, T0 + 5 * MIN, 1.00, 1.50)), temp_engine)
    assert day(temp_engine, rules).size_written_first is False  # no cards, no report
    upsert_day_fields(D, size_written_first=True, engine=temp_engine)
    assert day(temp_engine, rules).size_written_first is True
