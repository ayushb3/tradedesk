"""Desk API (issue #8): /days, /days/{date}, /patterns, /line over synthetic fills."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.desk import days as desk_days
from app.desk.rules import dump_rules, load_rules
from app.ingest.imports import import_fills
from app.ingest.robinhood import RobinhoodExport

UTC = timezone.utc
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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _order(oid, side, at, px, qty, *, net, direction, strike="680.0000", expiry="2026-10-13",
           trigger="immediate"):
    leg = {
        "side": side, "position_effect": "open" if side == "buy" else "close",
        "option_type": "call", "strike_price": strike, "expiration_date": expiry,
        "executions": [{"id": f"{oid}-x", "price": f"{px:.8f}", "quantity": f"{qty:.5f}",
                        "timestamp": _iso(at)}],
    }
    return {
        "id": oid, "account_number": "999999999", "chain_symbol": "SPY", "state": "filled",
        "trigger": trigger, "type": "limit",
        "stop_price": "1.0000" if trigger == "stop" else None,
        "processed_quantity": f"{qty:.5f}", "net_amount": f"{net:.2f}",
        "net_amount_direction": direction, "trade_value_multiplier": "100.0000",
        "created_at": _iso(at), "updated_at": _iso(at), "legs": [leg],
    }


def _synthetic_orders():
    d13 = datetime(2026, 10, 13, 13, 45, tzinfo=UTC)  # 06:45 PT (PDT)
    d12 = datetime(2026, 10, 12, 13, 45, tzinfo=UTC)
    d30 = datetime(2026, 9, 30, 13, 45, tzinfo=UTC)   # before the loss line start
    m = timedelta
    return [
        # 10-13 A: buy 2 @1.50 (fee 1.04), sell 2 @2.00 on a stop (fee 1.06): net +97.90
        _order("a-b", "buy", d13, 1.50, 2, net=301.04, direction="debit"),
        _order("a-s", "sell", d13 + m(minutes=10), 2.00, 2, net=398.94, direction="credit",
               trigger="stop"),
        # 10-13 B: buy 1 @2.00 (fee 1.00), sell 1 @1.50 AFTER the window (fee 1.50): net -52.50
        _order("b-b", "buy", d13 + m(minutes=20), 2.00, 1, net=201.00, direction="debit",
               strike="681.0000"),
        _order("b-s", "sell", d13 + m(hours=2, minutes=30), 1.50, 1, net=148.50,
               direction="credit", strike="681.0000"),
        # 10-12: one loser, -300.00 net (no fees)
        _order("c-b", "buy", d12, 5.00, 1, net=500.00, direction="debit", strike="670.0000",
               expiry="2026-10-12"),
        _order("c-s", "sell", d12 + m(minutes=5), 2.00, 1, net=200.00, direction="credit",
               strike="670.0000", expiry="2026-10-12"),
        # 09-30: one winner +50 net, before the line start
        _order("d-b", "buy", d30, 1.00, 1, net=100.00, direction="debit", strike="660.0000",
               expiry="2026-09-30"),
        _order("d-s", "sell", d30 + m(minutes=5), 1.50, 1, net=150.00, direction="credit",
               strike="660.0000", expiry="2026-09-30"),
    ]


@pytest.fixture()
def rules_file(tmp_path, monkeypatch):
    p = tmp_path / "rules.yaml"
    p.write_text(RULES_YAML, encoding="utf-8")
    monkeypatch.setenv("TRADEDESK_RULES_PATH", str(p))
    return p


@pytest.fixture()
def client(isolated_db, rules_file):
    from app.main import create_app

    c = TestClient(create_app())
    import_fills(RobinhoodExport(_synthetic_orders()))
    return c


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", k, None
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, None, node


# ------------------------------------------------------------------ day numbers

def test_day_numbers(client):
    d = client.get("/api/days/2026-10-13").json()
    s = d["stats"]
    assert s["net"] == pytest.approx(45.40)
    assert (s["positions"], s["entries"], s["n_closed"]) == (2, 2, 2)
    assert s["win_pct"] == pytest.approx(50.0)
    assert s["late_fills"] == 1
    assert s["max_risk"] == pytest.approx(301.04)
    assert s["stops"] == {"placed": 1, "filled": 1, "moves": 0, "pulled": 0}

    a, b = d["positions"]
    assert a["pnl"] == pytest.approx(97.90)
    assert b["pnl"] == pytest.approx(-52.50)
    # no written max loss: risk is default_risk_pct (30%) of cost
    assert a["risk"] == pytest.approx(301.04 * 0.30)
    assert a["r"] == pytest.approx(97.90 / (301.04 * 0.30))
    assert a["stop_multiple"] == pytest.approx(301.04 / 100)
    assert [f["side"] for f in a["fills"]] == ["B", "S"]
    assert a["fills"][1]["order_type"] == "stop"
    assert a["fills"][1]["pnl"] == pytest.approx(97.90)
    assert a["fills"][0]["ts"].endswith("Z") and a["fills"][0]["ts_pt"] == "06:45:00 PT"
    # ledger tags only: the market-data tags (#52) depend on bars and are tested elsewhere
    assert [t["code"] for t in a["tags"] if t["code"] not in ("held_past_stop", "exited_early")] == ["stop_risk"]
    assert a["trade_key"] and isinstance(a["trade_id"], int)
    assert d["fields"] is None
    assert d["window"]["start_pt"] == "06:30 PT" and d["window"]["end_pt"] == "08:30 PT"


def test_day_net_equals_trades_net_pnl(client):
    from sqlalchemy import func, select

    from app.db.connection import get_engine
    from app.db.schema import trades

    with get_engine().connect() as conn:
        db = dict(conn.execute(
            select(trades.c.session_date, func.sum(trades.c.net_pnl)).group_by(trades.c.session_date)
        ).all())
    listed = client.get("/api/days").json()["days"]
    assert len(listed) == 3
    for row in listed:
        assert row["net"] == pytest.approx(db[date.fromisoformat(row["date"])], abs=0.005)


def test_written_max_loss_drives_r(client):
    from app.desk.day_fields import upsert_day_fields

    upsert_day_fields("2026-10-13", max_loss_declared=100.0)
    d = client.get("/api/days/2026-10-13").json()
    assert d["positions"][0]["r"] == pytest.approx(0.979)
    assert d["fields"]["max_loss_declared"] == 100.0


def test_unknown_day_is_404(client):
    assert client.get("/api/days/2026-01-02").status_code == 404


def test_patterns(client):
    p = client.get("/api/patterns", params={"from": "2026-10-01", "to": "2026-10-31"}).json()
    assert p["days"] == 2
    assert p["net"] == pytest.approx(-254.60)
    assert (p["green_days"], p["red_days"]) == (1, 1)
    assert p["green_sum"] == pytest.approx(45.40) and p["red_sum"] == pytest.approx(-300.0)
    assert p["n_closed"] == 3 and p["n_wins"] == 1 and p["n_losses"] == 2
    assert p["win_pct"] == pytest.approx(100 / 3)
    assert p["rr"] == pytest.approx(97.90 / ((300.0 + 52.5) / 2))
    assert p["break_even_win_pct"] == pytest.approx(100 / (1 + p["rr"]))
    assert p["late_fills"] == 1 and p["late_days"] == ["2026-10-13"]
    assert p["biggest_risk"]["date"] == "2026-10-12"
    assert p["biggest_risk"]["dollars"] == pytest.approx(500.0)
    assert p["tag_counts"]["stop_risk"] >= 1


# ---------------------------------------------------------------------- the line

def test_line_pnl_since_start_counts_every_trade(client):
    ln = client.get("/api/line").json()
    # 09-30 (+50) is before the start date; 10-12 (-300) and 10-13 (+45.40) count.
    assert ln["pnl"] == pytest.approx(-254.60)
    assert ln["left"] == pytest.approx(1000 - 254.60)
    assert ln["trades_counted"] == 3 and ln["start_date"] == "2026-10-01"


def test_line_left_stops_growing_when_ahead(isolated_db, rules_file):
    import_fills(RobinhoodExport(_synthetic_orders()[:2]))  # +97.90 only
    ls = desk_days.line_status(now=datetime(2026, 10, 13, 20, 0, tzinfo=UTC))
    assert ls.pnl == pytest.approx(97.90) and ls.left == 1000.0


@pytest.mark.parametrize(
    "now_utc,state",
    [
        (datetime(2026, 10, 13, 13, 0, tzinfo=UTC), "before_window"),   # 06:00 PDT
        (datetime(2026, 10, 13, 13, 30, tzinfo=UTC), "in_window"),      # 06:30 PDT
        (datetime(2026, 10, 13, 14, 0, tzinfo=UTC), "in_window"),
        (datetime(2026, 10, 13, 15, 31, tzinfo=UTC), "closed"),         # 08:31 PDT
        # DST ended 2026-11-01: window is 14:30-16:30 UTC. 13:45Z would be in the
        # window under PDT but is 05:45 PST, still before it.
        (datetime(2026, 11, 3, 13, 45, tzinfo=UTC), "before_window"),
        (datetime(2026, 11, 3, 14, 45, tzinfo=UTC), "in_window"),
        (datetime(2026, 11, 3, 16, 45, tzinfo=UTC), "closed"),
    ],
)
def test_line_states(client, now_utc, state):
    ls = desk_days.line_status(now=now_utc)
    assert ls.state == state


def test_line_state_uses_pt_date_across_midnight_utc(client):
    # 2026-10-14 02:00Z is still 19:00 PDT on 10-13.
    ls = desk_days.line_status(now=datetime(2026, 10, 14, 2, 0, tzinfo=UTC))
    assert ls.today == date(2026, 10, 13) and ls.state == "closed"
    assert ls.today_net == pytest.approx(45.40)


def test_line_flags(client):
    on_13 = desk_days.line_status(now=datetime(2026, 10, 13, 16, 0, tzinfo=UTC))
    assert on_13.target_hit and not on_13.stop_hit and not on_13.line_hit  # 45.40 >= 40
    on_12 = desk_days.line_status(now=datetime(2026, 10, 12, 16, 0, tzinfo=UTC))
    assert on_12.stop_hit and not on_12.target_hit  # -300 <= -100


def test_line_hit_when_nothing_left(client, rules_file):
    r = load_rules()
    r.loss_line.amount = 200.0
    rules_file.write_text(dump_rules(r), encoding="utf-8")
    ln = client.get("/api/line").json()
    assert ln["left"] == pytest.approx(200 - 254.60)
    assert ln["today"]["line_hit"] is True


# --------------------------------------------------------- rules drive the numbers

def test_changing_the_stop_changes_every_stop_multiple(client, rules_file):
    before = client.get("/api/days/2026-10-13").json()
    r = load_rules()
    r.daily_stop = -200.0
    rules_file.write_text(dump_rules(r), encoding="utf-8")
    after = client.get("/api/days/2026-10-13").json()

    assert after["daily_stop"] == -200.0
    for b, a in zip(before["positions"], after["positions"]):
        assert a["stop_multiple"] == pytest.approx(b["stop_multiple"] / 2)
    assert after["stats"]["max_risk_x_stop"] == pytest.approx(before["stats"]["max_risk_x_stop"] / 2)

    summary = {d["date"]: d for d in client.get("/api/days").json()["days"]}
    assert summary["2026-10-13"]["max_risk_x_stop"] == pytest.approx(301.04 / 200)
    # 3.0x of the old stop is a stop_risk tag; at 1.5x of the new one it is a warning.
    assert before["positions"][0]["tags"][0]["text"].startswith("3.0x")
    assert after["positions"][0]["tags"][0]["text"].startswith("1.5x")


# ----------------------------------------------------------- tz / as_of / privacy

def test_tz_and_as_of_on_every_response(client):
    for url in ("/api/days", "/api/days/2026-10-13", "/api/patterns", "/api/line"):
        body = client.get(url).json()
        assert body["tz"] == "America/Los_Angeles", url
        assert body["as_of"] == "2026-10-13T16:15:00Z", url  # newest fill (the late sell)


def test_as_of_is_null_without_fills(isolated_db, rules_file):
    from app.main import create_app

    c = TestClient(create_app())
    body = c.get("/api/days").json()
    assert body["as_of"] is None and body["days"] == []


def test_all_times_are_aware_utc(client):
    for url in ("/api/days/2026-10-13", "/api/line", "/api/patterns", "/api/days"):
        for path, _, val in _walk(client.get(url).json()):
            if isinstance(val, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:.]+", val):
                pytest.fail(f"naive datetime at {url}{path}: {val}")


def test_no_account_order_or_provider_ids_leak(client):
    from app.desk.day_fields import upsert_day_fields

    upsert_day_fields("2026-10-13", why="plan", max_loss_declared=100.0)
    bad_key = re.compile(r"^account$|account_(number|id|no)|acct|order_id|provider|fill_id", re.I)
    for url in ("/api/days", "/api/days/2026-10-13", "/api/days/2026-10-12", "/api/patterns",
                "/api/line", "/api/rules"):
        body = client.get(url).json()
        for path, key, val in _walk(body):
            assert not (key and bad_key.search(key)), f"{url}{path}"
            if isinstance(val, str):
                assert not val.startswith("rh-"), f"{url}{path}"
                assert "999999999" not in val, f"{url}{path}"
        assert "rh-" not in client.get(url).text
