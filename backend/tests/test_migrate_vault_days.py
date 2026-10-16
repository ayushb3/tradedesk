"""migrate_vault_days (issue #16). Vault files are made up in tmp_path."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from scripts import migrate_vault_days as mig
from app.desk.day_fields import get_day_fields
from app.desk.rules import Rules
from app.ingest.imports import import_fills
from app.models.trades import Fill, Side

RULES = Rules.model_validate(yaml.safe_load("""\
timezone: America/Los_Angeles
window: {start: "06:30", end: "08:30"}
daily_stop: -100
daily_target: 40
loss_line: {amount: 1000, start_date: 2026-10-01}
default_risk_pct: 30
account_size: {source_path: does/not/exist.json, fallback: 5000}
rules: []
lessons: {}
"""))

# A vault key whose name carries a person's name is built at runtime from a neutral variable.
WHO = "mentor"
CALLER_KEY = f"{WHO}_only"


def _jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _day(date, rev, **kw):
    base = {"schema_version": 1, "date": date, "rev": rev, "traded": True, "pnl_usd": 0,
            CALLER_KEY: None, "stop_hit": False, "target_hit": False,
            "size_written_first": None, "max_loss_declared_usd": None,
            "platform_closed_by_8": None, "notes": ""}
    return base | kw


def _trade(tid, date, pnl, rev=1, excluded=False):
    return {"schema_version": 1, "id": tid, "rev": rev, "date": date, "ticker": "AAA",
            "pnl_usd": pnl, "excluded": excluded, "notes": ""}


@pytest.fixture()
def vault(tmp_path):
    days = _jsonl(tmp_path / "days.jsonl", [
        _day("2026-10-02", 1),
        _day("2026-10-05", 1, **{CALLER_KEY: True}),
        _day("2026-10-05", 2, **{CALLER_KEY: True, "size_written_first": True,
                                 "max_loss_declared_usd": 100, "platform_closed_by_8": False,
                                 "stop_hit": True, "notes": "first thing", "extra_flag": "x"}),
        _day("2026-10-06", 1, **{CALLER_KEY: False, "target_hit": True}),
    ])
    trades = _jsonl(tmp_path / "trades.jsonl", [
        _trade("a", "2026-10-05", -5),
        _trade("a", "2026-10-05", 10, rev=2),          # latest rev wins
        _trade("b", "2026-10-05", 99, excluded=True),   # excluded plays are not counted
        _trade("c", "2026-09-30", 7),                   # before the line start
    ])
    return days, trades


def _seed_desk(engine, date_utc=(2026, 10, 5)):
    base = datetime(*date_utc, 14, 0, tzinfo=timezone.utc)  # 07:00 PT

    class Static:
        def get_fills(self, since=None):
            return [
                Fill(provider_fill_id="syn-1", account="TEST", symbol="AAA", side=Side.buy,
                     qty=10, price=1.0, ts=base, fees=0.0, simulated=False),
                Fill(provider_fill_id="syn-2", account="TEST", symbol="AAA", side=Side.sell,
                     qty=10, price=2.0, ts=base + timedelta(minutes=5), fees=0.0, simulated=False),
            ]

    import_fills(Static(), engine)


def test_map_day_uses_neutral_names_and_notes():
    got = mig.map_day(_day("2026-10-05", 2, **{
        CALLER_KEY: True, "size_written_first": True, "max_loss_declared_usd": 100,
        "platform_closed_by_8": False, "stop_hit": True, "notes": "first thing", "extra_flag": "x"}))
    assert got == {
        "callers": [WHO], "size_written_first": True, "max_loss_declared": 100,
        "closed_by_window_end": False, "stop_hit": True, "target_hit": False,
        "notes": "first thing\nextra_flag: x",
    }
    assert CALLER_KEY not in got and mig.map_day(_day("d", 1, **{CALLER_KEY: False}))["callers"] == []


def test_highest_rev_per_date_migrates(temp_engine, vault):
    days, trades = vault
    _seed_desk(temp_engine)
    mig.run(days, trades, engine=temp_engine, rules=RULES, out=lambda *_: None)
    d5 = get_day_fields("2026-10-05", temp_engine)
    assert d5.callers == [WHO] and d5.size_written_first is True
    assert d5.max_loss_declared == 100 and d5.closed_by_window_end is False
    assert d5.stop_hit is True and "first thing" in d5.notes and "extra_flag: x" in d5.notes
    d6 = get_day_fields("2026-10-06", temp_engine)
    assert d6.callers == [] and d6.target_hit is True
    assert get_day_fields("2026-10-02", temp_engine) is not None


def test_totals_match_exit_zero(temp_engine, vault, capsys):
    days, trades = vault
    _seed_desk(temp_engine)  # +10 on 10-05, same as the latest-rev vault trade
    lines = []
    assert mig.run(days, trades, engine=temp_engine, rules=RULES, out=lines.append) == 0
    assert "totals match" in lines[-1]


def test_totals_differ_exit_nonzero_and_names_dates(temp_engine, vault):
    days, trades = vault  # nothing seeded in the desk: 10-05 differs
    lines = []
    assert mig.run(days, trades, engine=temp_engine, rules=RULES, out=lines.append) == 1
    text = "\n".join(lines)
    assert "DIFFER" in text and "2026-10-05" in text and "-10.00" in text
    assert "2026-09-30" not in text and "2026-10-02" not in text


def test_idempotent(temp_engine, vault):
    days, trades = vault
    quiet = lambda *_: None  # noqa: E731
    mig.run(days, trades, engine=temp_engine, rules=RULES, out=quiet)
    first = {d: get_day_fields(d, temp_engine) for d in ("2026-10-02", "2026-10-05", "2026-10-06")}
    lines = []
    mig.run(days, trades, engine=temp_engine, rules=RULES, out=lines.append)
    assert "migrated 0, already current 3" in lines[1]
    assert {d: get_day_fields(d, temp_engine) for d in first} == first  # updated_at untouched


def test_dry_run_writes_nothing_and_vault_untouched(temp_engine, vault):
    days, trades = vault
    before = (days.read_bytes(), trades.read_bytes())
    lines = []
    mig.run(days, trades, dry_run=True, engine=temp_engine, rules=RULES, out=lines.append)
    assert "would migrate 3" in lines[1]
    assert get_day_fields("2026-10-05", temp_engine) is None
    mig.run(days, trades, engine=temp_engine, rules=RULES, out=lambda *_: None)
    assert (days.read_bytes(), trades.read_bytes()) == before


def test_does_not_clear_existing_values_with_nulls(temp_engine, tmp_path):
    from app.desk.day_fields import upsert_day_fields

    upsert_day_fields("2026-10-02", temp_engine, why="kept")
    days = _jsonl(tmp_path / "d.jsonl", [_day("2026-10-02", 1)])
    trades = _jsonl(tmp_path / "t.jsonl", [])
    mig.run(days, trades, engine=temp_engine, rules=RULES, out=lambda *_: None)
    assert get_day_fields("2026-10-02", temp_engine).why == "kept"


def test_bad_input(tmp_path, temp_engine):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{nope\n", encoding="utf-8")
    with pytest.raises(mig.InputError):
        mig.run(bad, bad, engine=temp_engine, rules=RULES, out=lambda *_: None)
    with pytest.raises(mig.InputError):
        mig.run(tmp_path / "missing.jsonl", bad, engine=temp_engine, rules=RULES, out=lambda *_: None)
