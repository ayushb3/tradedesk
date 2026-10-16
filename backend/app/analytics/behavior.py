"""Behavioral-signal detectors for training-module drill attempts (M6 task 6.3.5).

Public API
----------
detect_moved_stop(actions)   -> MovedStopSignal | None
detect_hesitation(actions, decision_bar) -> HesitationSignal | None
detect_revenge_size(attempts) -> RevengeSizeSignal | None

Design contract
---------------
* All functions are PURE — no I/O, no time.time(), no side effects.
* Inputs are the persisted event log (list[AttemptAction]) or a list of
  Attempt objects in time order.
* The leak detector (task 6.3.3) will call these functions and emit the
  signal dicts over HTTP — each signal type therefore has a to_dict()
  method (or dataclasses.asdict works).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from app.models.training import Attempt, AttemptAction, AttemptActionType

if TYPE_CHECKING:
    pass


# --------------------------------------------------------------------------- #
# Signal types (frozen dataclasses — hashable, JSON-serializable via asdict)  #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MovedStopSignal:
    """Emitted when the user loosened a stop (bad direction).

    direction='long'  → loosened means moved_price < initial_price (lower floor).
    direction='short' → loosened means moved_price > initial_price (higher ceiling).

    delta is signed: negative = tightened (good), positive = loosened (bad).
    """
    initial_price: float
    moved_price: float
    delta: float          # signed: moved_price - initial_price
    direction: Literal["long", "short"]
    ts: datetime          # when the worst loosening move happened

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


@dataclass(frozen=True)
class HesitationSignal:
    """Emitted when the user showed excessive hesitation at the decision bar.

    wait_count: number of explicit wait actions in the log.
    terminal_action: the first non-wait action taken (None if user never committed).
    ts_gap_seconds: wall-clock gap between the first action at decision_bar
        and the terminal action (or last action if no terminal).
    """
    wait_count: int
    terminal_action: AttemptActionType | None
    ts_gap_seconds: float

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        if d["terminal_action"] is not None:
            d["terminal_action"] = d["terminal_action"]
        return d


@dataclass(frozen=True)
class RevengeSizeSignal:
    """Emitted when the user sized up after a losing attempt (revenge trading).

    Triggered when: attempt N-1 had process_score < 0.5 AND outcome_pnl < 0,
    and attempt N's entry qty is > 1.5x attempt N-1's entry qty.
    """
    previous_attempt_id: int
    current_attempt_id: int
    previous_qty: float
    current_qty: float
    ratio: float          # current_qty / previous_qty

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _entry_qty_from_actions(actions: list[AttemptAction]) -> float | None:
    """Return the qty from the first entry action (long or short), or None."""
    for a in actions:
        if a.action in (AttemptActionType.enter_long, AttemptActionType.enter_short):
            return a.qty
    return None


def _entry_direction_from_actions(
    actions: list[AttemptAction],
) -> Literal["long", "short"] | None:
    """Return 'long' or 'short' from the first entry action, or None."""
    for a in actions:
        if a.action == AttemptActionType.enter_long:
            return "long"
        if a.action == AttemptActionType.enter_short:
            return "short"
    return None


# --------------------------------------------------------------------------- #
# Moved-stop detector                                                          #
# --------------------------------------------------------------------------- #

def detect_moved_stop(actions: list[AttemptAction]) -> MovedStopSignal | None:
    """Detect whether the user set a stop then moved it in a losing direction.

    Long: initial set_stop at S1, then move_stop to S2 < S1 (loosened, floor lowered).
    Short: initial set_stop at S1, then move_stop to S2 > S1 (loosened, ceiling raised).

    When multiple move_stop actions exist, reports the worst-case (most loosened)
    move relative to the initial set_stop.

    Returns MovedStopSignal with initial_price, moved_price, delta, direction, ts,
    or None if no set_stop → move_stop sequence in a bad direction was found.

    Edge cases:
    - move_stop without a prior set_stop: ignored (malformed input).
    - set_stop with no subsequent move_stop: None (no movement to detect).
    - ts missing on an action would fail Pydantic validation upstream — ts is
      required on AttemptAction, so we rely on the model to enforce it.
    - actions are used in submission order (not bar order), consistent with the
      replayable-event-log invariant (test_attempt_event_log.py test 3).
    """
    initial_stop: float | None = None
    initial_stop_ts: datetime | None = None
    direction: Literal["long", "short"] | None = None

    # Determine trade direction from the first entry action in the log.
    direction = _entry_direction_from_actions(actions)
    # If we can't determine direction from entry, use set_stop alone (assume long).
    # The caller is responsible for context; we default to long for ambiguous logs.

    # Find the first set_stop.
    first_set_idx: int | None = None
    for i, a in enumerate(actions):
        if a.action == AttemptActionType.set_stop and a.price is not None:
            initial_stop = a.price
            initial_stop_ts = a.ts
            first_set_idx = i
            break

    if initial_stop is None or first_set_idx is None:
        # No set_stop at all — nothing to move.
        return None

    # If direction still unknown, default to long.
    if direction is None:
        direction = "long"

    # Scan subsequent move_stop actions for the worst-case loosening.
    worst_moved_price: float | None = None
    worst_ts: datetime | None = None

    for a in actions[first_set_idx + 1:]:
        if a.action == AttemptActionType.move_stop and a.price is not None:
            candidate = a.price
            delta = candidate - initial_stop  # signed

            # Is this loosened for the given direction?
            if direction == "long":
                # Loosened = moved_price < initial_price (delta < 0)
                is_loosened = candidate < initial_stop
            else:
                # Loosened = moved_price > initial_price (delta > 0)
                is_loosened = candidate > initial_stop

            if is_loosened:
                # Track the worst case (most loosened from initial_stop).
                if worst_moved_price is None:
                    worst_moved_price = candidate
                    worst_ts = a.ts
                else:
                    if direction == "long":
                        # Worst = lowest (most loosened = furthest below initial)
                        if candidate < worst_moved_price:
                            worst_moved_price = candidate
                            worst_ts = a.ts
                    else:
                        # Worst = highest (most loosened = furthest above initial)
                        if candidate > worst_moved_price:
                            worst_moved_price = candidate
                            worst_ts = a.ts

    if worst_moved_price is None:
        # Either no move_stop actions, or all moves tightened the stop (good).
        return None

    delta = worst_moved_price - initial_stop
    return MovedStopSignal(
        initial_price=initial_stop,
        moved_price=worst_moved_price,
        delta=delta,
        direction=direction,
        ts=worst_ts,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# Hesitation detector                                                          #
# --------------------------------------------------------------------------- #

def detect_hesitation(
    actions: list[AttemptAction],
    decision_bar: int,
) -> HesitationSignal | None:
    """Detect hesitation at the decision bar.

    Signals:
    - Multiple 'wait' actions at consecutive bars starting at decision_bar.
    - A long ts gap (>20s) between the first action at/after decision_bar
      and the terminal (non-wait) action.

    No signal (None) when:
    - User acted decisively (no waits, ts gap < 5s).

    Terminal action: first non-wait action at/after decision_bar.

    Edge case: if actions are out of bar order (submission order is preserved),
    we filter by bar >= decision_bar and use submission order within that set.
    This respects the event-log invariant.
    """
    # Collect all actions at/after decision_bar in submission order.
    relevant = [a for a in actions if a.bar >= decision_bar]

    if not relevant:
        return None

    # Count wait actions.
    wait_count = sum(1 for a in relevant if a.action == AttemptActionType.wait)

    # Find terminal action (first non-wait).
    terminal: AttemptActionType | None = None
    terminal_ts: datetime | None = None
    for a in relevant:
        if a.action != AttemptActionType.wait:
            terminal = a.action
            terminal_ts = a.ts
            break

    # Compute ts gap: from the first action at decision_bar to the terminal action.
    first_ts = relevant[0].ts
    if terminal_ts is not None:
        gap_seconds = (terminal_ts - first_ts).total_seconds()
    else:
        # No terminal action — gap to the last action in the log.
        last_ts = relevant[-1].ts
        gap_seconds = (last_ts - first_ts).total_seconds()

    # Determine whether to signal.
    has_waits = wait_count > 0
    has_long_gap = gap_seconds > 20.0

    if not has_waits and not has_long_gap:
        return None

    # All-waits with no terminal action is always a signal.
    return HesitationSignal(
        wait_count=wait_count,
        terminal_action=terminal,
        ts_gap_seconds=gap_seconds,
    )


# --------------------------------------------------------------------------- #
# Revenge-size detector                                                        #
# --------------------------------------------------------------------------- #

def detect_revenge_size(
    attempts: list[Attempt],
) -> RevengeSizeSignal | None:
    """Detect size-jumping after a losing attempt (revenge trading).

    Cross-attempt: compare the entry qty of attempt N against attempt N-1,
    where attempt N-1 was scored as process_score < 0.5 AND outcome_pnl < 0.

    If attempt N's entry qty is > 1.5x attempt N-1's, emit a RevengeSizeSignal.

    Requires attempts to be time-ordered (started_at ascending).
    Returns the signal for the LAST losing-then-upsized pair found, so that
    the most recent pattern is reported.

    Edge cases:
    - List of length 1: None (nothing to compare).
    - An attempt with no entry actions: skipped (no qty to compare).
    - process_score and outcome_pnl live on AttemptFinishResponse, not on
      Attempt itself; callers must attach them as dynamic attributes
      (process_score, outcome_pnl) on each Attempt for this detector to work.
      Missing attributes are treated as: process_score=1.0, outcome_pnl=0.0
      (not a loss), so the detector safely degrades on unscored attempts.
    """
    if len(attempts) < 2:
        return None

    result: RevengeSizeSignal | None = None

    for i in range(1, len(attempts)):
        prev = attempts[i - 1]
        curr = attempts[i]

        prev_score = getattr(prev, "process_score", 1.0) or 1.0
        prev_pnl = getattr(prev, "outcome_pnl", 0.0) or 0.0

        # Only trigger if previous attempt was a loss (both score AND pnl criteria).
        if prev_score >= 0.5 or prev_pnl >= 0.0:
            continue

        prev_qty = _entry_qty_from_actions(prev.actions or [])
        curr_qty = _entry_qty_from_actions(curr.actions or [])

        if prev_qty is None or curr_qty is None:
            continue

        if prev_qty <= 0:
            continue

        ratio = curr_qty / prev_qty
        if ratio > 1.5:
            result = RevengeSizeSignal(
                previous_attempt_id=prev.id,
                current_attempt_id=curr.id,
                previous_qty=prev_qty,
                current_qty=curr_qty,
                ratio=ratio,
            )

    return result
