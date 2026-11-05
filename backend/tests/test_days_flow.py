"""Days flow: the day journal endpoints and the trade review queue (reviewed_at).

All fixtures are synthetic (the same made-up orders as test_desk_api).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

from app.db.connection import init_db
from app.db.training_store import create_debrief, finish_debrief
from app.ingest.imports import import_fills
from app.ingest.query import get_trade, list_trades, set_reviewed
from app.models.trades import Fill, Side
from app.models.training import DebriefCreate, DebriefFinish

from .test_desk_api import client, rules_file  # noqa: F401  (pytest fixtures)

D = "2026-10-13"  # two synthetic positions
UTC = timezone.utc


# ------------------------------------------------------------- day journal API

def test_fields_missing_is_null(client):
    r = client.get(f"/api/days/{D}/fields")
    assert r.status_code == 200
    assert r.json() is None


def test_fields_put_is_partial(client):
    r = client.put(f"/api/days/{D}/fields", json={"notes": "waited for the level"})
    assert r.status_code == 200
    body = r.json()
    assert body["notes"] == "waited for the level"
    assert body["why"] == "" and body["how_felt"] == ""
    assert body["updated_at"].endswith("Z")

    r = client.put(f"/api/days/{D}/fields", json={"how_felt": "calm", "stop_hit": True})
    assert r.status_code == 200
    got = client.get(f"/api/days/{D}/fields").json()
    assert got["notes"] == "waited for the level"  # untouched by the second PUT
    assert got["how_felt"] == "calm" and got["stop_hit"] is True

    # an explicit null clears a nullable field
    client.put(f"/api/days/{D}/fields", json={"stop_hit": None})
    assert client.get(f"/api/days/{D}/fields").json()["stop_hit"] is None

    # the day detail carries the same row
    assert client.get(f"/api/days/{D}").json()["fields"]["how_felt"] == "calm"


def test_fields_put_validates(client):
    assert client.put(f"/api/days/{D}/fields", json={"mood": "x"}).status_code == 422
    assert client.put(f"/api/days/{D}/fields", json={"why": 5}).status_code == 422
    assert client.put(f"/api/days/{D}/fields", json={"why": None}).status_code == 422
    assert client.put(f"/api/days/{D}/fields", json={"callers": "one"}).status_code == 422
    assert client.put(f"/api/days/{D}/fields", json={}).status_code == 422
    assert client.put("/api/days/not-a-date/fields", json={"notes": "x"}).status_code == 422
    # nothing was written by the rejected calls
    assert client.get(f"/api/days/{D}/fields").json() is None


def test_fields_on_a_day_without_trades(client):
    r = client.put("/api/days/2026-10-14/fields", json={"why": "sat out, no setup"})
    assert r.status_code == 200
    assert client.get("/api/days/2026-10-14/fields").json()["why"] == "sat out, no setup"


# ------------------------------------------------------------- review queue API

def _unreviewed(client, day: str) -> int:
    return next(d for d in client.get("/api/days").json()["days"] if d["date"] == day)["unreviewed"]


def test_reviewed_toggle(client):
    a, b = client.get(f"/api/days/{D}").json()["positions"]
    assert a["reviewed_at"] is None and b["reviewed_at"] is None
    assert _unreviewed(client, D) == 2
    assert client.get(f"/api/trades/{a['trade_id']}").json()["reviewed_at"] is None

    r = client.put(f"/api/trades/{a['trade_id']}/reviewed", json={"reviewed": True})
    assert r.status_code == 200
    stamped = r.json()["reviewed_at"]
    assert stamped and stamped.endswith("Z")
    assert _unreviewed(client, D) == 1
    pa = client.get(f"/api/days/{D}").json()["positions"][0]
    assert pa["trade_id"] == a["trade_id"] and pa["reviewed_at"] == stamped

    # marking again keeps the first timestamp
    again = client.put(f"/api/trades/{a['trade_id']}/reviewed", json={"reviewed": True}).json()
    assert again["reviewed_at"] == stamped

    cleared = client.put(f"/api/trades/{a['trade_id']}/reviewed", json={"reviewed": False}).json()
    assert cleared["reviewed_at"] is None
    assert _unreviewed(client, D) == 2


def test_reviewed_validation(client):
    assert client.put("/api/trades/999999/reviewed", json={"reviewed": True}).status_code == 404
    tid = client.get(f"/api/days/{D}").json()["positions"][0]["trade_id"]
    assert client.put(f"/api/trades/{tid}/reviewed", json={}).status_code == 422


# ------------------------------------------------------------- store level

class _Provider:
    def __init__(self, fills):
        self._fills = fills

    def get_fills(self, since=None):
        return list(self._fills)


def _fills():
    t0 = datetime(2026, 6, 10, 13, 30, tzinfo=UTC)
    mk = lambda i, side, px, m: Fill(  # noqa: E731
        provider_fill_id=f"syn-{i}", account="TEST", symbol="AAA", side=side, qty=10,
        price=px, ts=t0 + timedelta(minutes=m), fees=0.0, simulated=False,
    )
    return [mk(1, Side.buy, 10.0, 0), mk(2, Side.sell, 11.0, 5)]


def test_reviewed_at_survives_full_reimport(temp_engine):
    fills = _fills()
    import_fills(_Provider(fills), engine=temp_engine)
    (t,) = list_trades(simulated=False, engine=temp_engine)
    stamped = set_reviewed(t.id, True, engine=temp_engine).reviewed_at
    assert stamped is not None

    import_fills(_Provider(fills), engine=temp_engine, full=True)
    (after,) = list_trades(simulated=False, engine=temp_engine)
    assert after.id == t.id
    assert after.reviewed_at == stamped


def test_finishing_a_debrief_marks_the_trade_reviewed(temp_engine):
    import_fills(_Provider(_fills()), engine=temp_engine)
    (t,) = list_trades(simulated=False, engine=temp_engine)
    assert t.reviewed_at is None

    d = create_debrief(DebriefCreate(trade_id=t.id), llm_backend="none", engine=temp_engine)
    finish_debrief(d.id, DebriefFinish(), engine=temp_engine)
    first = get_trade(t.id, engine=temp_engine).reviewed_at
    assert first is not None

    # a second debrief keeps the first review time
    d2 = create_debrief(DebriefCreate(trade_id=t.id), llm_backend="none", engine=temp_engine)
    finish_debrief(d2.id, DebriefFinish(), engine=temp_engine)
    assert get_trade(t.id, engine=temp_engine).reviewed_at == first


def test_reviewed_at_column_added_to_an_old_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, "
            "direction TEXT NOT NULL, open_ts_utc DATETIME NOT NULL, close_ts_utc DATETIME, "
            "session_date DATE NOT NULL, qty FLOAT NOT NULL, avg_entry FLOAT NOT NULL, "
            "avg_exit FLOAT, gross_pnl FLOAT, fees FLOAT NOT NULL, net_pnl FLOAT, pnl_pct FLOAT, "
            "stop_price FLOAT, r_multiple FLOAT, hold_time_s INTEGER, status TEXT NOT NULL, "
            "simulated BOOLEAN NOT NULL)"
        ))
        conn.execute(text(
            "INSERT INTO trades (id, symbol, direction, open_ts_utc, session_date, qty, avg_entry, "
            "fees, status, simulated) VALUES (7, 'AAA', 'long', '2026-06-10 13:30:00', "
            "'2026-06-10', 1, 1.0, 0, 'open', 1)"
        ))
    init_db(engine, force=True)
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(trades)"))}
    assert "reviewed_at" in cols
    assert get_trade(7, engine=engine).reviewed_at is None
    engine.dispose()
