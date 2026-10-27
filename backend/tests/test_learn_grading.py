"""Deterministic Learn grader. Synthetic keys only."""
from __future__ import annotations

from app.learn.grading import Answers, grade
from app.learn.store import AnswerKey, Rubric

RUB = Rubric(rule_refs={"level": "2.1 § A", "enter": "2.1 § B", "stop": "3.1.1 § C",
                        "target": "2.1 § D"})
LONG = AnswerKey(level=100.0, enter="enter", side="long", stop=99.5, target=102.0)
SHORT = AnswerKey(level=100.0, enter="enter", side="short", stop=100.5, target=98.0)
WAIT = AnswerKey(level=100.0, enter="wait")


def _g(key, **kw):
    base = dict(level=key.level, enter=key.enter, side=key.side, stop=key.stop, target=key.target)
    base.update(kw)
    return grade(key, RUB, Answers(**base))


def _step(res, name):
    return next(s for s in res["steps"] if s["step"] == name)


def test_perfect_answer_scores_100():
    r = _g(LONG)
    assert r["score"] == 100
    assert all(s["ok"] and s["graded"] for s in r["steps"])
    assert [s["max"] for s in r["steps"]] == [25.0] * 4


def test_wait_key_grades_only_level_and_enter_and_rescales():
    r = _g(WAIT)
    assert r["score"] == 100
    assert [s["graded"] for s in r["steps"]] == [True, True, False, False]
    assert _step(r, "level")["max"] == 50.0
    # right level, wrong call
    r = _g(WAIT, enter="enter", side="long", stop=99.0, target=101.0)
    assert r["score"] == 50
    assert _step(r, "enter")["ok"] is False


def test_wrong_enter_when_key_is_enter():
    r = _g(LONG, enter="wait", side=None, stop=None, target=None)
    assert _step(r, "enter")["points"] == 0
    assert _step(r, "stop")["points"] == 0 and _step(r, "target")["points"] == 0
    assert r["score"] == 25


def test_level_tolerances_full_half_zero():
    assert _step(_g(LONG, level=100.09), "level")["points"] == 25      # 0.09% <= 0.1%
    assert _step(_g(LONG, level=100.25), "level")["points"] == 12.5    # 0.25% <= 0.3%
    assert _step(_g(LONG, level=101.0), "level")["points"] == 0
    assert _step(_g(LONG, level=None), "level")["points"] == 0


def test_stop_wrong_side_scores_zero_even_if_close():
    r = _g(LONG, stop=100.2)                       # above the level for a long
    assert _step(r, "stop")["points"] == 0
    assert "wrong side" in _step(r, "stop")["note"]
    r = _g(SHORT, stop=99.8)                       # below the level for a short
    assert _step(r, "stop")["points"] == 0


def test_stop_right_side_but_far_gets_half():
    s = _step(_g(LONG, stop=98.0), "stop")
    assert (s["points"], s["ok"]) == (12.5, False)
    assert _step(_g(LONG, stop=99.54), "stop")["ok"] is True  # within 0.1% of 99.5


def test_target_sides_and_tolerance():
    assert _step(_g(LONG, target=99.0), "target")["points"] == 0       # below level for a long
    assert _step(_g(LONG, target=105.0), "target")["points"] == 12.5   # profit side, not at key
    assert _step(_g(LONG, target=102.1), "target")["ok"] is True       # 0.05% <= 0.15%
    assert _step(_g(SHORT, target=98.1), "target")["ok"] is True
    assert _step(_g(SHORT, target=101.0), "target")["points"] == 0


def test_side_mismatch_zeroes_stop_and_target():
    r = _g(LONG, side="short", stop=100.5, target=98.0)
    assert _step(r, "stop")["points"] == 0 and _step(r, "target")["points"] == 0
    assert "Direction" in _step(r, "stop")["note"]


def test_notes_cite_rule_refs():
    r = _g(LONG, stop=98.0)
    assert r["rule_refs"] == RUB.rule_refs
    for s in r["steps"]:
        assert RUB.rule_refs[s["step"]] in s["note"]
    assert RUB.rule_refs["stop"] in _step(_g(WAIT), "stop")["note"]


def test_score_is_an_int_and_bounded():
    r = _g(LONG, level=50, enter="wait", side=None, stop=None, target=None)
    assert isinstance(r["score"], int) and 0 <= r["score"] <= 100
