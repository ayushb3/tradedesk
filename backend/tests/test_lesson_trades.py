"""Your trades tagged with a lesson's setup (app.knowledge.lesson_trades + its route).

Synthetic lessons, tags and trades in a throwaway DB.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.knowledge.lesson_trades import matching_tags, tag_matches


def test_tag_words_must_all_appear_in_the_title_or_concepts():
    assert tag_matches("VWAP-reclaim", "Reclaiming VWAP after a flush")
    assert tag_matches("EMA-pullback", "Pullbacks to the 8 EMA")
    assert not tag_matches("EMA-pullback", "Pullbacks to a level")
    assert tag_matches("PDH-break", "Breaks of the prior day high")
    assert tag_matches("OR-breakout", "Opening range breakouts")
    assert not tag_matches("OR-breakout", "Breakout or breakdown")      # bare "or" is not OR
    assert not tag_matches("", "anything")


def test_matching_tags_reads_concept_names_too():
    names = ["PDH-break", "VWAP-reclaim", "EMA-pullback"]
    assert matching_tags(names, "Lesson title", ["VWAP reclaim", "Trend"]) == ["VWAP-reclaim"]


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app

    return TestClient(create_app())


def _seed(engine):
    from app.db.schema import tags, trade_tags, trades
    from app.knowledge import store as kstore

    body = "synthetic body"
    kstore.upsert_lesson(lesson_id=7, module="2", seq=1, title="Reclaiming VWAP",
                         source_path="/fake/7.txt", raw_text=body,
                         chash=kstore.content_hash(body), embedder="hashing-v1", chunk_count=1)
    kstore.upsert_lesson(lesson_id=8, module="2", seq=2, title="Unrelated lesson",
                         source_path="/fake/8.txt", raw_text=body,
                         chash=kstore.content_hash(body), embedder="hashing-v1", chunk_count=1)
    with engine.begin() as conn:
        t1 = conn.execute(tags.insert().values(name="VWAP-reclaim", active=True)).inserted_primary_key[0]
        t2 = conn.execute(tags.insert().values(name="PDH-break", active=True)).inserted_primary_key[0]

        def trade(day, hour, simulated=False, context="real"):
            return conn.execute(trades.insert().values(
                symbol="SYN", contract_label="SYN 100C", direction="long",
                open_ts_utc=datetime(day.year, day.month, day.day, hour), session_date=day,
                qty=1, avg_entry=1.0, fees=0.0, status="closed", simulated=simulated,
                context=context)).inserted_primary_key[0]

        a = trade(date(2030, 1, 2), 15)
        b = trade(date(2030, 1, 3), 15)
        sim = trade(date(2030, 1, 4), 15, simulated=True)
        other = trade(date(2030, 1, 5), 15)
        for tid, tag in ((a, t1), (b, t1), (b, t2), (sim, t1), (other, t2)):
            conn.execute(trade_tags.insert().values(trade_id=tid, tag_id=tag))
    return a, b


def test_lesson_trades_route(client):
    from app.db.connection import get_engine, init_db

    engine = get_engine()
    init_db(engine)
    a, b = _seed(engine)
    r = client.get("/api/knowledge/lessons/7/trades").json()
    assert r["tags"] == ["VWAP-reclaim"]
    assert [t["trade_id"] for t in r["trades"]] == [b, a]            # newest first, no sim
    assert r["trades"][0]["session_date"] == "2030-01-03"
    assert r["trades"][0]["tags"] == ["VWAP-reclaim"]
    empty = client.get("/api/knowledge/lessons/8/trades").json()
    assert empty == {"lesson_id": 8, "tags": [], "trades": []}
    assert client.get("/api/knowledge/lessons/999/trades").status_code == 404
