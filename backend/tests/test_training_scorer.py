"""Hand-computed fixtures for the process scorer (M6 task 6.1.1).

All tests build an Attempt and a duck-typed ScenarioFull-like namespace and
assert the exact expected numeric score.  No database, no HTTP — pure unit.

Formula reminder
----------------
process_score = clamp(0.7 * decision_correctness + 0.3 * checklist_adherence)

decision_correctness ∈ {0.0, 1.0} (binary in v1)
checklist_adherence  = fraction checked, or 0.5 when checklist is empty

Expected totals in these tests
-------------------------------
Correct decision, no checklist    → 0.7*1.0 + 0.3*0.5 = 0.85
Wrong decision,  no checklist     → 0.7*0.0 + 0.3*0.5 = 0.15
Correct decision, full checklist  → 0.7*1.0 + 0.3*1.0 = 1.00
Correct decision, 1/3 checklist   → 0.7*1.0 + 0.3*(1/3) ≈ 0.80
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.analytics.training import score_attempt
from app.models.training import (
    Attempt,
    AttemptAction,
    AttemptActionType,
    AnswerKey,
    DrillType,
)

UTC = timezone.utc
_TS = datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _action(bar: int, action: AttemptActionType, *,
            price: float | None = None, qty: float | None = None) -> AttemptAction:
    return AttemptAction(bar=bar, action=action, price=price, qty=qty, ts=_TS)


def _attempt(actions: list[AttemptAction], *, checklist: list[dict] | None = None) -> Attempt:
    """Build a minimal Attempt with the given actions (and optional checklist)."""
    a = Attempt(
        id=1,
        scenario_id=1,
        started_at=_TS,
        finished_at=None,
        actions=actions,
    )
    # Attach checklist_json as a dynamic attribute — the scorer reads it via
    # getattr(..., 'checklist_json', None) since Attempt doesn't declare it.
    if checklist is not None:
        object.__setattr__(a, "checklist_json", checklist)
    return a


def _scenario(
    *,
    drill_type: DrillType = DrillType.decision_point,
    decision_bar: int | None = 5,
    correct_actions: list[AttemptActionType] = None,
    management_notes: list[dict] | None = None,
) -> SimpleNamespace:
    """Duck-typed ScenarioFull — only the fields the scorer reads."""
    if correct_actions is None:
        correct_actions = [AttemptActionType.enter_long]
    answer_key = AnswerKey(
        correct_actions=correct_actions,
        explanation="test explanation",
        management_notes=management_notes or [],
    )
    return SimpleNamespace(
        drill_type=drill_type,
        decision_bar=decision_bar,
        answer_key=answer_key,
    )


# --------------------------------------------------------------------------- #
# Case 1: decision-point, correct entry, no checklist → 0.85                  #
# --------------------------------------------------------------------------- #

def test_decision_point_correct_entry_no_checklist():
    """Only enter_long at decision bar, correct_actions=[enter_long], no checklist."""
    attempt = _attempt([_action(5, AttemptActionType.enter_long)])
    scenario = _scenario(correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(0.85, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 2: decision-point, wrong entry → 0.15                                  #
# --------------------------------------------------------------------------- #

def test_decision_point_wrong_entry_no_checklist():
    """enter_short at decision bar when correct is enter_long → decision=0."""
    attempt = _attempt([_action(5, AttemptActionType.enter_short)])
    scenario = _scenario(correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(0.15, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 3: decision-point, skip is the correct action → 0.85                   #
# --------------------------------------------------------------------------- #

def test_decision_point_skip_when_skip_is_correct():
    """skip at decision bar, correct_actions=[skip] → decision=1.0."""
    attempt = _attempt([_action(5, AttemptActionType.skip)])
    scenario = _scenario(correct_actions=[AttemptActionType.skip])
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(0.85, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 4: wait at decision bar defers, next bar correct entry → 0.85           #
# --------------------------------------------------------------------------- #

def test_wait_at_decision_bar_defers_to_next_bar():
    """wait at bar 5, enter_long at bar 6 — enter_long is the terminal decision."""
    attempt = _attempt([
        _action(5, AttemptActionType.wait),
        _action(6, AttemptActionType.enter_long),
    ])
    scenario = _scenario(decision_bar=5, correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(0.85, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 5: do-nothing drill, no entry actions → 0.85                           #
# --------------------------------------------------------------------------- #

def test_do_nothing_drill_no_entries():
    """do-nothing with zero entry actions — user passed by sitting still."""
    attempt = _attempt([
        _action(3, AttemptActionType.wait),
        _action(4, AttemptActionType.skip),
    ])
    scenario = _scenario(
        drill_type=DrillType.do_nothing,
        correct_actions=[AttemptActionType.skip],
    )
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(0.85, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 6: do-nothing drill, one entry action → 0.15                            #
# --------------------------------------------------------------------------- #

def test_do_nothing_drill_with_entry_fails():
    """do-nothing drill with an enter_long anywhere → decision=0.0."""
    attempt = _attempt([
        _action(7, AttemptActionType.enter_long),
    ])
    scenario = _scenario(
        drill_type=DrillType.do_nothing,
        correct_actions=[AttemptActionType.skip],
    )
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(0.15, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 7: checklist boost — correct decision AND 3/3 items checked → 1.0      #
# --------------------------------------------------------------------------- #

def test_checklist_boost_full_completion():
    """Correct entry + all 3 checklist items checked → 0.7*1.0 + 0.3*1.0 = 1.0."""
    checklist = [
        {"id": 1, "text": "Rule A", "checked": True},
        {"id": 2, "text": "Rule B", "checked": True},
        {"id": 3, "text": "Rule C", "checked": True},
    ]
    attempt = _attempt(
        [_action(5, AttemptActionType.enter_long)],
        checklist=checklist,
    )
    scenario = _scenario(correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    assert result.process_score == pytest.approx(1.0, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 8: checklist penalty — correct decision AND 1/3 checked → ~0.80        #
# --------------------------------------------------------------------------- #

def test_checklist_partial_reduces_score():
    """Correct entry + 1/3 checklist items checked → 0.7*1.0 + 0.3*(1/3) ≈ 0.8."""
    checklist = [
        {"id": 1, "text": "Rule A", "checked": True},
        {"id": 2, "text": "Rule B", "checked": False},
        {"id": 3, "text": "Rule C", "checked": False},
    ]
    attempt = _attempt(
        [_action(5, AttemptActionType.enter_long)],
        checklist=checklist,
    )
    scenario = _scenario(correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    expected = 0.7 * 1.0 + 0.3 * (1 / 3)
    assert result.process_score == pytest.approx(expected, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 9: management-only with correct exit action → 1.0 decision sub-score   #
# --------------------------------------------------------------------------- #

def test_management_only_correct_exit():
    """management-only drill: exit action matching correct_actions → decision=1.0."""
    attempt = _attempt([
        _action(8, AttemptActionType.exit, price=15.0, qty=100),
    ])
    scenario = _scenario(
        drill_type=DrillType.management_only,
        correct_actions=[AttemptActionType.exit],
    )
    result = score_attempt(attempt, scenario)
    # decision=1.0, checklist=0.5 (no checklist) → 0.85
    assert result.process_score == pytest.approx(0.85, abs=1e-4)


# --------------------------------------------------------------------------- #
# Case 10: pathological inputs — empty actions, missing decision_bar → no crash
# --------------------------------------------------------------------------- #

def test_pathological_empty_actions_missing_decision_bar():
    """No actions, decision_bar=None → score must be in [0,1] with no exception."""
    attempt = _attempt([])
    scenario = _scenario(decision_bar=None, correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    # With decision_bar=None, _terminal_decision_action returns None → skip.
    # skip is not in correct_actions=[enter_long] → decision=0.0
    # checklist empty → 0.5
    # total = 0.7*0.0 + 0.3*0.5 = 0.15
    assert 0.0 <= result.process_score <= 1.0
    assert result.process_score == pytest.approx(0.15, abs=1e-4)


def test_pathological_all_wait_actions_scores_as_skip():
    """All actions are wait — tape ran out without a decision → treated as skip."""
    attempt = _attempt([
        _action(5, AttemptActionType.wait),
        _action(6, AttemptActionType.wait),
    ])
    scenario = _scenario(decision_bar=5, correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    # skip is not enter_long → decision=0.0 → total=0.15
    assert 0.0 <= result.process_score <= 1.0
    assert result.process_score == pytest.approx(0.15, abs=1e-4)


# --------------------------------------------------------------------------- #
# Ancillary: outcome_pnl is computed but NOT factored into process_score       #
# --------------------------------------------------------------------------- #

def test_outcome_pnl_computed_but_not_in_process_score():
    """Enter at 10.0, exit at 12.0, qty=100 → outcome_pnl=200. Score is unchanged."""
    attempt = _attempt([
        _action(5, AttemptActionType.enter_long, price=10.0, qty=100),
        _action(8, AttemptActionType.exit, price=12.0, qty=100),
    ])
    scenario = _scenario(correct_actions=[AttemptActionType.enter_long])
    result = score_attempt(attempt, scenario)
    # process_score same as case 1 (correct entry, no checklist)
    assert result.process_score == pytest.approx(0.85, abs=1e-4)
    # outcome_pnl is recorded
    assert result.outcome_pnl == pytest.approx(200.0, abs=1e-4)


def test_outcome_pnl_none_when_no_entry():
    """No entry action → outcome_pnl should be None (not 0.0 in the response)."""
    attempt = _attempt([_action(5, AttemptActionType.skip)])
    scenario = _scenario(correct_actions=[AttemptActionType.skip])
    result = score_attempt(attempt, scenario)
    assert result.outcome_pnl is None
