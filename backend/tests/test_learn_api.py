"""Learn API: configured/unconfigured states, public shape, no-future-leak gate, grading.
Synthetic drills (tests/fixtures/learn) and synthetic course notes only."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import learn_synth as S

FIX = Path(__file__).parent / "fixtures"
SET = "2026-10-28"
D1, D2 = "2026-10-28-spy-levels-1", "2026-10-28-spy-patience-2"
SECRET_KEYS = {"answer_key", "grading_rubric", "explanation", "bars_until", "detector"}


class FakeProvider:
    def get_candles(self, symbol, day, timeframe="1m"):
        return S.resistance_session(day, True, symbol)


def _walk_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_keys(v)


def _make_client(monkeypatch, tmp_path, *, learn=True, notes=True):
    from app.config import get_settings
    import app.db.connection as conn
    from app.api.deps import provider_dep
    from app.main import create_app

    monkeypatch.setenv("TRADEDESK_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("KNOWLEDGE_EMBEDDER", "hashing")
    if learn:
        shutil.copytree(FIX / "learn", tmp_path / "learn")
        monkeypatch.setenv("TRADEDESK_LEARN_DIR", str(tmp_path / "learn"))
    else:
        monkeypatch.setenv("TRADEDESK_LEARN_DIR", "")
    if notes:
        shutil.copytree(FIX / "course_notes", tmp_path / "notes")
        monkeypatch.setenv("TRADEDESK_COURSE_NOTES_DIR", str(tmp_path / "notes"))
    else:
        monkeypatch.setenv("TRADEDESK_COURSE_NOTES_DIR", "")
    get_settings.cache_clear()
    conn._engines.clear()
    # "today" is after the fixture set, whatever the real date is
    monkeypatch.setattr("app.learn.store.today_pt", lambda now=None: date(2026, 10, 28))
    app = create_app()
    app.dependency_overrides[provider_dep] = lambda: FakeProvider()
    return TestClient(app)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path)
    yield c
    from app.config import get_settings
    import app.db.connection as conn

    get_settings.cache_clear()
    conn._engines.clear()


GOOD = {"level": 600.05, "enter": "enter", "side": "short", "stop": 600.07, "target": 597.45}


# --------------------------------------------------------------- unconfigured

def test_unconfigured_state_on_every_endpoint(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path, learn=False)
    calls = [("get", "/api/learn/today"), ("get", "/api/learn/topics"),
             ("get", "/api/learn/lessons"), ("get", f"/api/learn/drills/{D1}"),
             ("get", f"/api/learn/drills/{D1}/bars"), ("get", f"/api/learn/drills/{D1}/result"),
             ("post", f"/api/learn/drills/{D1}/answer")]
    for method, url in calls:
        r = getattr(c, method)(url, **({"json": GOOD} if method == "post" else {}))
        assert r.status_code == 200, url
        assert r.json() == {"configured": False, "message": "learn folder not configured"}


def test_learn_dir_set_but_empty_gives_no_drills(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path)
    shutil.rmtree(tmp_path / "learn" / "drills")
    r = c.get("/api/learn/today").json()
    assert r == {"configured": True, "set_date": None, "drills": [], "review_pending": False}


# ------------------------------------------------------------------- today

def test_today_lists_public_drills_without_secrets(client):
    r = client.get("/api/learn/today").json()
    assert r["configured"] is True and r["set_date"] == SET
    assert [d["id"] for d in r["drills"]] == [D1, D2]
    assert r["review_pending"] is True
    d = r["drills"][0]
    assert (d["status"], d["score"], d["review"]) == ("new", None, True)
    assert d["lessons"] == [
        {"id": "3.1.1", "title": "3.1.1 Where to put the stop", "video_url": "https://example.com/video/5"},
        {"id": "2.1", "title": "2.1 Trailing the runner", "video_url": "https://example.com/video/3"},
    ]
    assert not SECRET_KEYS & set(_walk_keys(r))


def test_today_uses_latest_set_not_after_today(client, monkeypatch):
    monkeypatch.setattr("app.learn.store.today_pt", lambda now=None: date(2026, 11, 2))
    assert client.get("/api/learn/today").json()["set_date"] == SET
    monkeypatch.setattr("app.learn.store.today_pt", lambda now=None: date(2026, 10, 20))
    r = client.get("/api/learn/today").json()
    assert r["set_date"] is None and r["drills"] == []


def test_lesson_refs_null_when_notes_not_configured(monkeypatch, tmp_path):
    c = _make_client(monkeypatch, tmp_path, notes=False)
    d = c.get("/api/learn/today").json()["drills"][0]
    assert d["lessons"] == [{"id": "3.1.1", "title": None, "video_url": None},
                            {"id": "2.1", "title": None, "video_url": None}]
    assert c.get("/api/learn/topics").json() == []
    assert c.get("/api/learn/lessons").json() == []


def test_drill_detail_and_404(client):
    d = client.get(f"/api/learn/drills/{D1}").json()
    assert d["id"] == D1 and not SECRET_KEYS & set(d)
    assert client.get("/api/learn/drills/nope").status_code == 404
    assert client.get("/api/learn/drills/..%2F..%2Fresults/bars").status_code == 404


# -------------------------------------------------------------------- bars gate

def test_bars_never_reach_pause_before_an_answer(client):
    b = client.get(f"/api/learn/drills/{D1}/bars").json()
    ts = [c["ts"] for c in b["candles"]]
    assert ts[0] == "2026-10-14T13:30:00Z"
    assert ts[-1] == "2026-10-14T14:32:00Z" and all(t < "2026-10-14T14:33:00Z" for t in ts)
    assert b["revealed_until"] == "2026-10-14T14:33:00Z"
    assert (b["ticker"], b["timeframe"]) == ("SPY", "1m")
    assert set(b["candles"][0]) >= {"symbol", "ts", "open", "high", "low", "close", "volume"}
    # the answer to a different drill does not unlock this one
    client.post(f"/api/learn/drills/{D2}/answer", json={"level": 600.0, "enter": "wait"})
    assert client.get(f"/api/learn/drills/{D1}/bars").json()["revealed_until"] == "2026-10-14T14:33:00Z"


def test_bars_extend_to_bars_until_once_answered(client):
    assert client.post(f"/api/learn/drills/{D1}/answer", json=GOOD).status_code == 200
    b = client.get(f"/api/learn/drills/{D1}/bars").json()
    ts = [c["ts"] for c in b["candles"]]
    assert ts[-1] == "2026-10-14T16:02:00Z" and len(ts) == 153
    assert b["revealed_until"] == "2026-10-14T16:02:00Z"


# ----------------------------------------------------------------------- answer

def test_answer_grades_and_returns_key_and_explanation(client, tmp_path):
    r = client.post(f"/api/learn/drills/{D1}/answer", json=GOOD)
    assert r.status_code == 200
    g = r.json()
    assert g["drill_id"] == D1 and g["score"] == 100
    assert [s["step"] for s in g["steps"]] == ["level", "enter", "stop", "target"]
    assert g["answer_key"]["stop"] == 600.07
    assert g["explanation"].startswith("Synthetic")
    assert g["rule_refs"]["stop"] == "3.1.1 § Key ideas"
    assert [x["id"] for x in g["lessons"]] == ["3.1.1", "2.1"]
    lines = (tmp_path / "learn" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    assert set(row) == {"drill_id", "set_date", "topic", "answered_at", "answers", "grade"}
    assert row["answered_at"].endswith("Z") and row["topic"] == "stops"


def test_retake_overwrites_latest_wins(client, tmp_path):
    client.post(f"/api/learn/drills/{D1}/answer", json={**GOOD, "stop": 598.0})  # wrong side
    first = client.get("/api/learn/today").json()["drills"][0]
    assert first["status"] == "done" and first["score"] < 100
    client.post(f"/api/learn/drills/{D1}/answer", json=GOOD)
    d = client.get(f"/api/learn/drills/{D1}").json()
    assert (d["status"], d["score"]) == ("done", 100)
    assert client.get(f"/api/learn/drills/{D1}/result").json()["score"] == 100
    lines = (tmp_path / "learn" / "results.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_wait_key_is_graded_on_level_and_enter_only(client):
    g = client.post(f"/api/learn/drills/{D2}/answer",
                    json={"level": 600.05, "enter": "wait"}).json()
    assert g["score"] == 100
    assert [s["graded"] for s in g["steps"]] == [True, True, False, False]


def test_result_404_before_answer_and_bad_body_422(client):
    assert client.get(f"/api/learn/drills/{D1}/result").status_code == 404
    assert client.post(f"/api/learn/drills/{D1}/answer", json={"enter": "maybe"}).status_code == 422
    assert client.post("/api/learn/drills/nope/answer", json=GOOD).status_code == 404


def test_answer_key_absent_from_all_pre_answer_responses(client):
    bodies = [client.get(u).json() for u in (
        "/api/learn/today", f"/api/learn/drills/{D1}", f"/api/learn/drills/{D1}/bars",
        "/api/learn/topics", "/api/learn/lessons", "/api/learn/lessons?q=stop")]
    for b in bodies:
        assert not SECRET_KEYS & set(_walk_keys(b))
    text = json.dumps(bodies)
    assert "Synthetic explanation" not in text and "600.07" not in text


# ---------------------------------------------------------------- topics/lessons

def test_topics_order_and_merge_with_results(client):
    t = client.get("/api/learn/topics").json()
    assert [x["slug"] for x in t] == ["stops", "trade-management", "position-sizing",
                                      "premarket-routine"]
    assert all(x["drills_done"] == 0 and x["avg_score"] is None and x["last_date"] is None for x in t)
    client.post(f"/api/learn/drills/{D1}/answer", json=GOOD)
    client.post(f"/api/learn/drills/{D2}/answer", json={"level": 600.05, "enter": "enter",
                                                         "side": "long", "stop": 1, "target": 2})
    t = {x["slug"]: x for x in client.get("/api/learn/topics").json()}
    assert t["stops"]["drills_done"] == 1 and t["stops"]["avg_score"] == 100
    assert t["trade-management"]["avg_score"] == 50
    assert len(t["stops"]["last_date"]) == 10


def test_lessons_all_and_search(client):
    allx = client.get("/api/learn/lessons").json()
    assert [x["id"] for x in allx] == ["1.1", "1.2", "2.1", "2.3", "3.1.1"]
    row = next(x for x in allx if x["id"] == "3.1.1")
    assert row["video_url"] == "https://example.com/video/5" and "stops" in row["topics"]
    assert set(row) == {"id", "title", "module", "video_url", "topics"}
    hits = client.get("/api/learn/lessons?q=where to put the stop").json()
    assert hits and hits[0]["id"] == "3.1.1"
