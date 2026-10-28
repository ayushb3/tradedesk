"""Stable trade ids across re-imports (issue #3).

`trades.id` is preserved for a trade whose opening fill is unchanged, so every
record keyed on it (enrichment, tags, debriefs, agent sessions, scenarios)
stays attached after a re-import. Fixtures are synthetic.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from app.db import agents_store
from app.db.connection import init_db
from app.db.schema import fills as fills_t
from app.db.schema import tags as tags_t
from app.db.schema import trades as trades_t
from app.db.training_store import create_debrief, create_scenario, get_debrief, get_scenario_full
from app.ingest.imports import import_fills
from app.ingest.query import get_trade, list_trades, update_trade
from app.models.training import (
    AnswerKey, AttemptActionType, DebriefCreate, ScenarioCreate, ScenarioSource,
)
from app.models.trades import Fill, Grade, Side, TradeUpdate

UTC = timezone.utc
BASE = datetime(2026, 10, 14, 13, 30, tzinfo=UTC)  # 06:30 PT


def _fill(i, symbol, side, qty, price, minute):
    return Fill(
        provider_fill_id=f"syn-{symbol}-{i:03d}", account="TEST", symbol=symbol,
        side=side, qty=qty, price=price, ts=BASE + timedelta(minutes=minute),
        fees=0.0, simulated=False,
    )


class Static:
    def __init__(self, fills):
        self.fills = fills

    def get_fills(self, since=None):
        return list(self.fills)


FILLS = [
    _fill(1, "AAA", Side.buy, 100, 10.0, 0),
    _fill(2, "AAA", Side.sell, 100, 12.0, 10),
    _fill(3, "BBB", Side.buy, 50, 20.0, 20),
    _fill(4, "BBB", Side.sell, 50, 19.0, 30),
]


def _ids(engine):
    return {t.symbol: t.id for t in list_trades(simulated=False, engine=engine)}


def _fill_count(engine):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(fills_t)).scalar()


def test_reimport_adds_no_fills_and_keeps_ids(temp_engine):
    import_fills(Static(FILLS), temp_engine)
    before, n = _ids(temp_engine), _fill_count(temp_engine)
    for full in (False, True):
        res = import_fills(Static(FILLS), temp_engine, full=full)
        assert res["trades_built"] == 2
        assert _fill_count(temp_engine) == n
        assert _ids(temp_engine) == before


def test_everything_keyed_on_trade_id_survives_reimport(temp_engine):
    import_fills(Static(FILLS), temp_engine)
    tid = _ids(temp_engine)["AAA"]

    with temp_engine.begin() as c:
        tag_id = c.execute(tags_t.insert().values(name="setup-x")).inserted_primary_key[0]
    update_trade(tid, TradeUpdate(notes="clean", grade=Grade.A, stop_price=8.0,
                                  tag_ids=[tag_id]), engine=temp_engine)
    debrief = create_debrief(DebriefCreate(trade_id=tid), "none", engine=temp_engine)
    session = agents_store.create_session("critic", f"trade:{tid}", engine=temp_engine)
    scen = create_scenario(
        ScenarioCreate(
            title="from trade", source_type=ScenarioSource.debrief, source_trade_id=tid,
            answer_key=AnswerKey(correct_actions=[list(AttemptActionType)[0]], explanation="x"),
        ),
        engine=temp_engine,
    )

    import_fills(Static(FILLS), temp_engine, full=True)
    import_fills(Static(FILLS), temp_engine)

    assert _ids(temp_engine)["AAA"] == tid
    t = get_trade(tid, engine=temp_engine)
    assert t.enrichment.notes == "clean" and t.enrichment.grade == Grade.A
    assert t.stop_price == 8.0 and [x.id for x in t.tags] == [tag_id]
    assert get_debrief(debrief.id, engine=temp_engine).trade_id == tid
    assert agents_store.get_latest_session_for_subject(
        f"trade:{tid}", "critic", engine=temp_engine)["id"] == session["id"]
    assert get_scenario_full(scen.id, engine=temp_engine).source_trade_id == tid


def test_new_fills_do_not_shift_existing_ids_and_ids_are_not_recycled(temp_engine):
    import_fills(Static(FILLS), temp_engine)
    before = _ids(temp_engine)
    top = max(before.values())

    # An earlier trade appears (would renumber under delete/reinsert) ...
    early = [_fill(9, "ZZZ", Side.buy, 10, 5.0, -60), _fill(10, "ZZZ", Side.sell, 10, 6.0, -50)]
    import_fills(Static(early + FILLS), temp_engine)
    after = _ids(temp_engine)
    assert {k: after[k] for k in before} == before
    assert after["ZZZ"] > top

    # ... and a vanished trade's id is never handed to a different trade
    # while something (here a debrief) still references it.
    gone_id = after["ZZZ"]
    create_debrief(DebriefCreate(trade_id=gone_id), "none", engine=temp_engine)  # references it
    import_fills(Static(FILLS), temp_engine, full=True)
    assert "ZZZ" not in _ids(temp_engine)
    later = [_fill(20, "CCC", Side.buy, 1, 1.0, 90), _fill(21, "CCC", Side.sell, 1, 2.0, 95)]
    import_fills(Static(FILLS + later), temp_engine)
    assert _ids(temp_engine)["CCC"] > gone_id


def test_open_trade_keeps_id_when_it_closes(temp_engine):
    opening = [FILLS[0]]
    import_fills(Static(opening), temp_engine)
    tid = _ids(temp_engine)["AAA"]
    import_fills(Static(FILLS[:2]), temp_engine)
    assert _ids(temp_engine)["AAA"] == tid


def test_trade_key_is_opaque_and_exposed(temp_engine):
    import_fills(Static(FILLS), temp_engine)
    t = get_trade(_ids(temp_engine)["AAA"], engine=temp_engine)
    assert re.fullmatch(r"tk-[0-9a-f]{20}", t.trade_key)
    assert "rh-" not in t.trade_key


def test_migration_backfills_existing_db_without_changing_ids(temp_engine):
    import_fills(Static(FILLS), temp_engine)
    before = _ids(temp_engine)
    with temp_engine.begin() as c:  # simulate a pre-#3 local DB
        c.execute(text("DROP INDEX uq_trades_trade_key"))
        c.execute(text("ALTER TABLE trades DROP COLUMN trade_key"))
    init_db(temp_engine, force=True)
    init_db(temp_engine, force=True)  # idempotent
    assert _ids(temp_engine) == before
    with temp_engine.connect() as c:
        keys = c.execute(select(trades_t.c.trade_key)).scalars().all()
    assert len(keys) == 2 and all(keys) and len(set(keys)) == 2
    import_fills(Static(FILLS), temp_engine, full=True)
    assert _ids(temp_engine) == before


# --- PT display acceptance (no browser): backend emits Z, frontend formats in PT --- #
def test_fill_at_1330_utc_serializes_z_and_displays_0630_pt(temp_engine):
    import_fills(Static(FILLS), temp_engine)
    body = get_trade(_ids(temp_engine)["AAA"], engine=temp_engine).model_dump(mode="json")
    assert body["open_ts"] == "2026-10-14T13:30:00Z"
    pt = datetime(2026, 10, 14, 13, 30, tzinfo=UTC).astimezone(ZoneInfo("America/Los_Angeles"))
    assert (pt.hour, pt.minute) == (6, 30)  # PDT, UTC-7


def test_frontend_formatter_uses_pacific_time():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/lib/format.ts").read_text(encoding="utf-8")
    assert 'DISPLAY_TZ = "America/Los_Angeles"' in src
    assert 'DISPLAY_TZ_LABEL = "PT"' in src
    assert re.search(r"toLocaleTimeString\([^)]*timeZone: DISPLAY_TZ", src, re.S)
