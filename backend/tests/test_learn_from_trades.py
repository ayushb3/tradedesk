"""Drills from your own rule breaks (app.learn.from_trades + the builder's trade source).

Synthetic sessions and synthetic trade signals only: no real trades, tickers or dates.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.knowledge import course_index as ci
from app.knowledge import course_search as cs
from app.knowledge.embedder import HashingEmbedder
from app.learn import build as B
from app.learn import from_trades as ft
from app.learn import ltp
from app.learn import store
from app.learn.detectors import Candidate
from tests import learn_synth as S
from tests.test_learn_build import loader

UTC = timezone.utc
FIX = Path(__file__).parent / "fixtures"
FOR = date(2026, 10, 28)      # a Wednesday; the synthetic sessions are the weekdays before it


@pytest.fixture()
def rules(tmp_path):
    shutil.copytree(FIX / "course_notes", tmp_path / "notes")
    notes = cs.CourseNotes(tmp_path / "notes", tmp_path / "cache" / ci.CACHE_NAME,
                           HashingEmbedder())
    return B.RuleBook(notes)


def sig(tid, day, codes, side="long", ticker="SPY", at=(14, 0)):
    d = date.fromisoformat(day)
    return ft.TradeSignal(tid, day, ticker, side,
                          datetime(d.year, d.month, d.day, *at, tzinfo=UTC), frozenset(codes))


# ------------------------------------------------------------------ models

def test_old_drill_files_without_because_still_load():
    files = sorted((FIX / "learn" / "drills").rglob("*.json"))
    assert files
    for f in files:
        assert "because" not in json.loads(f.read_text(encoding="utf-8"))
        d = store.load_drill_file(f)
        assert d is not None and d.because is None


def test_because_round_trips_through_a_drill_file(tmp_path):
    src = store.load_drill_file(sorted((FIX / "learn" / "drills").rglob("*.json"))[0])
    d = src.model_copy(update={"because": store.Because(trade_id=7, date="2026-10-27",
                                                        rule="exited_early")})
    p = store.write_drill(tmp_path, d)
    back = store.load_drill_file(p)
    assert back.because == store.Because(trade_id=7, date="2026-10-27", rule="exited_early")


# ------------------------------------------------------------------- wants

def test_pick_wants_maps_rules_to_kinds_and_cites_the_latest_trade():
    sigs = [sig(1, "2026-10-21", {"before_cutoff"}), sig(2, "2026-10-23", {"before_cutoff"}),
            sig(3, "2026-10-26", {"exited_early", "gave_back"}),
            sig(4, "2026-10-22", {"countertrend", "added_lower"}),
            sig(5, "2026-10-27", {"held_loser"})]          # no matching kind
    wants = ft.pick_wants(sigs)
    assert [w.kind for w in wants] == ["late", "first_target", "countertrend"]   # count, then recency
    late = wants[0]
    assert late.count == 2 and late.because == store.Because(trade_id=2, date="2026-10-23",
                                                             rule="before_cutoff")
    assert wants[1].because.rule == "exited_early" and wants[1].because.trade_id == 3
    assert wants[2].because.rule == "countertrend"
    assert ft.pick_wants([sig(9, "2026-10-27", {"held_loser"})]) == []


def test_pick_wants_breaks_ties_by_recency_then_kind_order():
    sigs = [sig(1, "2026-10-26", {"before_cutoff"}), sig(2, "2026-10-26", {"exited_early"}),
            sig(3, "2026-10-27", {"countertrend"})]
    assert [w.kind for w in ft.pick_wants(sigs)] == ["countertrend", "first_target", "late"]


def test_index_options_chart_on_the_etf():
    assert ft.chart_ticker("SPXW") == "SPY" and ft.chart_ticker("qqq") == "QQQ"
    w = ft.pick_wants([sig(1, "2026-10-26", {"before_cutoff"}, ticker="SPX")])[0]
    assert w.ticker == "SPY"


# ------------------------------------------------------------------- trend

def _trend_day(day: date, up: bool):
    a, b = (600.0, 606.0) if up else (606.0, 600.0)
    return S.full_day([(0, a), (120, b), (389, b)], day)


def test_trend_at_reads_the_auto_chart_and_never_guesses():
    day = date(2026, 10, 26)
    up, down = _trend_day(day, True), _trend_day(day, False)
    at = S.session_open(day) + timedelta(minutes=100)
    assert ft.trend_at(up, at) == "up" and ft.trend_at(down, at) == "down"
    assert ft.trend_at(up, S.session_open(day) + timedelta(minutes=5)) is None   # too few bars
    assert ft.trend_at([], at) is None


def test_mark_countertrend_only_when_the_entry_fights_the_trend():
    day = date(2026, 10, 26)
    bars = _trend_day(day, False)
    at = S.session_open(day) + timedelta(minutes=100)
    call = ft.TradeSignal(1, day.isoformat(), "SPY", "long", at)
    put = ft.TradeSignal(2, day.isoformat(), "SPY", "short", at)
    assert "countertrend" in ft.mark_countertrend(call, bars).codes
    assert "countertrend" not in ft.mark_countertrend(put, bars).codes
    unknown = ft.TradeSignal(3, day.isoformat(), "SPY", None, at)
    assert ft.mark_countertrend(unknown, bars) is unknown


# -------------------------------------------------------------- candidates

def _cand(pause: datetime, side="long", enter=True, **kw) -> Candidate:
    base = dict(detector="level_touches", ticker="SPY", date=pause.date().isoformat(),
                pause_at=pause, level=600.0, side=side, strength=0.6, enter=enter,
                stop=599.0 if enter else None, target=603.0 if enter else None)
    base.update(kw)
    return Candidate(**base)


def test_late_candidate_turns_any_setup_after_the_cutoff_into_a_wait():
    day = date(2026, 10, 26)
    early = _cand(datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC))   # 08:00 PT
    late = _cand(datetime(day.year, day.month, day.day, 16, 0, tzinfo=UTC))    # 09:00 PT
    assert ft.late_candidate(early, time(8, 30)) is None
    c = ft.late_candidate(late, time(8, 30))
    assert (c.enter, c.stop, c.target, c.entry, c.wait_reason) == (False, None, None, None,
                                                                   "after_cutoff")
    assert c.facts["trade_kind"] == "late" and c.facts["was_enter"] is True
    assert late.enter is True                       # the raw candidate is left alone


def test_countertrend_candidate_needs_a_side_against_the_trend_inside_the_window():
    day = date(2026, 10, 26)
    bars = _trend_day(day, False)
    at = S.session_open(day) + timedelta(minutes=100)
    assert ft.countertrend_candidate(_cand(at, "short"), bars) is None
    c = ft.countertrend_candidate(_cand(at, "long"), bars)
    assert c is not None and not c.enter and c.wait_reason == "countertrend"
    assert c.facts["trend"] == "down"
    assert ft.countertrend_candidate(_cand(at, "long"), bars, cutoff=time(6, 0)) is None


def test_first_target_pauses_on_the_target_bar_with_a_break_even_runner():
    day = date(2026, 10, 26)
    bars = S.ltp_session(day, True)
    cands = [c for c in ltp.ltp_pullback(bars, "SPY", day.isoformat(),
                                         ltp.SessionContext({"Prior day high": 604.6}))
             if c.enter]
    assert cands and cands[0].facts["next_levels"]
    raw = cands[0]
    close = B.rth_window(day)[1]
    c = ft.first_target_candidate(raw, bars, B.LOOKAHEAD_MIN, close)
    assert c is not None
    assert c.level == raw.target and c.enter and c.side == raw.side and c.entry is None
    assert c.stop == c.facts["fill"] and c.target > c.level > c.stop       # long runner
    hit = datetime.fromisoformat(c.facts["first_target_at"].replace("Z", "+00:00"))
    assert c.pause_at == hit + timedelta(minutes=1) and c.pause_at > raw.pause_at
    # a setup that never reached its target gives nothing
    flop = [x for x in ltp.ltp_pullback(S.ltp_session(day, False), "SPY", day.isoformat(),
                                        ltp.SessionContext({"Prior day high": 604.6})) if x.enter]
    for x in flop:
        assert ft.first_target_candidate(x, S.ltp_session(day, False), B.LOOKAHEAD_MIN,
                                         close) is None


# --------------------------------------------------------------- builder

SIGNALS = [
    sig(11, "2026-10-26", {"before_cutoff"}),
    sig(12, "2026-10-27", {"exited_early"}),
    sig(13, "2026-10-27", {"countertrend"}, side="short"),
]


def _build(rules, root=None, **kw):
    return B.build(FOR, 6, ["SPY", "QQQ"], loader=loader, rules=rules, root=root,
                   trade_signals=SIGNALS, **kw)


def test_build_adds_drills_from_your_trades_with_because(rules, tmp_path):
    root = tmp_path / "learn"
    res = _build(rules, root)
    trade = [d for d in res["drills"] if d.because is not None]
    market = [d for d in res["drills"] if d.because is None]
    assert len(market) == 3 and 1 <= len(trade) <= ft.TRADE_PICK
    assert res["from_trades"]["built"] == len(trade)
    for d in trade:
        assert d.because.trade_id in {11, 12, 13}
        assert d.because.rule in {"exited_early", "countertrend", "before_cutoff"}
        assert "-t" in d.id and d.lessons and d.grading_rubric.rule_refs
        if d.because.rule == "exited_early":
            assert d.answer_key.enter == "enter" and d.answer_key.entry is None
            assert "first target" in d.explanation
        else:
            assert d.answer_key.enter == "wait"
    keys = {(d.ticker, d.date, d.pause_at) for d in res["drills"]}
    assert len(keys) == len(res["drills"])                       # no setup served twice
    on_disk = store.load_set(root, FOR.isoformat())
    assert {d.id: d.because for d in on_disk} == {d.id: d.because for d in res["drills"]}


def test_build_from_trades_is_deterministic(rules, tmp_path):
    a = _build(rules, tmp_path / "a")
    b = _build(rules, tmp_path / "b")
    dump = lambda r: [d.model_dump(mode="json") for d in r["drills"]]   # noqa: E731
    assert dump(a) == dump(b)


def test_late_drills_respect_the_cutoff(rules):
    res = B.build(FOR, 6, ["SPY", "QQQ"], loader=loader, rules=rules, root=None,
                  trade_signals=[sig(21, "2026-10-26", {"before_cutoff"})], cutoff=time(6, 45))
    late = [d for d in res["drills"] if d.because is not None]
    assert len(late) == 1 and late[0].answer_key.enter == "wait"
    assert late[0].pause_at.astimezone(ft.PT).time() > time(6, 45)
    assert "06:45 PT cutoff" in late[0].explanation


def test_no_signals_means_the_same_market_set(rules):
    plain = B.build(FOR, 6, ["SPY", "QQQ"], loader=loader, rules=rules, root=None)
    none = B.build(FOR, 6, ["SPY", "QQQ"], loader=loader, rules=rules, root=None,
                   trade_signals=[])
    assert [d.id for d in plain["drills"]] == [d.id for d in none["drills"]]
    assert all(d.because is None for d in plain["drills"])


def test_trade_pick_zero_builds_none(rules):
    res = _build(rules, trade_pick=0)
    assert all(d.because is None for d in res["drills"])


# --------------------------------------------------------------- signals

def test_signals_from_days_collects_broken_rules_tags_and_excursions():
    from app.analytics.excursions import ExcursionFact

    fill = SimpleNamespace(ts=datetime(2026, 10, 26, 16, 0, tzinfo=UTC))
    lp = SimpleNamespace(trade_id=5, position=SimpleNamespace(ticker="SPY", type="C"))
    a = SimpleNamespace(tags=[SimpleNamespace(code="added_lower")], fills=[fill])
    kept = SimpleNamespace(code="daily_stop", kept=True)
    broken = SimpleNamespace(code="before_cutoff", kept=False)
    unknown = SimpleNamespace(code="call_source", kept=None)
    day = SimpleNamespace(
        loaded=[lp], result=SimpleNamespace(day=date(2026, 10, 26), positions=[a]),
        rule_results=SimpleNamespace(trades={5: SimpleNamespace(results=[kept, broken, unknown])}),
        excursions={5: ExcursionFact(5, pnl=10.0, risk=10.0, crossed_stop=False,
                                     drawdown_minutes=0, beyond_exit_r=1.5)},
    )
    [s] = ft.signals_from_days([day])
    assert s.trade_id == 5 and s.date == "2026-10-26" and s.side == "long"
    assert s.entry_ts == fill.ts
    assert s.codes == frozenset({"added_lower", "before_cutoff", "exited_early"})


def test_load_signals_on_an_empty_desk_is_empty(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEDESK_RULES_PATH", str(tmp_path / "rules.yaml"))
    assert ft.load_signals(FOR) == []
