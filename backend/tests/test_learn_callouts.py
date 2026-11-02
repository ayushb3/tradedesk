"""Drills from captured calls. Invented callout files only (no real call data)."""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.knowledge import course_index as ci
from app.knowledge import course_search as cs
from app.knowledge.embedder import HashingEmbedder
from app.learn import build as B
from app.learn import callouts as C
from tests import learn_synth as S

DAY = date(2026, 10, 27)
FOR = date(2026, 10, 28)
FIX = Path(__file__).parent / "fixtures"


def _wall(minute: int) -> str:
    return (S.session_open(DAY) + timedelta(minutes=minute, seconds=17)).astimezone(
        C.ltp.ET).isoformat()


def _event(kind: str, minute: int, ticker="SPX", direction="call", conf=0.95, flags=()):
    return {"type": kind, "ticker": ticker, "direction": direction, "confidence": conf,
            "quote": "invented", "ts": 0.0, "strike": None, "expiry": None, "price": None,
            "level": None, "wall": _wall(minute), "group": "SP500", "source": "test",
            "flags": list(flags)}


def _write(root: Path, events: list[dict], labels: list[dict] | None = None) -> Path:
    (root / "callouts").mkdir(parents=True, exist_ok=True)
    (root / "callouts" / f"callouts-{DAY}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")
    if labels is not None:
        (root / "labels").mkdir(exist_ok=True)
        cols = ["real?(y/n)", "correct_ticker", "correct_type", "time", "type", "ticker",
                "direction", "quote"]
        lines = [",".join(cols)] + [",".join(str(r.get(c, "")) for c in cols) for r in labels]
        (root / "labels" / f"label-{DAY}.csv").write_text("\n".join(lines), encoding="utf-8")
    return root


def test_entry_calls_map_to_the_chart_ticker_and_collect_later_exits(tmp_path):
    root = _write(tmp_path, [_event("watch", 50), _event("entry", 66), _event("trim", 80),
                             _event("entry", 70, ticker="TSLA", direction="put"),
                             _event("exit", 90, ticker="SPY")])
    got = C.load_calls(root, DAY)
    assert [(c.ticker, c.called, c.side) for c in got] == [("SPY", "SPX", "long"),
                                                           ("TSLA", "TSLA", "short")]
    assert len(got[0].exits) == 2 and got[1].exits == []


def test_unlabelled_days_keep_only_confident_unflagged_calls(tmp_path):
    root = _write(tmp_path, [_event("entry", 66, conf=0.5), _event("entry", 70, flags=["restated"]),
                             _event("entry", 72, flags=["ticker_inferred"]), _event("entry", 74)])
    assert [c.at.minute for c in C.load_calls(root, DAY)] == [44]   # 10:44 ET = minute 74


def test_labelled_days_keep_only_rows_marked_real_with_ticker_fixes(tmp_path):
    ev = [_event("entry", 66), _event("entry", 74)]
    t = [e["wall"][11:19] for e in ev]
    root = _write(tmp_path, ev, labels=[
        {"real?(y/n)": "n", "time": t[0], "type": "entry"},
        {"real?(y/n)": "y", "correct_ticker": "qqq", "time": t[1], "type": "entry"}])
    got = C.load_calls(root, DAY)
    assert [(c.ticker, c.at.minute) for c in got] == [("QQQ", 44)]


def test_missing_folder_or_day_gives_no_calls(tmp_path):
    assert C.load_calls(None, DAY) == [] and C.load_calls(tmp_path, DAY) == []


def test_call_candidate_pauses_at_the_call_minute_with_a_rule_shaped_plan(tmp_path):
    call = C.load_calls(_write(tmp_path, [_event("entry", 68)]), DAY)[0]
    ctx = C.ltp.SessionContext({"Prior day high": 605.0})
    c = C.call_candidate(call, S.ltp_session(DAY), DAY.isoformat(), ctx)
    assert c is not None and c.detector == "somesh_call" and c.enter and c.side == "long"
    assert c.pause_at == S.session_open(DAY) + timedelta(minutes=68)
    assert c.stop < c.facts["entry"] < c.target
    assert (c.target - c.facts["entry"]) >= C.MIN_RR * (c.facts["entry"] - c.stop)
    assert c.level <= c.facts["entry"] and c.facts["target_name"] == "Prior day high"


def test_a_call_with_no_level_paying_the_risk_is_skipped(tmp_path):
    call = C.load_calls(_write(tmp_path, [_event("entry", 68)]), DAY)[0]
    assert C.call_candidate(call, S.ltp_session(DAY), DAY.isoformat()) is None


@pytest.fixture()
def rules(tmp_path):
    shutil.copytree(FIX / "course_notes", tmp_path / "notes")
    return B.RuleBook(cs.CourseNotes(tmp_path / "notes", tmp_path / "cache" / ci.CACHE_NAME,
                                     HashingEmbedder()))


def test_build_puts_a_working_call_in_the_set_and_drops_a_losing_one(rules, tmp_path):
    root = _write(tmp_path / "calls", [_event("entry", 68)])
    res = B.build(FOR, 1, ["SPY"], loader=lambda t, d: S.ltp_session(d, True, t), rules=rules,
                  root=None, callouts_root=root)
    call = [d for d in res["drills"] if d.detector == "somesh_call"]
    assert res["candidates"]["somesh_call"] == 1 and len(call) == 1
    assert "Somesh called calls" in call[0].explanation and "invented" not in call[0].explanation
    lose = B.build(FOR, 1, ["SPY"], loader=lambda t, d: S.ltp_session(d, False, t), rules=rules,
                   root=None, callouts_root=root)
    assert not [d for d in lose["drills"] if d.detector == "somesh_call"]
