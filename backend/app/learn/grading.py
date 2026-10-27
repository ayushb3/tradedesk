"""Deterministic drill grader. No LLM, no randomness.

Four steps (level, enter, stop, target) share 100 points equally among the GRADED steps. When
the key is "wait" only level and enter are graded (the score is rescaled over those two).

    level   |yours - key| / key <= tol -> full; <= 3 * tol -> half; else 0 (pct based)
    enter   exact match
    stop    must sit on the losing side of the key level for the key side; then, with
            R = |key level - key stop|: full within stop_tol_r (0.35R) of the key stop, half
            within 1R of it, else 0
    target  must sit on the profit side; full within target_tol_r (0.5R) of the key target,
            half within 1.5R, else 0

R comes from the answer key, so drill files written before stop_tol_r / target_tol_r existed
still grade (the rubric defaults fill the gaps).

Stop and target are only credited when the answer's side matches the key side.
"""
from __future__ import annotations

from pydantic import BaseModel

from .store import AnswerKey, Rubric


class Answers(BaseModel):
    level: float | None = None
    enter: str  # "enter" | "wait" (validated at the API edge)
    side: str | None = None
    stop: float | None = None
    target: float | None = None
    note: str | None = None


def _rel_pct(yours: float, key: float) -> float:
    if key == 0:
        return float("inf")
    return abs(yours - key) / abs(key) * 100.0


def _tier(yours: float, key: float, tol_pct: float) -> tuple[float, str]:
    d = _rel_pct(yours, key)
    if d <= tol_pct:
        return 1.0, "within tolerance"
    if d <= 3 * tol_pct:
        return 0.5, "close"
    return 0.0, "off"


STOP_HALF_R = 1.0
TARGET_HALF_R = 1.5
_EPS = 1e-9


def _risk(key: AnswerKey) -> float:
    """R: the key's risk, level to key stop (floored so a degenerate old key cannot divide by 0)."""
    r = abs(key.level - key.stop) if key.stop is not None else 0.0
    return max(r, 0.0001 * abs(key.level), 0.01)


def _tier_r(yours: float, key: float, r: float, full_r: float, half_r: float) -> float:
    d = abs(yours - key)
    if d <= full_r * r + _EPS:
        return 1.0
    if d <= half_r * r + _EPS:
        return 0.5
    return 0.0


def _cite(rubric: Rubric, step: str) -> str:
    ref = rubric.rule_refs.get(step)
    return f" Rule: {ref}" if ref else ""


def _step_level(key: AnswerKey, a: Answers, rub: Rubric) -> tuple[float, str]:
    if a.level is None:
        return 0.0, "No level given."
    frac, label = _tier(a.level, key.level, rub.level_tol_pct)
    return frac, {"within tolerance": "Level matches.", "close": "Level is close, not exact.",
                  "off": "Level is off."}[label]


def _step_enter(key: AnswerKey, a: Answers, rub: Rubric) -> tuple[float, str]:
    if a.enter == key.enter:
        return 1.0, "Right call: " + ("take it." if key.enter == "enter" else "wait.")
    return 0.0, ("The setup was there: this was an enter."
                 if key.enter == "enter" else "The conditions were not met yet: this was a wait.")


def _side_ok(key: AnswerKey, a: Answers) -> bool:
    return a.side is not None and a.side == key.side


def _step_stop(key: AnswerKey, a: Answers, rub: Rubric) -> tuple[float, str]:
    if a.stop is None:
        return 0.0, "No stop given."
    if not _side_ok(key, a):
        return 0.0, f"Direction differs from the key ({key.side})."
    wrong_side = a.stop >= key.level if key.side == "long" else a.stop <= key.level
    if wrong_side:
        return 0.0, "Stop is on the wrong side of the level."
    frac = _tier_r(a.stop, key.stop, _risk(key), rub.stop_tol_r, STOP_HALF_R)
    if frac == 1.0:
        return 1.0, "Stop matches."
    if frac == 0.5:
        return 0.5, "Stop is on the right side but not where the rule puts it."
    return 0.0, "Stop is too far from where the rule puts it."


def _step_target(key: AnswerKey, a: Answers, rub: Rubric) -> tuple[float, str]:
    if a.target is None:
        return 0.0, "No target given."
    if not _side_ok(key, a):
        return 0.0, f"Direction differs from the key ({key.side})."
    wrong_side = a.target <= key.level if key.side == "long" else a.target >= key.level
    if wrong_side:
        return 0.0, "Target is on the wrong side of the level."
    frac = _tier_r(a.target, key.target, _risk(key), rub.target_tol_r, TARGET_HALF_R)
    if frac == 1.0:
        return 1.0, "Target matches."
    if frac == 0.5:
        return 0.5, "Target is on the profit side but not at the rule's level."
    return 0.0, "Target is too far from the rule's level."


_FNS = {"level": _step_level, "enter": _step_enter, "stop": _step_stop, "target": _step_target}


def graded_steps(key: AnswerKey) -> list[str]:
    steps = ["level", "enter"]
    if key.enter == "enter" and key.side and key.stop is not None:
        steps.append("stop")
    if key.enter == "enter" and key.side and key.target is not None:
        steps.append("target")
    return steps


def grade(key: AnswerKey, rubric: Rubric, answers: Answers) -> dict:
    """{score, steps:[{step, graded, ok, points, max, yours, key, note}], rule_refs}."""
    graded = graded_steps(key)
    per = 100.0 / len(graded)
    steps, total = [], 0.0
    for step in ("level", "enter", "stop", "target"):
        yours = getattr(answers, step)
        kval = getattr(key, step)
        if step not in graded:
            steps.append({"step": step, "graded": False, "ok": False, "points": 0, "max": 0,
                          "yours": yours, "key": kval,
                          "note": "Not graded: the key is to wait." + _cite(rubric, step)})
            continue
        frac, msg = _FNS[step](key, answers, rubric)
        pts = round(frac * per, 1)
        total += frac * per
        steps.append({"step": step, "graded": True, "ok": frac == 1.0, "points": pts,
                      "max": round(per, 1), "yours": yours, "key": kval,
                      "note": msg + _cite(rubric, step)})
    return {"score": int(round(total)), "steps": steps, "rule_refs": dict(rubric.rule_refs)}
