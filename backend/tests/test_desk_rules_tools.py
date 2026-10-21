"""Desk rules tools (issue #26): propose/update, backup, audit, guards, annotations."""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from app.db.connection import get_engine
from app.desk.rules import EXAMPLE_PATH
from app.desk_mcp import mock, rules_tools, tools, writes
from app.desk_mcp.server import create_server

PT = ZoneInfo("America/Los_Angeles")
AFTER = datetime(2026, 10, 14, 12, 0, tzinfo=PT)      # Wednesday, after the window
IN_WINDOW = datetime(2026, 10, 14, 7, 0, tzinfo=PT)   # Wednesday, inside it
SATURDAY = datetime(2026, 10, 17, 7, 0, tzinfo=PT)
EXAMPLE = EXAMPLE_PATH.read_text(encoding="utf-8")


def _edit(**changes) -> str:
    data = yaml.safe_load(EXAMPLE)
    data.update(changes)
    return yaml.safe_dump(data, sort_keys=False)


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    # Mock-mode rules file lives under the temp dir; point it at tmp_path.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    db = tmp_path / "mock.db"
    mock.build_mock_db(db)
    c = tools.Ctx("mock", str(db), get_engine(str(db)), mock.mock_rules())
    rules_tools.target_path(c)  # seed the temp copy from the example
    return c


def _file(tmp_path) -> Path:
    return tmp_path / rules_tools.MOCK_RULES_NAME


def test_propose_valid_returns_diff_and_summary_and_writes_nothing(ctx, tmp_path):
    out = rules_tools.propose_rules(ctx, _edit(daily_stop=-300), now=AFTER)
    assert out["valid"] and out["errors"] == []
    assert "daily_stop -100 -> -300" in out["summary"]
    assert "-daily_stop: -100" in out["diff"] and "+daily_stop: -300" in out["diff"]
    assert "PT" in out["diff"].splitlines()[1]
    assert _file(tmp_path).read_text(encoding="utf-8") == EXAMPLE


def test_propose_summarises_rule_changes(ctx):
    data = yaml.safe_load(EXAMPLE)
    data["rules"] = data["rules"][1:] + [{"number": 10, "text": "new"}]
    data["rules"][0]["text"] = "changed"
    out = rules_tools.propose_rules(ctx, yaml.safe_dump(data, sort_keys=False), now=AFTER)
    assert {"rule 1 removed", "rule 10 added", "rule 2 edited"} <= set(out["summary"])


@pytest.mark.parametrize("bad,needle", [
    ({"daily_stop": 50}, "daily_stop must be negative"),
    ({"daily_target": -5}, "daily_target must be positive"),
    ({"window": {"start": "08:30", "end": "06:30"}}, "not after start"),
    ({"daily_stop": -5000}, "bigger than the whole loss line"),
    ({"loss_line": {"amount": 0, "start_date": "2026-01-01"}}, "loss_line.amount"),
])
def test_propose_reports_errors(ctx, bad, needle):
    out = rules_tools.propose_rules(ctx, _edit(**bad), now=AFTER)
    assert not out["valid"] and any(needle in e for e in out["errors"])


def test_update_rejects_invalid_and_unconfirmed(ctx, tmp_path):
    with pytest.raises(ValueError, match="invalid"):
        rules_tools.update_rules(ctx, _edit(daily_stop=50), confirm=True, now=AFTER)
    with pytest.raises(ValueError, match="confirm"):
        rules_tools.update_rules(ctx, _edit(daily_stop=-300), confirm=False, now=AFTER)
    assert _file(tmp_path).read_text(encoding="utf-8") == EXAMPLE
    assert not list(tmp_path.glob("rules.backup-*"))
    rows = [r for r in writes.audit_rows(ctx) if r["tool"] == "update_rules"]
    assert len(rows) == 2 and all(r["result"].startswith("error") for r in rows)


def test_update_writes_backup_audit_and_reloads(ctx, tmp_path):
    new = _edit(daily_stop=-300)
    out = rules_tools.update_rules(ctx, new, confirm=True, now=AFTER)
    assert out["written"] and out["backup"] == "rules.backup-20261014-120000.yaml"
    assert (tmp_path / out["backup"]).read_text(encoding="utf-8") == EXAMPLE
    assert _file(tmp_path).read_text(encoding="utf-8") == new
    assert ctx.rules.daily_stop == -300
    row = writes.audit_rows(ctx)[0]
    assert row["tool"] == "update_rules" and row["result"] == "ok"
    assert row["args"]["yaml_sha256"] == hashlib.sha256(new.encode()).hexdigest()[:16]
    assert "daily_stop -100 -> -300" in row["args"]["changed"]
    assert "rules:" not in str(row["args"]) and "Rule 1" not in str(row["args"])
    assert not list(tmp_path.glob(".rules-*"))


def test_stop_change_shows_in_get_day_without_code_change(ctx):
    before = tools.get_day(ctx, "2026-10-12")["positions"][0]["stop_multiple"]
    rules_tools.update_rules(ctx, _edit(daily_stop=-200), confirm=True, now=AFTER)
    after = tools.get_day(ctx, "2026-10-12")["positions"][0]["stop_multiple"]
    assert before != after and after == pytest.approx(before / 2)


def test_real_mode_uses_env_path_only(tmp_path, monkeypatch):
    p = tmp_path / "rules.yaml"
    p.write_text(EXAMPLE, encoding="utf-8")
    monkeypatch.setenv("TRADEDESK_RULES_PATH", str(p))
    db = tmp_path / "x.db"
    mock.build_mock_db(db)
    c = tools.Ctx("real", str(db), get_engine(str(db)), mock.mock_rules())
    rules_tools.update_rules(c, _edit(daily_stop=-250), confirm=True, now=AFTER)
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["daily_stop"] == -250
    assert len(list(tmp_path.glob("rules.backup-*.yaml"))) == 1


def test_guard_refuses_in_window_but_not_weekend(ctx):
    for fn in (
        lambda: rules_tools.propose_rules(ctx, EXAMPLE, now=IN_WINDOW),
        lambda: rules_tools.update_rules(ctx, EXAMPLE, confirm=True, now=IN_WINDOW),
    ):
        with pytest.raises(ValueError, match="trading window"):
            fn()
    assert rules_tools.propose_rules(ctx, EXAMPLE, now=SATURDAY)["valid"]
    assert writes.audit_rows(ctx)[0]["tool"] == "propose_rules"


def test_guard_refuses_when_line_hit(ctx, tmp_path):
    ctx.rules.loss_line.amount = 1.0  # mock data has a big loser: line is hit
    for fn in (
        lambda: rules_tools.propose_rules(ctx, EXAMPLE, now=AFTER),
        lambda: rules_tools.update_rules(ctx, EXAMPLE, confirm=True, now=AFTER),
    ):
        with pytest.raises(ValueError, match="loss line"):
            fn()
    assert _file(tmp_path).read_text(encoding="utf-8") == EXAMPLE


def test_tool_annotations_over_inprocess_server(ctx):
    mcp = create_server(ctx)
    listed = asyncio.run(mcp.list_tools())
    ro = {t.name: t.annotations.readOnlyHint for t in listed}
    reads = {"status", "rules", "list_days", "get_day", "get_trade", "patterns",
             "line_status", "propose_rules", "push_phone_snapshot", "search_course", "get_topic",
             "get_sheet", "lessons_for", "course_status"}
    writes_ = {"import_robinhood", "add_plan", "annotate_trade", "day_fields", "update_rules"}
    assert set(ro) == reads | writes_
    assert all(ro[n] is True for n in reads) and all(ro[n] is False for n in writes_)
