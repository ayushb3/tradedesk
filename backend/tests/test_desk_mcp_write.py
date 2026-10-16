"""Desk MCP write tools (issue #12): whitelists, path guard, audit rows, app visibility."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from app.db.connection import get_engine, init_db
from app.desk_mcp import mock, tools, writes

FORBIDDEN_KEYS = {"account", "account_number", "order_id", "provider_fill_id"}


@pytest.fixture()
def ctx(tmp_path):
    path = tmp_path / "mock.db"
    mock.build_mock_db(path)
    return tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())


@pytest.fixture()
def empty_ctx(tmp_path):
    path = tmp_path / "empty.db"
    engine = get_engine(str(path))
    init_db(engine)
    return tools.Ctx("mock", str(path), engine, mock.mock_rules())


@pytest.fixture()
def export(tmp_path, monkeypatch):
    """An export dir holding a synthetic raw-option-orders file."""
    d = tmp_path / "rh-export" / "out"
    d.mkdir(parents=True)
    f = d / "raw-option-orders-2026-10-01.json"
    f.write_text(json.dumps(mock.synthetic_orders()), encoding="utf-8")
    monkeypatch.setenv(writes.EXPORT_DIR_ENV, str(d))
    return d, f


def _last(ctx, tool=None):
    rows = [r for r in writes.audit_rows(ctx) if tool is None or r["tool"] == tool]
    return rows[0]


def _first_trade_id(ctx, date="2026-10-09"):
    return tools.get_day(ctx, date)["positions"][0]["trade_id"]


def _walk_strings(node):
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in FORBIDDEN_KEYS
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)
    elif isinstance(node, str):
        yield node


# ----------------------------------------------------------------- import

def test_import_happy_path_counts_only_and_audited(empty_ctx, export):
    _, f = export
    out = writes.import_robinhood(empty_ctx, str(f))
    assert out["fills_added"] == 10 and out["trades"] == 5 and out["days_touched"] == 4
    assert out["tz"] == "America/Los_Angeles"
    for s in _walk_strings(out):
        assert not re.fullmatch(r"m\d-[bs]", s) and not s.startswith("rh-")
    row = _last(empty_ctx, "import_robinhood")
    assert row["result"] == "ok" and row["mode"] == "mock" and row["ts_pt"].endswith(" PT")
    # relative names resolve inside the export dir
    again = writes.import_robinhood(empty_ctx, f.name)
    assert again["fills_added"] == 0


def test_import_rematches_plans_for_touched_days(empty_ctx, export):
    _, f = export
    plan = writes.add_plan(empty_ctx, "mentor-a", "ZORP 25C 10/8", 200, 60,
                           session_date="2026-10-08")
    assert plan["matched"] is False
    writes.import_robinhood(empty_ctx, str(f))
    assert tools.get_day(empty_ctx, "2026-10-08")["plans"][0]["matched_trade_id"] is not None


def _reject(ctx, path, match):
    with pytest.raises(ValueError, match=match):
        writes.import_robinhood(ctx, path)
    row = _last(ctx, "import_robinhood")
    assert row["result"].startswith("error:")


def test_import_rejects_dotdot_escape(ctx, export):
    d, _ = export
    outside = d.parent / "raw-option-orders-evil.json"
    outside.write_text("[]", encoding="utf-8")
    _reject(ctx, str(d / ".." / outside.name), "outside")
    _reject(ctx, f"..{os.sep}{outside.name}", "outside")


def test_import_rejects_absolute_outside(ctx, export, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    f = other / "raw-option-orders-x.json"
    f.write_text(json.dumps(mock.synthetic_orders()), encoding="utf-8")
    _reject(ctx, str(f), "outside")


def test_import_rejects_symlink_pointing_out(ctx, export, tmp_path):
    d, _ = export
    target = tmp_path / "secret-raw-option-orders-1.json"
    target.write_text(json.dumps(mock.synthetic_orders()), encoding="utf-8")
    link = d / "raw-option-orders-link.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not creatable here")
    _reject(ctx, str(link), "outside")


def test_import_rejects_junction_pointing_out(ctx, export, tmp_path):
    if sys.platform != "win32":
        pytest.skip("junctions are Windows only")
    d, _ = export
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "raw-option-orders-j.json").write_text(
        json.dumps(mock.synthetic_orders()), encoding="utf-8")
    junction = d / "jct"
    res = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                         capture_output=True)
    if res.returncode != 0:
        pytest.skip("junction not creatable")
    _reject(ctx, str(junction / "raw-option-orders-j.json"), "outside")


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive paths are Windows only")
def test_import_path_case_is_insensitive_on_windows(empty_ctx, export):
    _, f = export
    assert writes.import_robinhood(empty_ctx, str(f).swapcase())["fills_added"] == 10


def test_import_rejects_wrong_names_and_non_json(ctx, export):
    d, _ = export
    (d / "account.json").write_text(json.dumps({"equity": 1000}), encoding="utf-8")
    _reject(ctx, str(d / "account.json"), "only raw-option-orders")
    (d / "raw-option-orders-bad.json").write_text("not json at all", encoding="utf-8")
    _reject(ctx, str(d / "raw-option-orders-bad.json"), "not a valid")
    (d / "raw-option-orders-1.txt").write_text("[]", encoding="utf-8")
    _reject(ctx, str(d / "raw-option-orders-1.txt"), "only raw-option-orders")
    _reject(ctx, str(d), "outside")
    _reject(ctx, "raw-option-orders-missing.json", "no such file")


def test_import_leaves_account_json_alone(empty_ctx, export):
    d, f = export
    (d / "account.json").write_text("{not even json", encoding="utf-8")
    assert writes.import_robinhood(empty_ctx, str(f))["fills_added"] == 10


def test_default_export_dir_is_sibling_of_repo(monkeypatch):
    monkeypatch.delenv(writes.EXPORT_DIR_ENV, raising=False)
    d = writes.export_dir()
    assert d.parts[-2:] == ("rh-export", "out")
    assert d.parent.parent == writes.REPO_ROOT.parent


# ---------------------------------------------------------------- add_plan

def test_add_plan_happy_and_matched(ctx):
    out = writes.add_plan(ctx, "mentor-b", "ACME 13C 10/13", 150, 50, target="2x",
                          note="n", session_date="2026-10-13")
    assert out["plan"]["created_pt"].endswith(" PT") and out["plan"]["symbol"] == "ACME"
    assert out["matched"] is True  # m5 ACME 13C traded that day
    solo = writes.add_plan(ctx, "mentor-b", "QQQ 500P", 100, 40, session_date="2026-10-15")
    assert solo["matched"] is False
    row = _last(ctx, "add_plan")
    assert row["result"] == "ok" and row["args"]["contract"] == "QQQ 500P"


@pytest.mark.parametrize("kwargs", [
    {"contract": "nonsense"}, {"size_usd": 0}, {"max_loss_usd": 500},
    {"caller": "  "}, {"session_date": "10/13"},
])
def test_add_plan_rejections_are_audited(ctx, kwargs):
    base = dict(caller="mentor-b", contract="ACME 13C", size_usd=150, max_loss_usd=50)
    before = len(writes.audit_rows(ctx))
    with pytest.raises(ValueError):
        writes.add_plan(ctx, **{**base, **kwargs})
    rows = writes.audit_rows(ctx)
    assert len(rows) == before + 1 and rows[0]["result"].startswith("error:")


# ---------------------------------------------------------- annotate_trade

def test_annotate_trade_writes_and_shows_in_http_app(isolated_db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    mock.build_mock_db(isolated_db)
    c = tools.Ctx("mock", str(isolated_db), get_engine(str(isolated_db)), mock.mock_rules())
    tid = _first_trade_id(c)
    out = writes.annotate_trade(c, tid, caller="mentor-c", notes="held too long",
                                grade="B", tags=["late-exit"])
    assert out["written"] == ["caller", "notes", "grade", "tags"]
    assert out["tags"] == ["caller:mentor-c", "late-exit"]

    r = TestClient(create_app()).get(f"/api/trades/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert body["enrichment"]["notes"] == "held too long" and body["enrichment"]["grade"] == "B"
    assert {t["name"] for t in body["tags"]} == {"caller:mentor-c", "late-exit"}

    row = _last(c, "annotate_trade")
    assert row["result"] == "ok" and row["args"]["trade_id"] == tid
    assert row["args"]["caller"] == "mentor-c"



def test_annotate_replaces_caller_and_keeps_other_tags(ctx):
    tid = _first_trade_id(ctx)
    writes.annotate_trade(ctx, tid, caller="one", tags=["a-tag"])
    out = writes.annotate_trade(ctx, tid, caller="two")
    assert out["tags"] == ["a-tag", "caller:two"]


@pytest.mark.parametrize("kwargs, match", [
    ({"stop_price": 1.0}, "not writable"),
    ({"tag_ids": [1]}, "not writable"),
    ({"grade": "Z"}, "grade"),
    ({"tags": ["caller:x"]}, "caller field"),
    ({"tags": "notalist"}, "list"),
    ({"tags": ["bad/tag"]}, "tag"),
    ({"caller": "../x"}, "caller"),
    ({}, "nothing to write"),
])
def test_annotate_rejections_are_audited(ctx, kwargs, match):
    tid = _first_trade_id(ctx)
    with pytest.raises(ValueError, match=match):
        writes.annotate_trade(ctx, tid, **kwargs)
    assert _last(ctx, "annotate_trade")["result"].startswith("error:")


def test_annotate_unknown_trade_rejected(ctx):
    with pytest.raises(ValueError, match="no trade"):
        writes.annotate_trade(ctx, 999999, notes="x")
    assert _last(ctx)["result"].startswith("error:")


# --------------------------------------------------------------- day_fields

def test_day_fields_happy_and_whitelist(ctx):
    out = writes.day_fields(ctx, "2026-10-13", why="followed the plan", stop_hit=False,
                            callers=["mentor-a"])
    assert out["day_fields"]["why"] == "followed the plan"
    assert _last(ctx, "day_fields")["result"] == "ok"
    for bad in ({"pnl": 5}, {}, {"stop_hit": "yes"}):
        with pytest.raises(ValueError):
            writes.day_fields(ctx, "2026-10-13", **bad)
        assert _last(ctx, "day_fields")["result"].startswith("error:")
    with pytest.raises(ValueError):
        writes.day_fields(ctx, "bad-date", why="x")


# -------------------------------------------------------------------- audit

def test_audit_scrubs_id_shaped_values(ctx):
    tid = _first_trade_id(ctx)
    writes.annotate_trade(ctx, tid, notes="order 123456789012 and rh-abc123def and "
                          "5f0c1b2e-1111-2222-3333-444455556666 seen")
    args = _last(ctx, "annotate_trade")["args"]
    assert "123456789012" not in args["notes"] and "rh-abc" not in args["notes"]
    assert "5f0c1b2e" not in args["notes"] and "[redacted-id]" in args["notes"]


def test_stdio_server_exposes_write_tools_and_audits(tmp_path):
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "TRADEDESK_MODE": "mock", "TEMP": str(tmp_path), "TMP": str(tmp_path),
           "TMPDIR": str(tmp_path)}
    params = StdioServerParameters(
        command=sys.executable, args=[os.path.join(backend, "scripts", "desk_mcp.py")],
        env=env, cwd=backend,
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                good = await session.call_tool(
                    "day_fields", {"date": "2026-10-13", "fields": {"why": "ok"}})
                bad = await session.call_tool(
                    "day_fields", {"date": "2026-10-13", "fields": {"pnl": 1}})
                return good, bad

    good, bad = asyncio.run(asyncio.wait_for(run(), timeout=90))
    assert not good.isError and bad.isError
    path = tmp_path / mock.MOCK_DB_NAME
    c = tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())
    results = [r["result"] for r in writes.audit_rows(c) if r["tool"] == "day_fields"]
    assert any(r == "ok" for r in results) and any(r.startswith("error:") for r in results)
