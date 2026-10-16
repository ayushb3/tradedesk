"""Desk 5.1: pattern -> lesson map. Made-up ids and reasons only; no lesson text."""
from __future__ import annotations

from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from app.db.connection import get_engine, init_db
from app.db.schema import knowledge_lessons
from app.desk import lessons as L
from app.desk.rules import EXAMPLE_PATH, LessonLink, Rules, load_rules, validate
from app.desk_mcp import mock, tools

EXAMPLE = EXAMPLE_PATH.read_text(encoding="utf-8")


def _rules(**lessons) -> Rules:
    base = yaml.safe_load(EXAMPLE)
    base["lessons"] = {
        k: [{"lesson_id": i, "reason": f"reason for {i}"} for i in ids]
        for k, ids in lessons.items()
    }
    return Rules.model_validate(base)


def _engine(tmp_path, rows=()):
    eng = get_engine(str(tmp_path / "kb.db"))
    init_db(eng)
    with eng.begin() as conn:
        for pk, module, seq in rows:
            conn.execute(knowledge_lessons.insert().values(
                id=pk, module=module, seq=seq, title="t", source_path="s", raw_text="x",
                content_hash=f"h{pk}", chunk_count=0, ingested_at=datetime(2026, 1, 1),
            ))
    return eng


def _yaml_with(lessons) -> str:
    data = yaml.safe_load(EXAMPLE)
    data["lessons"] = lessons
    return yaml.safe_dump(data, sort_keys=False)


# ------------------------------------------------------------------ validation

def test_example_lessons_valid_and_use_made_up_ids():
    assert validate(EXAMPLE) == []
    rules = load_rules(EXAMPLE_PATH)
    ids = {l.lesson_id for links in rules.lessons.values() for l in links}
    assert ids and all(i.startswith("ex-") for i in ids)


def test_unknown_pattern_key_rejected():
    errs = validate(_yaml_with({"not_a_pattern": [{"lesson_id": "ex-1", "reason": "r"}]}))
    assert any("unknown pattern not_a_pattern" in e for e in errs)


@pytest.mark.parametrize("key", sorted(L.ALLOWED_PATTERNS) + ["rule_3"])
def test_known_pattern_keys_accepted(key):
    assert validate(_yaml_with({key: [{"lesson_id": "ex-1", "reason": "r"}]})) == []


# ------------------------------------------------------------------ resolution

def test_empty_knowledge_base_means_nothing_available(tmp_path):
    eng = _engine(tmp_path)
    out = L.lessons_for(["added_lower"], _rules(added_lower=["ex-1"]), eng)
    assert out == [{
        "pattern": "added_lower", "lesson_id": "ex-1", "reason": "reason for ex-1",
        "href": None, "available": False,
    }]
    assert L.check_lessons(_rules(added_lower=["ex-1"]), eng) == []  # not ingested yet


def test_ids_resolve_against_ingested_rows_plain_and_module_style(tmp_path):
    eng = _engine(tmp_path, rows=[(7, "3", 2), (8, "12.1", 4)])
    rules = _rules(added_lower=["7", "3.2"], gave_back=["12.1.4", "99"])
    out = L.lessons_for(["added_lower", "gave_back"], rules, eng)
    by_id = {x["lesson_id"]: x for x in out}
    assert by_id["7"]["href"] == "/knowledge/lessons/7" and by_id["7"]["available"]
    assert by_id["3.2"]["href"] == "/knowledge/lessons/7"
    assert by_id["12.1.4"]["href"] == "/knowledge/lessons/8"
    assert by_id["99"]["href"] is None and by_id["99"]["available"] is False
    assert L.check_lessons(rules, eng) == ["lessons.gave_back: unknown lesson id 99"]


def test_order_by_count_dedupe_and_aliases(tmp_path):
    eng = _engine(tmp_path)
    rules = _rules(held_loser=["ex-1"], stop_at_risk=["ex-2", "ex-1"], added_lower=["ex-1"])
    out = L.lessons_for(["held_loser", "stop_risk", "stop_risk", "gave_back"], rules, eng)
    assert [(x["pattern"], x["lesson_id"]) for x in out] == [
        ("stop_risk", "ex-2"), ("stop_risk", "ex-1"), ("held_loser", "ex-1"),
    ]
    assert L.lessons_for([], rules, eng) == []
    assert L.lessons_for({"held_loser": 0}, rules, eng) == []


def test_lesson_link_model_unchanged():
    assert LessonLink(lesson_id="ex-1").reason == ""


# --------------------------------------------------------- HTTP and MCP output

@pytest.fixture()
def mock_env(tmp_path, monkeypatch, isolated_db):
    from app.desk.rules import dump_rules
    from app.main import create_app

    mock.build_mock_db(isolated_db)
    base = mock.mock_rules()
    mapped = {code: [LessonLink(lesson_id="ex-1", reason="made-up reason")]
              for code in sorted(L.PATTERN_KEYS)}
    rules = base.model_copy(update={"lessons": mapped})
    p = tmp_path / "rules.yaml"
    p.write_text(dump_rules(rules), encoding="utf-8")
    monkeypatch.setenv("TRADEDESK_RULES_PATH", str(p))
    ctx = tools.Ctx("mock", str(isolated_db), get_engine(str(isolated_db)), rules)
    return TestClient(create_app()), ctx


def test_http_and_mcp_day_json_have_lessons(mock_env):
    client, ctx = mock_env
    days = client.get("/api/days").json()["days"]
    http = mcp = None
    for d in days:
        http = client.get(f"/api/days/{d['date']}").json()
        if http["lessons"]:
            mcp = tools.get_day(ctx, d["date"])
            break
    assert http is not None and http["lessons"], "mock days should carry at least one tag"
    assert mcp is not None and mcp["lessons"] == http["lessons"]
    first = http["lessons"][0]
    assert set(first) == {"pattern", "lesson_id", "reason", "href", "available"}
    assert first["lesson_id"] == "ex-1" and first["available"] is False and first["href"] is None


def test_patterns_has_recurring_lessons(mock_env):
    client, ctx = mock_env
    http = client.get("/api/patterns").json()
    assert "lessons" in http
    for x in http["lessons"]:
        assert x["days"] >= 2 and x["positions"] >= x["days"]
    assert tools.patterns(ctx)["lessons"] == http["lessons"]
