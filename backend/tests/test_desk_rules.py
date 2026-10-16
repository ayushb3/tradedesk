"""Desk 1.1: rules.yaml loader, validator, round-trip, DST, account size, GET /api/rules."""
from __future__ import annotations

import json
import shutil
from datetime import date

import pytest
import yaml
from fastapi.testclient import TestClient

from app.desk.rules import (
    EXAMPLE_PATH,
    account_size,
    dump_rules,
    load_rules,
    validate,
    window_bounds,
)

EXAMPLE = EXAMPLE_PATH.read_text(encoding="utf-8")


def _mutated(**changes) -> str:
    data = yaml.safe_load(EXAMPLE)
    data.update(changes)
    return yaml.safe_dump(data, sort_keys=False)


def test_example_is_valid_and_has_nine_rules():
    assert validate(EXAMPLE) == []
    rules = load_rules(EXAMPLE_PATH)
    assert [r.number for r in rules.rules] == list(range(1, 10))
    assert rules.lessons == {}
    assert rules.window.start == "06:30" and rules.window.end == "08:30"


def test_validate_rejects_bad_files():
    data = yaml.safe_load(EXAMPLE)
    del data["daily_stop"]
    assert any("daily_stop" in e for e in validate(yaml.safe_dump(data)))

    bad_window = _mutated(window={"start": "08:30", "end": "06:30"})
    assert any("not after start" in e for e in validate(bad_window))

    data = yaml.safe_load(EXAMPLE)
    data["rules"].append({"number": 3, "text": "dup"})
    assert any("duplicate rule number 3" in e for e in validate(yaml.safe_dump(data)))

    data = yaml.safe_load(EXAMPLE)
    data["rules"].append({"number": 0, "text": "x"})
    assert any("unknown rule number 0" in e for e in validate(yaml.safe_dump(data)))

    data = yaml.safe_load(EXAMPLE)
    data["lessons"] = {"rule_42": [{"lesson_id": "L1", "reason": "r"}]}
    assert any("unknown rule number 42" in e for e in validate(yaml.safe_dump(data)))

    assert any("negative" in e for e in validate(_mutated(daily_stop=100)))
    assert any("positive" in e for e in validate(_mutated(daily_target=-5)))
    assert validate("- just a list") != []
    assert validate("a: [unclosed") != []


def test_round_trip(tmp_path):
    rules = load_rules(EXAMPLE_PATH)
    text = dump_rules(rules)
    assert validate(text) == []
    p = tmp_path / "r.yaml"
    p.write_text(text, encoding="utf-8")
    assert load_rules(p) == rules


def test_first_load_copies_example(tmp_path):
    p = tmp_path / "data" / "rules.yaml"
    load_rules(p)
    assert p.read_text(encoding="utf-8") == EXAMPLE


@pytest.mark.parametrize(
    "day,offset_hours",
    [
        (date(2026, 11, 2), 8),  # after fall-back: PST
        (date(2026, 3, 9), 7),   # after spring-forward: PDT
        (date(2026, 10, 30), 7),  # before fall-back: PDT
        (date(2026, 3, 6), 8),   # before spring-forward: PST
    ],
)
def test_window_bounds_dst(day, offset_hours):
    rules = load_rules(EXAMPLE_PATH)
    start, end = window_bounds(rules, day)
    assert start.tzinfo is not None and start < end
    assert (start.hour, start.minute) == (6, 30) and (end.hour, end.minute) == (8, 30)
    assert start.utcoffset().total_seconds() == -offset_hours * 3600


def test_stop_change_reflected_after_reload(tmp_path):
    p = tmp_path / "r.yaml"
    shutil.copyfile(EXAMPLE_PATH, p)
    assert load_rules(p).daily_stop == -100
    p.write_text(_mutated(daily_stop=-250), encoding="utf-8")
    assert load_rules(p).daily_stop == -250


def test_account_size_ignores_ids(tmp_path):
    rules = load_rules(EXAMPLE_PATH)
    assert account_size(rules, base_dir=tmp_path) == rules.account_size.fallback
    out = tmp_path / "rh-export" / "out"
    out.mkdir(parents=True)
    (out / "account.json").write_text(
        json.dumps({"account_number": "ACCT-SECRET-123", "id": 99999, "equity": 4321.5})
    )
    assert account_size(rules, base_dir=tmp_path) == 4321.5
    (out / "account.json").write_text(json.dumps({"account_number": 123456789, "id": 5}))
    assert account_size(rules, base_dir=tmp_path) == rules.account_size.fallback


def test_get_rules_endpoint(tmp_path, monkeypatch):
    p = tmp_path / "rules.yaml"
    shutil.copyfile(EXAMPLE_PATH, p)
    monkeypatch.setenv("TRADEDESK_RULES_PATH", str(p))
    from app.main import create_app

    r = TestClient(create_app()).get("/api/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["tz"] == "America/Los_Angeles"
    assert body["daily_stop"] == -100
    assert len(body["rules"]) == 9
    assert "account_number" not in json.dumps(body)
