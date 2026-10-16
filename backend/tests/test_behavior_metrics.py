"""M6 task 6.3.5 — behavioral signal detectors from attempt.actions_json.

Tests exercise the three pure-function detectors in analytics/behavior.py:
    - detect_moved_stop   (long/short, loosened vs tightened, worst-case)
    - detect_hesitation   (multiple waits, long ts gap, no terminal action)
    - detect_revenge_size (multi-attempt: size up after a loss)

No HTTP, no database — all unit tests against pure functions.

Hand-computed fixture style matching test_training_scorer.py.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.analytics.behavior import (
    HesitationSignal,
    MovedStopSignal,
    RevengeSizeSignal,
    detect_hesitation,
    detect_moved_stop,
    detect_revenge_size,
)
from app.models.training import Attempt, AttemptAction, AttemptActionType

UTC = timezone.utc
_BASE_TS = datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _ts(offset_seconds: float = 0) -> datetime:
    """Return a UTC datetime offset by `offset_seconds` from _BASE_TS."""
    return _BASE_TS + timedelta(seconds=offset_seconds)


def _action(
    bar: int,
    action: AttemptActionType,
    *,
    price: float | None = None,
    qty: float | None = None,
    ts_offset: float = 0,
) -> AttemptAction:
    """Build an AttemptAction with an optional ts offset in seconds."""
    return AttemptAction(
        bar=bar,
        action=action,
        price=price,
        qty=qty,
        ts=_ts(ts_offset),
    )


def _attempt(
    attempt_id: int,
    actions: list[AttemptAction],
    *,
    process_score: float = 1.0,
    outcome_pnl: float = 0.0,
) -> Attempt:
    """Build a minimal Attempt with dynamic process_score/outcome_pnl attributes."""
    a = Attempt(
        id=attempt_id,
        scenario_id=1,
        started_at=_BASE_TS,
        finished_at=None,
        actions=actions,
    )
    # Attach scores as dynamic attributes — detect_revenge_size reads via getattr.
    object.__setattr__(a, "process_score", process_score)
    object.__setattr__(a, "outcome_pnl", outcome_pnl)
    return a


# --------------------------------------------------------------------------- #
# MOVED-STOP DETECTOR                                                          #
# --------------------------------------------------------------------------- #

# Test 1
def test_moved_stop_long_loosened_flagged():
    """Long trade: set_stop@12.30 then move_stop@12.20 (floor lowered = loosened).

    Expected: signal with delta=-0.10, direction='long'.
    """
    actions = [
        _action(3, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(4, AttemptActionType.set_stop, price=12.30),
        _action(5, AttemptActionType.move_stop, price=12.20),
    ]
    signal = detect_moved_stop(actions)

    assert signal is not None, "Expected a MovedStopSignal for loosened long stop"
    assert signal.direction == "long"
    assert signal.initial_price == pytest.approx(12.30, abs=1e-6)
    assert signal.moved_price == pytest.approx(12.20, abs=1e-6)
    assert signal.delta == pytest.approx(-0.10, abs=1e-6), (
        f"delta should be -0.10 (loosened long: floor lowered), got {signal.delta}"
    )


# Test 2
def test_moved_stop_long_tightened_not_flagged():
    """Long trade: set_stop@12.30, move_stop@12.35 (floor raised = tightened, GOOD).

    Expected: None — tightening a stop is disciplined risk management.
    """
    actions = [
        _action(3, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(4, AttemptActionType.set_stop, price=12.30),
        _action(5, AttemptActionType.move_stop, price=12.35),
    ]
    signal = detect_moved_stop(actions)
    assert signal is None, (
        f"Tightening a long stop should not signal, got {signal}"
    )


# Test 3
def test_moved_stop_short_loosened_flagged():
    """Short trade: set_stop@12.50, move_stop@12.65 (ceiling raised = loosened).

    Expected: signal with delta=+0.15, direction='short'.
    """
    actions = [
        _action(3, AttemptActionType.enter_short, price=12.30, qty=100),
        _action(4, AttemptActionType.set_stop, price=12.50),
        _action(5, AttemptActionType.move_stop, price=12.65),
    ]
    signal = detect_moved_stop(actions)

    assert signal is not None, "Expected a MovedStopSignal for loosened short stop"
    assert signal.direction == "short"
    assert signal.initial_price == pytest.approx(12.50, abs=1e-6)
    assert signal.moved_price == pytest.approx(12.65, abs=1e-6)
    assert signal.delta == pytest.approx(0.15, abs=1e-6), (
        f"delta should be +0.15 (loosened short: ceiling raised), got {signal.delta}"
    )


# Test 4
def test_moved_stop_short_tightened_not_flagged():
    """Short trade: set_stop@12.50, move_stop@12.40 (ceiling lowered = tightened, GOOD).

    Expected: None.
    """
    actions = [
        _action(3, AttemptActionType.enter_short, price=12.30, qty=100),
        _action(4, AttemptActionType.set_stop, price=12.50),
        _action(5, AttemptActionType.move_stop, price=12.40),
    ]
    signal = detect_moved_stop(actions)
    assert signal is None, (
        f"Tightening a short stop should not signal, got {signal}"
    )


# Test 5
def test_no_set_stop_no_signal():
    """Only entry+exit actions (no set_stop at all) → None.

    Can't detect a move if no stop was ever established.
    """
    actions = [
        _action(3, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(8, AttemptActionType.exit, price=12.80, qty=100),
    ]
    signal = detect_moved_stop(actions)
    assert signal is None, (
        f"No set_stop → no signal possible, got {signal}"
    )


# Test 6
def test_move_stop_without_prior_set_ignored():
    """move_stop alone (no preceding set_stop) → None (defensive / malformed input)."""
    actions = [
        _action(3, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(4, AttemptActionType.move_stop, price=12.20),  # no prior set_stop
    ]
    signal = detect_moved_stop(actions)
    assert signal is None, (
        f"move_stop without prior set_stop should return None (malformed), got {signal}"
    )


# Test 7
def test_multiple_move_stops_uses_the_worst_case():
    """Long: set_stop@12.30, then move_stop@12.25 (loosened), then move_stop@12.20 (worse).

    Expected: signal reports moved_price=12.20 (worst), delta=-0.10.
    The first loosening (to 12.25) is intermediate — we want the worst of all loosened moves.
    """
    actions = [
        _action(3, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(4, AttemptActionType.set_stop, price=12.30),
        _action(5, AttemptActionType.move_stop, price=12.25, ts_offset=1),  # loosened
        _action(6, AttemptActionType.move_stop, price=12.20, ts_offset=2),  # even worse
    ]
    signal = detect_moved_stop(actions)

    assert signal is not None, "Expected a signal for multiple loosened moves"
    assert signal.moved_price == pytest.approx(12.20, abs=1e-6), (
        f"Expected worst-case moved_price=12.20, got {signal.moved_price}"
    )
    assert signal.delta == pytest.approx(-0.10, abs=1e-6), (
        f"Expected delta=-0.10 (12.20 - 12.30), got {signal.delta}"
    )


# --------------------------------------------------------------------------- #
# HESITATION DETECTOR                                                          #
# --------------------------------------------------------------------------- #

# Test 8
def test_no_hesitation_immediate_decision():
    """Single enter_long at decision_bar with ts within 3s of first action → None.

    Decisive action: no waits, small ts gap.
    """
    actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=100, ts_offset=1),
    ]
    signal = detect_hesitation(actions, decision_bar=5)
    assert signal is None, (
        f"Immediate entry with no waits and tiny ts gap should not signal, got {signal}"
    )


# Test 9
def test_multiple_waits_flagged():
    """2 wait actions at decision_bar and decision_bar+1, then enter → signal with wait_count=2."""
    actions = [
        _action(5, AttemptActionType.wait, ts_offset=0),
        _action(6, AttemptActionType.wait, ts_offset=2),
        _action(7, AttemptActionType.enter_long, price=12.50, qty=100, ts_offset=4),
    ]
    signal = detect_hesitation(actions, decision_bar=5)

    assert signal is not None, "2 wait actions at decision_bar should trigger hesitation"
    assert signal.wait_count == 2, (
        f"Expected wait_count=2, got {signal.wait_count}"
    )
    assert signal.terminal_action == AttemptActionType.enter_long


# Test 10
def test_long_ts_gap_flagged():
    """Single enter_long at decision_bar but ts is 45s after the previous action → signal."""
    actions = [
        _action(5, AttemptActionType.wait, ts_offset=0),   # first action at decision_bar
        _action(5, AttemptActionType.enter_long, price=12.50, qty=100, ts_offset=45),
    ]
    signal = detect_hesitation(actions, decision_bar=5)

    assert signal is not None, "45s ts gap at decision_bar should trigger hesitation"
    assert signal.ts_gap_seconds == pytest.approx(45.0, abs=0.5), (
        f"Expected ts_gap_seconds ≈ 45.0, got {signal.ts_gap_seconds}"
    )


# Test 11
def test_no_terminal_action_flagged():
    """All wait actions (no commit) → signal with terminal_action=None."""
    actions = [
        _action(5, AttemptActionType.wait, ts_offset=0),
        _action(6, AttemptActionType.wait, ts_offset=3),
        _action(7, AttemptActionType.wait, ts_offset=6),
    ]
    signal = detect_hesitation(actions, decision_bar=5)

    assert signal is not None, "All waits with no commit should trigger hesitation"
    assert signal.terminal_action is None, (
        f"Expected terminal_action=None (user never committed), got {signal.terminal_action}"
    )
    assert signal.wait_count == 3


# --------------------------------------------------------------------------- #
# REVENGE-SIZE DETECTOR (multi-attempt)                                        #
# --------------------------------------------------------------------------- #

# Test 12
def test_revenge_after_loss_flagged():
    """Attempt N-1: scored 0.15, net_pnl=-100, qty=100.
    Attempt N: qty=200.

    Expected: signal with ratio=2.0.
    """
    prev_actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(8, AttemptActionType.exit, price=12.30, qty=100),
    ]
    curr_actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=200),
        _action(8, AttemptActionType.exit, price=12.70, qty=200),
    ]
    attempts = [
        _attempt(1, prev_actions, process_score=0.15, outcome_pnl=-100.0),
        _attempt(2, curr_actions, process_score=0.85, outcome_pnl=400.0),
    ]
    signal = detect_revenge_size(attempts)

    assert signal is not None, "Revenge sizing after a loss should trigger signal"
    assert signal.ratio == pytest.approx(2.0, abs=1e-6), (
        f"Expected ratio=2.0 (200/100), got {signal.ratio}"
    )
    assert signal.previous_attempt_id == 1
    assert signal.current_attempt_id == 2
    assert signal.previous_qty == pytest.approx(100.0)
    assert signal.current_qty == pytest.approx(200.0)


# Test 13
def test_no_revenge_after_win():
    """Attempt N-1: scored 0.85 (correct decision, positive pnl).
    Attempt N: qty=200.

    Sizing up after a WIN is fine — no signal.
    """
    prev_actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(8, AttemptActionType.exit, price=12.80, qty=100),
    ]
    curr_actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=200),
    ]
    attempts = [
        _attempt(1, prev_actions, process_score=0.85, outcome_pnl=300.0),
        _attempt(2, curr_actions, process_score=0.85, outcome_pnl=200.0),
    ]
    signal = detect_revenge_size(attempts)
    assert signal is None, (
        f"Sizing up after a win should not signal, got {signal}"
    )


# Test 14
def test_no_revenge_when_qty_similar():
    """Attempt N-1: lost with qty=100. Attempt N: qty=110 (< 1.5x threshold).

    Small size variations are noise — no signal.
    """
    prev_actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=100),
        _action(8, AttemptActionType.exit, price=12.30, qty=100),
    ]
    curr_actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=110),
    ]
    attempts = [
        _attempt(1, prev_actions, process_score=0.15, outcome_pnl=-100.0),
        _attempt(2, curr_actions, process_score=0.85, outcome_pnl=50.0),
    ]
    signal = detect_revenge_size(attempts)
    assert signal is None, (
        f"110/100 = 1.1x is below the 1.5x threshold — should not signal, got {signal}"
    )


# Test 15
def test_first_attempt_no_signal():
    """Single-attempt list → None (no previous attempt to compare)."""
    actions = [
        _action(5, AttemptActionType.enter_long, price=12.50, qty=100),
    ]
    attempts = [_attempt(1, actions, process_score=0.15, outcome_pnl=-100.0)]
    signal = detect_revenge_size(attempts)
    assert signal is None, (
        f"Single attempt has no prior to compare against — should return None, got {signal}"
    )


# Test 16
def test_revenge_only_uses_last_losing_attempt():
    """N-2: winning (score=0.85), N-1: losing (score=0.15), N: upsized.

    The winning attempt N-2 should NOT invalidate the pattern at N-1 → N.
    Signal should fire comparing N against N-1 (losing).
    """
    n2_actions = [_action(5, AttemptActionType.enter_long, price=12.50, qty=100)]
    n1_actions = [_action(5, AttemptActionType.enter_long, price=12.50, qty=100)]
    n_actions  = [_action(5, AttemptActionType.enter_long, price=12.50, qty=200)]

    attempts = [
        _attempt(1, n2_actions, process_score=0.85, outcome_pnl=200.0),   # win
        _attempt(2, n1_actions, process_score=0.15, outcome_pnl=-100.0),  # loss
        _attempt(3, n_actions,  process_score=0.85, outcome_pnl=300.0),   # up-sized
    ]
    signal = detect_revenge_size(attempts)

    assert signal is not None, (
        "N-1 was a loss and N is 2x the size — signal should fire"
    )
    assert signal.previous_attempt_id == 2
    assert signal.current_attempt_id == 3
    assert signal.ratio == pytest.approx(2.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# INTEGRATION SMOKE                                                             #
# --------------------------------------------------------------------------- #

# Test 17
def test_behavior_signals_on_seeded_leak_fixture():
    """End-to-end seeded fixture: revenge trading after 2 losses.

    Scenario:
      - 09:30 — attempt 1: loss (score=0.15, pnl=-50, qty=100)
      - 09:45 — attempt 2: loss (score=0.15, pnl=-75, qty=100)
      - 10:00 — attempt 3: enters with qty=300 (3x the prior size)

    detect_revenge_size must fire; this fixture is the acceptance criterion
    the leak detector (6.3.3) will use.
    """
    t0 = datetime(2026, 7, 9, 9, 30, 0, tzinfo=UTC)
    t1 = datetime(2026, 7, 9, 9, 45, 0, tzinfo=UTC)
    t2 = datetime(2026, 7, 9, 10, 0, 0, tzinfo=UTC)

    def _ts_at(dt: datetime) -> datetime:
        return dt

    attempt1_actions = [
        AttemptAction(bar=5, action=AttemptActionType.enter_long, price=12.50, qty=100, ts=t0),
        AttemptAction(bar=8, action=AttemptActionType.exit, price=12.30, qty=100, ts=t0 + timedelta(minutes=10)),
    ]
    attempt2_actions = [
        AttemptAction(bar=5, action=AttemptActionType.enter_long, price=12.60, qty=100, ts=t1),
        AttemptAction(bar=8, action=AttemptActionType.exit, price=12.40, qty=100, ts=t1 + timedelta(minutes=10)),
    ]
    attempt3_actions = [
        AttemptAction(bar=5, action=AttemptActionType.enter_long, price=12.55, qty=300, ts=t2),
        AttemptAction(bar=8, action=AttemptActionType.exit, price=12.75, qty=300, ts=t2 + timedelta(minutes=10)),
    ]

    def _make_attempt(aid, actions, score, pnl):
        a = Attempt(id=aid, scenario_id=1, started_at=actions[0].ts, actions=actions)
        object.__setattr__(a, "process_score", score)
        object.__setattr__(a, "outcome_pnl", pnl)
        return a

    attempts = [
        _make_attempt(10, attempt1_actions, 0.15, -50.0),
        _make_attempt(11, attempt2_actions, 0.15, -75.0),
        _make_attempt(12, attempt3_actions, 0.85, 600.0),
    ]

    signal = detect_revenge_size(attempts)

    assert signal is not None, (
        "Seeded leak fixture (3x size after 2 losses) must trigger a RevengeSizeSignal"
    )
    assert signal.ratio == pytest.approx(3.0, abs=1e-6), (
        f"Expected ratio=3.0 (300/100), got {signal.ratio}"
    )
    assert signal.current_attempt_id == 12
    # The signal should compare against attempt 11 (the most recent loss).
    assert signal.previous_attempt_id == 11


# Test 18
def test_behavior_signals_return_serializable_dicts():
    """Each signal type has a to_dict() method and JSON-round-trips cleanly.

    The leak detector (6.3.3) will emit these to HTTP — JSON-serializability
    is therefore a hard requirement.
    """
    ts = datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC)

    # MovedStopSignal
    stop_signal = MovedStopSignal(
        initial_price=12.30,
        moved_price=12.20,
        delta=-0.10,
        direction="long",
        ts=ts,
    )
    stop_dict = stop_signal.to_dict()
    assert isinstance(stop_dict, dict)
    stop_json = json.dumps(stop_dict)   # must not raise
    stop_rt = json.loads(stop_json)
    assert stop_rt["initial_price"] == pytest.approx(12.30, abs=1e-6)
    assert stop_rt["delta"] == pytest.approx(-0.10, abs=1e-6)
    assert stop_rt["direction"] == "long"
    assert isinstance(stop_rt["ts"], str), "ts must be serialized as a string"

    # HesitationSignal
    hes_signal = HesitationSignal(
        wait_count=2,
        terminal_action=AttemptActionType.enter_long,
        ts_gap_seconds=45.0,
    )
    hes_dict = hes_signal.to_dict()
    assert isinstance(hes_dict, dict)
    hes_json = json.dumps(hes_dict)     # must not raise
    hes_rt = json.loads(hes_json)
    assert hes_rt["wait_count"] == 2
    assert hes_rt["ts_gap_seconds"] == pytest.approx(45.0, abs=1e-6)

    # RevengeSizeSignal
    rev_signal = RevengeSizeSignal(
        previous_attempt_id=1,
        current_attempt_id=2,
        previous_qty=100.0,
        current_qty=200.0,
        ratio=2.0,
    )
    rev_dict = rev_signal.to_dict()
    assert isinstance(rev_dict, dict)
    rev_json = json.dumps(rev_dict)     # must not raise
    rev_rt = json.loads(rev_json)
    assert rev_rt["ratio"] == pytest.approx(2.0, abs=1e-6)
    assert rev_rt["previous_attempt_id"] == 1
    assert rev_rt["current_attempt_id"] == 2

    # Also verify dataclasses.asdict works (alternate serialization path).
    assert dataclasses.asdict(stop_signal)["initial_price"] == pytest.approx(12.30, abs=1e-6)
    assert dataclasses.asdict(rev_signal)["ratio"] == pytest.approx(2.0, abs=1e-6)
