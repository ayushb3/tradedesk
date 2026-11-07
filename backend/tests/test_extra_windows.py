"""Optional ``extra_windows`` in the rules: entries in a later session are not late.

Synthetic fixture only (tests/fixtures/desk/rules_extra_windows.yaml): main window
06:30-08:30 PT, an 11:00-12:00 "afternoon session" and a 12:30-13:00 "close session".
Times are UTC; PT = UTC-7 on 2026-10-14 (18:00Z = 11:00 PT).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from app.desk import ledger, matching, rule_results as rr
from app.desk.ledger import DayInput, Fill, Position
from app.desk.rules import (
    EXAMPLE_PATH,
    dump_rules,
    entry_window,
    is_after_window,
    last_window_end,
    load_rules,
    validate,
)

UTC = timezone.utc
DAY = date(2026, 10, 14)
FIXTURE = Path(__file__).parent / "fixtures" / "desk" / "rules_extra_windows.yaml"


def at(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, 14, hh, mm, tzinfo=UTC)


def pos(open_at: datetime) -> Position:
    return Position(
        fills=[
            Fill("B", 1, 1.0, -100.0, open_at),
            Fill("S", 1, 1.1, 110.0, open_at + timedelta(minutes=5)),
        ],
        status="closed",
    )


def rules():
    return load_rules(FIXTURE)


def test_fixture_is_valid_and_round_trips(tmp_path):
    assert validate(FIXTURE.read_text(encoding="utf-8")) == []
    r = rules()
    assert [w.label for w in r.extra_windows] == ["afternoon session", "close session"]
    p = tmp_path / "r.yaml"
    p.write_text(dump_rules(r), encoding="utf-8")
    assert load_rules(p) == r


def test_files_without_extra_windows_dump_without_the_key():
    assert "extra_windows" not in dump_rules(load_rules(EXAMPLE_PATH))
    assert load_rules(EXAMPLE_PATH).extra_windows == []


def test_validate_rejects_bad_extra_windows():
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    data["extra_windows"] = [{"start": "12:00", "end": "11:00", "label": "x"}]
    assert any("not after start" in e for e in validate(yaml.safe_dump(data)))
    data["extra_windows"] = [{"start": "noon", "end": "13:00"}]
    assert any("HH:MM" in e for e in validate(yaml.safe_dump(data)))


def test_entry_window_main_extra_and_late():
    r = rules()
    assert entry_window(r, at(15, 29), DAY) == (True, None)               # 08:29 PT
    assert entry_window(r, at(18, 15), DAY) == (True, "afternoon session")  # 11:15 PT
    assert entry_window(r, at(19, 45), DAY) == (True, "close session")      # 12:45 PT
    assert is_after_window(r, at(16, 0), DAY)                               # 09:00 PT
    assert is_after_window(r, at(19, 15), DAY)                              # 12:15 PT, between
    assert is_after_window(r, at(20, 1), DAY)                               # 13:01 PT
    assert last_window_end(r, DAY) == at(20, 0)


def test_before_cutoff_accepts_fills_in_an_extra_window():
    r = rules()
    ins = [
        rr.TradeInput(1, ledger.analyze_position(pos(at(14, 0)), r), None, None),   # 07:00 PT
        rr.TradeInput(2, ledger.analyze_position(pos(at(18, 10)), r), None, None),  # 11:10 PT
        rr.TradeInput(3, ledger.analyze_position(pos(at(16, 30)), r), None, None),  # 09:30 PT
    ]
    d = rr.evaluate_day(DAY, ins, r)

    def res(tid):
        return next(x for x in d.trades[tid].results if x.code == "before_cutoff")

    assert res(1).kept is True
    assert res(2).kept is True and "afternoon session" in res(2).detail
    assert res(3).kept is False
    day = next(x for x in d.results if x.code == "before_cutoff")
    assert day.kept is False and "1 of 3" in day.detail


def test_ledger_late_fills_skip_extra_windows():
    r = rules()
    day = DayInput(date=DAY, positions=[pos(at(18, 10)), pos(at(16, 30))])
    # Afternoon trade: both fills inside 11:00-12:00. Late trade: both fills at 09:30+.
    assert ledger.analyze_day(day, r).stats.late_fills == 2


def test_day_is_not_finished_until_the_last_window_ends():
    r = rules()
    assert not matching.day_is_over(DAY, r, now=at(18, 30))
    assert matching.day_is_over(DAY, r, now=at(20, 1))
