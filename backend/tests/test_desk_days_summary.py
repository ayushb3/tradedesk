"""list_days carries full day stats and callers (desk page across-days table, issue #14)."""
import pytest

from app.desk_mcp import mock, tools


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    from app.db.connection import get_engine

    path = tmp_path_factory.mktemp("mockdb") / "mock.db"
    mock.build_mock_db(path)
    return tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())


def test_list_days_rows_match_get_day_stats(ctx):
    rows = tools.list_days(ctx)["days"]
    assert rows
    for row in rows:
        day = tools.get_day(ctx, row["date"])
        assert row["stats"] == day["stats"]
        assert row["size_written_first"] == day["size_written_first"]


def test_list_days_callers_fall_back_to_plan_cards(ctx):
    rows = {r["date"]: r for r in tools.list_days(ctx)["days"]}
    planned = [r for r in rows.values() if r["callers"]]
    assert planned, "mock fixture has a plan card with a caller"
    for r in rows.values():
        day = tools.get_day(ctx, r["date"])
        expected = (day["fields"] or {}).get("callers") or list(
            dict.fromkeys(p["caller"] for p in day["plans"]))
        assert r["callers"] == expected
