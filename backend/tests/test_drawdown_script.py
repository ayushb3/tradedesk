"""Hand-computed fixture tests for the drawdown-script scorer (M6 task 6.3.2).

The drawdown-script drill is a multi-session engineered losing streak.  A
single Attempt covers N sub-sessions using the bar // 1000 encoding:

    session_index = bar // 1000   (0, 1, 2, …)
    local_bar     = bar % 1000    (0..999 within each session)

Sessions 0, 1, 2 occupy bars 0..999, 1000..1999, 2000..2999 respectively.
This test file enforces and documents that convention.

Formula reminder
----------------
process_score = clamp(0.7 * decision_correctness + 0.3 * checklist_adherence)

decision_correctness for drawdown-script:
    = average(1.0 if terminal_decision == correct_action else 0.0, over sessions)
    Absent actions in a session → treated as skip.

checklist_adherence = fraction checked, or 0.5 when checklist is empty.

Expected totals in these tests (all with empty checklist → adherence=0.5)
--------------------------------------------------------------------------
All 3 sessions correct  → 0.7*1.0 + 0.3*0.5 = 0.85
All 3 sessions wrong    → 0.7*0.0 + 0.3*0.5 = 0.15
2 correct / 1 wrong     → 0.7*(2/3) + 0.3*0.5 = 0.4667 + 0.15 ≈ 0.6167
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

# Drawdown-script answer key: 3 sessions, all correct = skip.
# decision_bar values (in local-bar space) match the bar % 1000 values used below.
_DRAWDOWN_NOTES_3S = [
    {"session": 0, "decision_bar": 12, "correct_action": "skip"},
    {"session": 1, "decision_bar": 30, "correct_action": "skip"},
    {"session": 2, "decision_bar": 12, "correct_action": "skip"},
]

# Global bars for each session's decision event (bar // 1000 * 1000 + local_bar).
_SESSION0_DBAR = 0 * 1000 + 12   # = 12
_SESSION1_DBAR = 1 * 1000 + 30   # = 1030
_SESSION2_DBAR = 2 * 1000 + 12   # = 2012


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _action(
    bar: int,
    action: AttemptActionType,
    *,
    price: float | None = None,
    qty: float | None = None,
) -> AttemptAction:
    return AttemptAction(bar=bar, action=action, price=price, qty=qty, ts=_TS)


def _attempt(
    actions: list[AttemptAction],
    *,
    checklist: list[dict] | None = None,
) -> Attempt:
    a = Attempt(
        id=1,
        scenario_id=1,
        started_at=_TS,
        finished_at=None,
        actions=actions,
    )
    if checklist is not None:
        object.__setattr__(a, "checklist_json", checklist)
    return a


def _drawdown_scenario(
    *,
    management_notes: list[dict] | None = None,
) -> SimpleNamespace:
    """Duck-typed ScenarioFull for a 3-session drawdown-script drill."""
    notes = management_notes if management_notes is not None else _DRAWDOWN_NOTES_3S
    return SimpleNamespace(
        drill_type=DrillType.drawdown_script,
        decision_bar=None,   # drawdown-script has no single decision_bar
        answer_key=AnswerKey(
            correct_actions=[AttemptActionType.skip],  # summary-level; per-session in notes
            explanation="Engineered losing streak — skip all sessions.",
            management_notes=notes,
        ),
    )


# --------------------------------------------------------------------------- #
# Test 1: all 3 sessions correct (skip) → process_score = 0.85               #
# --------------------------------------------------------------------------- #

def test_drawdown_script_scorer_averages_per_session_scores() -> None:
    """User skips at the decision bar in every session → decision_correctness=1.0."""
    attempt = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.skip),
        _action(_SESSION1_DBAR, AttemptActionType.skip),
        _action(_SESSION2_DBAR, AttemptActionType.skip),
    ])
    result = score_attempt(attempt, _drawdown_scenario())

    # decision=1.0, checklist=0.5 → 0.7*1.0 + 0.3*0.5 = 0.85
    assert result.process_score == pytest.approx(0.85, abs=1e-4)


# --------------------------------------------------------------------------- #
# Test 2: all 3 sessions wrong (enter_long) → process_score = 0.15           #
# --------------------------------------------------------------------------- #

def test_drawdown_script_all_wrong_scores_low() -> None:
    """User enters long in every session when correct is skip → decision=0.0."""
    attempt = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION1_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION2_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
    ])
    result = score_attempt(attempt, _drawdown_scenario())

    # decision=0.0, checklist=0.5 → 0.7*0.0 + 0.3*0.5 = 0.15
    assert result.process_score == pytest.approx(0.15, abs=1e-4)
    assert result.process_score < 0.3


# --------------------------------------------------------------------------- #
# Test 3: 2 correct / 1 wrong → process_score between 0.3 and 0.8            #
# --------------------------------------------------------------------------- #

def test_drawdown_script_mixed_partial_credit() -> None:
    """Sessions 0 and 2 skipped correctly, session 1 traded incorrectly."""
    attempt = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.skip),
        _action(_SESSION1_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION2_DBAR, AttemptActionType.skip),
    ])
    result = score_attempt(attempt, _drawdown_scenario())

    # decision = (1+0+1)/3 = 2/3
    expected = 0.7 * (2 / 3) + 0.3 * 0.5
    assert result.process_score == pytest.approx(expected, abs=1e-4)
    assert 0.3 < result.process_score < 0.8


# --------------------------------------------------------------------------- #
# Test 4: anti-gambling — high score despite negative outcome_pnl             #
# --------------------------------------------------------------------------- #

def test_drawdown_script_high_score_despite_negative_pnl() -> None:
    """All sessions followed rules (skip).  Fabricated exit fills produce
    negative outcome_pnl.  process_score must still be ~0.85.

    This is the core anti-gambling regression test for the drawdown-script
    variant.  Failing it would allow the process score to leak P&L information
    (PLAN-M6-TRAINING.md §0 principle 1).
    """
    # Skip decisions (correct) at each session's decision bar.
    # Also include entry+exit fills that produce a net negative P&L.
    # These fills simulate a scenario where the user did enter (hypothetically)
    # and lost — but the *decision* recorded is still skip at the bar the scorer
    # checks.  In real play the frontend would not submit fills after a skip, but
    # the scorer must be robust: even if exit fills appear in the log, the
    # process score is keyed on the terminal action at decision_bar, not on fills.
    attempt = _attempt([
        # Session 0: skip at decision bar (correct) + hypothetical losing fill
        _action(_SESSION0_DBAR, AttemptActionType.skip),
        _action(_SESSION0_DBAR + 1, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION0_DBAR + 5, AttemptActionType.exit, price=98.0, qty=100),
        # Session 1: skip (correct)
        _action(_SESSION1_DBAR, AttemptActionType.skip),
        # Session 2: skip (correct) + another losing fill
        _action(_SESSION2_DBAR, AttemptActionType.skip),
        _action(_SESSION2_DBAR + 2, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION2_DBAR + 8, AttemptActionType.exit, price=97.5, qty=100),
    ])
    result = score_attempt(attempt, _drawdown_scenario())

    # The terminal action at each decision bar is skip → decision=1.0 per session.
    # process_score must be the same as all-correct (0.85) regardless of fills.
    assert result.process_score == pytest.approx(0.85, abs=1e-4)

    # outcome_pnl is negative (the fills produced a loss).
    # Confirm it is recorded (not None / not zero) and is indeed negative.
    assert result.outcome_pnl is not None
    assert result.outcome_pnl < 0.0


# --------------------------------------------------------------------------- #
# Test 5: size-jumping does NOT affect process_score                          #
# --------------------------------------------------------------------------- #

def test_drawdown_script_size_jumping_flagged_but_not_scored() -> None:
    """Session 1: enter_long with 1× qty (baseline wrong answer).
    Session 2: enter_long with 2× qty (size-jump after a loss).
    Both are wrong decisions — the process score should be the same regardless
    of qty, because qty is not an input to the decision scoring.

    The revenge-size *detector* (6.3.5) will flag the pattern separately; this
    test asserts the scorer itself is qty-agnostic.
    """
    # Baseline: session 0 wrong, session 1 wrong (1× qty), session 2 wrong (1× qty)
    attempt_baseline = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION1_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION2_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
    ])
    # Size-jump: session 0 wrong (1× qty), session 1 right (skip), session 2 wrong
    # with 2× qty.  Correct actions are all skip.
    attempt_sized = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION1_DBAR, AttemptActionType.skip),
        _action(_SESSION2_DBAR, AttemptActionType.enter_long, price=100.0, qty=200),  # 2× size
    ])

    sc = _drawdown_scenario()
    result_baseline = score_attempt(attempt_baseline, sc)
    result_sized = score_attempt(attempt_sized, sc)

    # Baseline: all 3 wrong → decision=0.0 → score=0.15
    assert result_baseline.process_score == pytest.approx(0.15, abs=1e-4)

    # Size-jump: sessions 0 and 2 wrong, session 1 correct → decision=1/3
    expected_sized = 0.7 * (1 / 3) + 0.3 * 0.5
    assert result_sized.process_score == pytest.approx(expected_sized, abs=1e-4)

    # Key assertion: the up-sized position (qty=200 vs qty=100) does NOT change
    # the score relative to a same-decision attempt with normal qty.
    attempt_same_decision_normal_qty = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(_SESSION1_DBAR, AttemptActionType.skip),
        _action(_SESSION2_DBAR, AttemptActionType.enter_long, price=100.0, qty=100),
    ])
    result_normal_qty = score_attempt(attempt_same_decision_normal_qty, sc)
    assert result_sized.process_score == pytest.approx(result_normal_qty.process_score, abs=1e-6)


# --------------------------------------------------------------------------- #
# Test 6: empty actions_json → treated as skip in every session               #
# --------------------------------------------------------------------------- #

def test_drawdown_script_zero_actions_scores_as_all_skips() -> None:
    """Empty actions_json → every session is treated as skip (no action = skip).

    When the correct action for all sessions is skip, an empty attempt should
    score the same as an attempt with explicit skip actions in each session.
    """
    attempt_empty = _attempt([])
    attempt_explicit_skips = _attempt([
        _action(_SESSION0_DBAR, AttemptActionType.skip),
        _action(_SESSION1_DBAR, AttemptActionType.skip),
        _action(_SESSION2_DBAR, AttemptActionType.skip),
    ])
    sc = _drawdown_scenario()
    result_empty = score_attempt(attempt_empty, sc)
    result_explicit = score_attempt(attempt_explicit_skips, sc)

    # Both should score 0.85 — correct answer is skip everywhere.
    assert result_empty.process_score == pytest.approx(0.85, abs=1e-4)
    assert result_empty.process_score == pytest.approx(result_explicit.process_score, abs=1e-6)


# --------------------------------------------------------------------------- #
# Test 7: bar // 1000 convention is the canonical session splitter             #
# --------------------------------------------------------------------------- #

def test_drawdown_script_bar_key_convention_documented() -> None:
    """Sanity test: bar // 1000 is the session index, bar % 1000 is local bar.

    This test explicitly encodes the convention so any future change that
    silently breaks the session-splitting logic will cause a clear failure
    here rather than a mysterious scoring regression.

    The test constructs actions where:
    - bar=12   → session 0, local_bar=12  (within session 0's decision_bar=12)
    - bar=1030 → session 1, local_bar=30  (within session 1's decision_bar=30)
    - bar=2012 → session 2, local_bar=12  (within session 2's decision_bar=12)
    """
    for global_bar, expected_session, expected_local in [
        (12,   0, 12),
        (1030, 1, 30),
        (2012, 2, 12),
    ]:
        assert global_bar // 1000 == expected_session, (
            f"bar={global_bar}: expected session {expected_session}, "
            f"got {global_bar // 1000}"
        )
        assert global_bar % 1000 == expected_local, (
            f"bar={global_bar}: expected local_bar {expected_local}, "
            f"got {global_bar % 1000}"
        )

    # End-to-end: action at bar=12 is skip → session 0 scores correct (decision_bar=12).
    # action at bar=1030 is enter_long → session 1 scores wrong (decision_bar=30).
    # action at bar=2012 is skip → session 2 scores correct (decision_bar=12).
    attempt = _attempt([
        _action(12,   AttemptActionType.skip),
        _action(1030, AttemptActionType.enter_long, price=100.0, qty=100),
        _action(2012, AttemptActionType.skip),
    ])
    result = score_attempt(attempt, _drawdown_scenario())

    # 2 correct sessions out of 3 → decision = 2/3
    expected = 0.7 * (2 / 3) + 0.3 * 0.5
    assert result.process_score == pytest.approx(expected, abs=1e-4)
