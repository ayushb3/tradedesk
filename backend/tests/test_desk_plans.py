"""Plan cards (issue #9). Fixtures are synthetic."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.connection import SCHEMA_VERSION
from app.db.schema import plans as plans_t
from app.desk.plans import add_plan, is_late, list_plans, mark_late, parse_contract
from app.ingest.imports import import_fills
from app.models.trades import Fill, Side

D = date(2026, 10, 14)
T0 = datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)  # 06:30 PT


def test_schema_version():
    assert SCHEMA_VERSION == 11


# ── parsing ──────────────────────────────────────────────────────────────── #
def test_parse_put_defaults_to_session_date():
    c = parse_contract("SPY 580P", D)
    assert (c.symbol, c.option_type, c.strike, c.expiry) == ("SPY", "PUT", 580.0, D)


def test_parse_call_lowercase_and_decimal():
    c = parse_contract(" qqq 497.5c ", D)
    assert (c.symbol, c.option_type, c.strike) == ("QQQ", "CALL", 497.5)


def test_parse_explicit_expiry_formats():
    assert parse_contract("SPY 580P 10/17", D).expiry == date(2026, 10, 17)
    assert parse_contract("SPY 580P 10/17/27", D).expiry == date(2027, 10, 17)
    assert parse_contract("SPY 580P 2026-10-21", D).expiry == date(2026, 10, 21)
    assert parse_contract("SPY 580P 1/2", date(2026, 12, 30)).expiry == date(2027, 1, 2)


@pytest.mark.parametrize(
    "bad", ["", "SPY", "SPY 580", "580P", "SPY 580X", "SPY 0P", "SPY 580P 13/40", "SPY 580P soon"]
)
def test_parse_bad_input(bad):
    with pytest.raises(ValueError):
        parse_contract(bad, D)


# ── validation ───────────────────────────────────────────────────────────── #
def _add(engine, **kw):
    args = dict(caller="mentor", contract="SPY 580P", size_usd=500, max_loss_usd=100,
                session_date=D, created_at=T0, engine=engine)
    args.update(kw)
    return add_plan(**args)


def test_add_plan_defaults(temp_engine):
    p = _add(temp_engine, target="+30%", note="breakdown")
    assert p.id == 1 and p.status == "open" and p.late is False
    assert (p.symbol, p.option_type, p.strike, p.expiry) == ("SPY", "PUT", 580.0, D)
    assert p.created_pt == "06:30 PT" and p.matched_trade_id is None


@pytest.mark.parametrize(
    "kw",
    [
        dict(caller=""), dict(caller="   "), dict(size_usd=0), dict(size_usd=-5),
        dict(max_loss_usd=0), dict(max_loss_usd=-1), dict(max_loss_usd=600),
        dict(size_usd="big"), dict(contract="nonsense"),
    ],
)
def test_add_plan_rejects_invalid(temp_engine, kw):
    with pytest.raises(ValueError):
        _add(temp_engine, **kw)
    assert list_plans(D, temp_engine) == []


def test_max_loss_equal_to_size_allowed(temp_engine):
    assert _add(temp_engine, size_usd=200, max_loss_usd=200).max_loss_usd == 200


# ── late helper ──────────────────────────────────────────────────────────── #
def test_is_late(temp_engine):
    p = _add(temp_engine)  # created 13:30Z
    assert is_late(p, T0 - timedelta(minutes=1)) is True
    assert is_late(p, T0) is False  # same instant: written first
    assert is_late(p, T0 + timedelta(minutes=5)) is False
    assert is_late(p, (T0 + timedelta(minutes=5)).replace(tzinfo=None)) is False  # naive = UTC


def test_mark_late_persists(temp_engine):
    early = _add(temp_engine)
    late = _add(temp_engine, created_at=T0 + timedelta(minutes=10))
    fill_ts = T0 + timedelta(minutes=5)
    assert mark_late(early.id, fill_ts, temp_engine).late is False
    assert mark_late(late.id, fill_ts, temp_engine).late is True
    assert [p.late for p in list_plans(D, temp_engine)] == [False, True]
    with pytest.raises(ValueError):
        mark_late(999, fill_ts, temp_engine)


# ── API ──────────────────────────────────────────────────────────────────── #
@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def test_post_get_round_trip(client):
    r = client.post("/api/plans", json={
        "caller": "mentor", "contract": "SPY 580P", "size_usd": 500, "max_loss_usd": 100,
        "target": "+30%", "note": "n", "session_date": "2026-10-14",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created_at"].endswith("Z")
    assert body["created_pt"].endswith(" PT")
    assert body["expiry"] == "2026-10-14" and body["option_type"] == "PUT"
    assert body["status"] == "open" and body["late"] is False

    got = client.get("/api/plans", params={"date": "2026-10-14"})
    assert got.status_code == 200
    assert [p["id"] for p in got.json()] == [body["id"]]
    assert client.get("/api/plans", params={"date": "2026-10-15"}).json() == []


def test_post_invalid_is_422(client):
    r = client.post("/api/plans", json={
        "caller": "mentor", "contract": "SPY 580P", "size_usd": 100, "max_loss_usd": 500,
    })
    assert r.status_code == 422
    r = client.post("/api/plans", json={
        "caller": "mentor", "contract": "garbage", "size_usd": 100, "max_loss_usd": 50,
    })
    assert r.status_code == 422


def test_get_defaults_to_today(client):
    r = client.post("/api/plans", json={
        "caller": "mentor", "contract": "SPY 580C", "size_usd": 100, "max_loss_usd": 50,
    })
    assert r.status_code == 200
    assert [p["id"] for p in client.get("/api/plans").json()] == [r.json()["id"]]


# ── survives re-import ───────────────────────────────────────────────────── #
class Static:
    def __init__(self, fills):
        self.fills = fills

    def get_fills(self, since=None):
        return list(self.fills)


def test_plans_survive_full_reimport(temp_engine):
    fills = [
        Fill(provider_fill_id="syn-AAA-1", account="TEST", symbol="AAA", side=Side.buy,
             qty=10, price=1.0, ts=T0, fees=0.0, simulated=False),
        Fill(provider_fill_id="syn-AAA-2", account="TEST", symbol="AAA", side=Side.sell,
             qty=10, price=2.0, ts=T0 + timedelta(minutes=5), fees=0.0, simulated=False),
    ]
    import_fills(Static(fills), temp_engine)
    p = _add(temp_engine)
    import_fills(Static(fills), temp_engine, full=True)
    import_fills(Static(fills), temp_engine)
    after = list_plans(D, temp_engine)
    assert [(x.id, x.caller, x.symbol) for x in after] == [(p.id, "mentor", "SPY")]
    with temp_engine.connect() as c:
        assert c.execute(select(plans_t)).all()
