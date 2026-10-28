"""Day view stays cheap: init_db once per engine, no desk reload per option-bar read,
memoized mock excursion facts and mock bars. Same output as an uncached load.

Synthetic mock DB only (made-up tickers); no network.
"""
from __future__ import annotations

from sqlalchemy import create_engine

from app.db import connection


def _ctx(tmp_path):
    from app.db.connection import get_engine
    from app.desk_mcp import mock, tools

    path = tmp_path / "mock.db"
    mock.build_mock_db(path)
    return tools.Ctx("mock", str(path), get_engine(str(path)), mock.mock_rules())


def _busy_day(ctx) -> str:
    from app.desk_mcp import tools

    days = tools.list_days(ctx)["days"]
    return max(days, key=lambda d: d["positions"])["date"]


def test_init_db_bootstraps_each_engine_once(tmp_path, monkeypatch):
    calls = []
    real = connection.metadata.create_all
    monkeypatch.setattr(
        connection.metadata, "create_all", lambda e, *a, **k: (calls.append(e), real(e, *a, **k))
    )
    a = create_engine(f"sqlite:///{tmp_path / 'a.db'}", future=True)
    b = create_engine(f"sqlite:///{tmp_path / 'b.db'}", future=True)
    for _ in range(3):
        connection.init_db(a)
    connection.init_db(b)
    assert calls == [a, b]


def test_option_bar_reads_do_not_reload_desk_days(tmp_path, monkeypatch):
    """Bars need only the trade's session date; R/rule results are not computed."""
    from app.desk import days as desk_days
    from app.desk_mcp import tools
    from app.ingest.option_bars import get_cached_option_bars_for_trade, get_option_bars_for_trade
    from app.providers.mock import MockProvider
    from app.config import get_settings

    ctx = _ctx(tmp_path)
    tid = tools.get_day(ctx, _busy_day(ctx))["positions"][0]["trade_id"]

    def boom(*a, **k):
        raise AssertionError("desk days reloaded for an option-bar read")

    monkeypatch.setattr(desk_days, "analysis_by_trade", boom)
    bars = get_option_bars_for_trade(tid, MockProvider(get_settings()), "1m", ctx.engine, False)
    assert bars is not None and bars.bars
    assert get_cached_option_bars_for_trade(tid, "1m", ctx.engine, source="mock") is None
    assert get_option_bars_for_trade(10_000, MockProvider(get_settings()), "1m", ctx.engine) is None


def test_mock_facts_memoized_and_identical(tmp_path, monkeypatch):
    from app.desk_mcp import tools
    from app.ingest import excursion_rollup as er

    ctx = _ctx(tmp_path)
    day = _busy_day(ctx)
    first = tools.get_day(ctx, day)

    calls = []
    real = er.fact_for_position
    monkeypatch.setattr(er, "fact_for_position", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    again = tools.get_day(ctx, day)
    assert calls == []  # every position served from the memo
    assert again == first

    er.clear_fact_memo()
    fresh = tools.get_day(ctx, day)
    assert calls and fresh == first  # recomputed, same answer


def test_mock_provider_memo_returns_equal_fresh_lists():
    from datetime import date

    from app.config import get_settings
    from app.providers.mock import MockProvider
    from app.providers.mock import provider as mp
    from app.providers.mock.synthetic import generate_candles, resample_candles

    p = MockProvider(get_settings())
    day = date(2026, 10, 13)
    one, two = p.get_candles("ZORP", day, "5m"), p.get_candles("ZORP", day, "5m")
    assert one == two and one is not two
    one.clear()
    assert p.get_candles("ZORP", day, "5m") == two
    assert two == resample_candles(generate_candles("ZORP", day, tz=p.tz), "5m", tz=p.tz)
    assert mp._session_candles.cache_info().hits >= 1
