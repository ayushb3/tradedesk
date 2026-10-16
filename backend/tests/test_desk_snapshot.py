"""Phone snapshot tool (issue #18): shape, privacy walk, size cap, registration."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.db.connection import get_engine
from app.desk.rules import LessonLink
from app.desk_mcp import mock, snapshot, tools
from app.desk_mcp.server import create_server

NOW = datetime(2026, 10, 15, 15, 52, tzinfo=timezone.utc)  # 8:52 PT
FORBIDDEN_KEYS = {"trade_id", "db_path", "provider_fill_id", "account", "account_number",
                  "order", "order_id", "href", "title", "id"}


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    path = tmp_path_factory.mktemp("snapdb") / "mock.db"
    mock.build_mock_db(path)
    rules = mock.mock_rules()
    rules.lessons = {"stop_risk": [LessonLink(lesson_id="ex-1", reason="size to the stop")]}
    return tools.Ctx("mock", str(path), get_engine(str(path)), rules)


def _by_key(out):
    return {(w["collection"], w["doc_id"]): w["data"] for w in out["writes"]}


def _walk(node, path="$", in_tag=False):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k
            yield from _walk(v, f"{path}.{k}", k == "tags")
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, path, in_tag)


def test_shape_and_default_date(ctx):
    out = snapshot.push_phone_snapshot(ctx, now=NOW)
    assert out["tz"] == "America/Los_Angeles" and out["as_of"]
    assert out["generated_pt"] == "8:52 PT" and out["generated_at"] == "2026-10-15T15:52:00Z"
    assert "ArtifactData" in out["instructions"]
    docs = _by_key(out)
    assert set(docs) == {("snapshot", "meta"), ("snapshot_days", "2026-10-13"),
                         ("snapshot_week", "current")}
    meta = docs[("snapshot", "meta")]
    assert meta["latest_date"] == "2026-10-13" and meta["generated_pt"] == "8:52 PT"
    assert set(meta["line"]) == {"amount", "start_date", "pnl", "left", "trades_counted",
                                 "line_hit"}
    day = docs[("snapshot_days", "2026-10-13")]
    assert day["date"] == "2026-10-13" and len(day["positions"]) == 2
    pos = day["positions"][0]
    assert {"trade_key", "label", "pnl", "r", "tags", "plan", "off_plan", "fills"} <= set(pos)
    assert set(pos["fills"][0]) == {"side", "qty", "px", "cash", "ts_pt", "order_type", "pnl"}
    assert {"stats", "plans", "fields", "lessons", "off_plan_count", "plans_not_taken",
            "size_written_first", "daily_stop", "daily_target", "window"} <= set(day)
    week = docs[("snapshot_week", "current")]
    assert week["date_to"] == "2026-10-13" and week["date_from"] == "2026-10-07"


def test_explicit_date_and_bad_date(ctx):
    out = snapshot.push_phone_snapshot(ctx, "2026-10-08", now=NOW)
    assert ("snapshot_days", "2026-10-08") in _by_key(out)
    day = _by_key(out)[("snapshot_days", "2026-10-08")]
    assert day["positions"][0]["plan"]["caller"] == "mentor-a" and day["plans"]
    with pytest.raises(ValueError):
        snapshot.push_phone_snapshot(ctx, "nope")
    with pytest.raises(ValueError):
        snapshot.push_phone_snapshot(ctx, "2026-01-02")


def test_no_forbidden_keys_anywhere(ctx):
    for date in ("2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"):
        out = snapshot.push_phone_snapshot(ctx, date, now=NOW)
        for path, key in _walk(out):
            if key == "text":
                assert path.endswith(".tags"), path
            else:
                assert key not in FORBIDDEN_KEYS, (path, key)
        blob = json.dumps(out)
        assert str(ctx.db_path) not in blob and "localhost" not in blob
        assert "/knowledge/" not in blob


def test_lessons_carry_only_id_reason_pattern(ctx):
    out = snapshot.push_phone_snapshot(ctx, now=NOW)
    docs = _by_key(out)
    seen = docs[("snapshot_days", "2026-10-13")]["lessons"] + \
        docs[("snapshot_week", "current")]["lessons"]
    assert seen, "expected the stop_risk lesson link"
    for item in seen:
        assert set(item) == {"pattern", "lesson_id", "reason", "available"}
    assert seen[0]["lesson_id"] == "ex-1" and seen[0]["reason"] == "size to the stop"


def test_size_cap_drops_fills(ctx, monkeypatch):
    full = snapshot._size(snapshot._day_doc(tools.get_day(ctx, "2026-10-13")))
    monkeypatch.setattr(snapshot, "MAX_DOC_BYTES", full)
    out = snapshot.push_phone_snapshot(ctx, now=NOW)
    day = _by_key(out)[("snapshot_days", "2026-10-13")]
    assert day["fills_trimmed"] is True
    assert all("fills" not in p for p in day["positions"])


def test_real_docs_under_cap(ctx):
    out = snapshot.push_phone_snapshot(ctx, now=NOW)
    for w in out["writes"]:
        assert snapshot._size(w["data"]) < 200 * 1024
    assert "fills_trimmed" not in _by_key(out)[("snapshot_days", "2026-10-13")]


def test_registered_read_only_on_server(ctx):
    listed = asyncio.run(create_server(ctx).list_tools())
    tool = next(t for t in listed if t.name == "push_phone_snapshot")
    assert tool.annotations.readOnlyHint is True
    result = asyncio.run(create_server(ctx).call_tool("push_phone_snapshot", {"date": "2026-10-13"}))
    payload = result[1] if isinstance(result, tuple) else result
    text = json.dumps(payload, default=str)
    assert "snapshot_days" in text
