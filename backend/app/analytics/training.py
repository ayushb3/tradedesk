"""Process scorer for training-module drill attempts (M6 task 6.1.1).

Public API
----------
score_attempt(attempt: Attempt, scenario: ScenarioFull) -> AttemptFinishResponse

Design contract
---------------
* Score **process, never outcome** (PLAN-M6-TRAINING.md §0 principle 1).
  outcome_pnl is computed, stored, and displayed, but it is *never* an input
  to process_score. A drill where the user followed every rule and lost money
  scores higher than one where they broke rules and got lucky.

* process_score ∈ [0, 1] is a weighted average of two independent sub-scores:
    decision_correctness  (weight 0.7) — did the user do the right thing?
    checklist_adherence   (weight 0.3) — did they check their rules?

  Total = clamp(0.7 * decision_correctness + 0.3 * checklist_adherence, 0, 1)

Drill-type variants
-------------------
decision-point (default)
    Terminal action at/after decision_bar is compared to answer_key.correct_actions.
    `wait` at the decision bar defers to the next bar; keep walking until a
    non-wait action is found or the tape ends (treated as skip).

management-only
    Entry is forced by the scenario before decision_bar. The user's job is
    exit/stop management. Score is keyed on exit actions within the scenario
    range. If answer_key.management_notes is populated, each bar in those notes
    is scored against the user's action at that bar and the results are averaged.

do-nothing
    Passing means the user took zero entry actions across the entire range.
    If any enter_long or enter_short appears in actions_json → decision = 0.0.
    This is the anti-gambling drill (PLAN §1, pillar 2).

drawdown-script
    An engineered multi-session losing streak designed to measure session-3
    behaviour: does the user still follow rules, or do they start size-jumping
    and revenge trading?

    Bar-encoding convention (CRITICAL — do not change without updating tests):
        session_index = bar // 1000
        local_bar     = bar % 1000

    So bars 0..999 belong to session 0, bars 1000..1999 to session 1, etc.
    This preserves the append-only event-log contract: the entire multi-session
    attempt is stored in a single Attempt row with contiguous bars.

    Scoring: answer_key.management_notes carries per-session guidance in the form
        [{"session": int, "decision_bar": int, "correct_action": str}, ...]
    Each session's terminal decision action (at/after decision_bar in local-bar
    space) is compared to the session's correct_action. Results are averaged.
    If a session has no user actions at all, it is treated as a `skip` decision.

    outcome_pnl is computed across all sessions but NEVER influences
    process_score (PLAN §0 principle 1 — anti-gambling guarantee).

ScenarioFull import
-------------------
ScenarioFull lives in app.db.training_store (documented in training_store.py).
We use TYPE_CHECKING to avoid a hard circular import — at runtime the argument
is duck-typed (anything with the right attributes works).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.training import (
    Attempt,
    AttemptAction,
    AttemptActionType,
    AttemptFinishResponse,
    AnswerKey,
)

if TYPE_CHECKING:
    from app.db.training_store import ScenarioFull

# Entry actions that constitute an "I'm taking a trade" decision.
_ENTRY_ACTIONS = {AttemptActionType.enter_long, AttemptActionType.enter_short}


# --------------------------------------------------------------------------- #
# Decision-correctness helpers                                                  #
# --------------------------------------------------------------------------- #

def _terminal_decision_action(
    actions: list[AttemptAction],
    decision_bar: int | None,
) -> AttemptActionType | None:
    """Walk the action log to find the terminal non-wait decision.

    Starting from decision_bar, walk bar-by-bar in ascending bar order.
    The first bar at or after decision_bar whose last action is NOT `wait`
    is the terminal decision. If only `wait` actions exist for all remaining
    bars, or there are no actions at/after decision_bar, return None
    (treated as `skip`).

    v1: no partial credit for "almost correct" actions — that's a follow-up.
    """
    if decision_bar is None:
        return None

    # Group actions by bar, keeping insertion order within each bar.
    bar_actions: dict[int, list[AttemptActionType]] = {}
    for a in actions:
        bar_actions.setdefault(a.bar, []).append(a.action)

    # Inspect bars >= decision_bar in ascending order.
    for bar in sorted(b for b in bar_actions if b >= decision_bar):
        # The terminal action at this bar is the last one recorded.
        last = bar_actions[bar][-1]
        if last != AttemptActionType.wait:
            return last
        # wait at this bar → defer to next bar

    return None  # tape ended with only waits or no actions after decision_bar


def _score_decision_point(
    actions: list[AttemptAction],
    scenario: "ScenarioFull",
) -> float:
    """decision-point and default drill scoring (weight 0.7 sub-score)."""
    terminal = _terminal_decision_action(actions, scenario.decision_bar)

    # None means the user never acted → effectively skip.
    if terminal is None:
        terminal = AttemptActionType.skip

    correct_set = set(scenario.answer_key.correct_actions)
    return 1.0 if terminal in correct_set else 0.0


def _score_do_nothing(actions: list[AttemptAction]) -> float:
    """do-nothing drill: passing means no entry action across the full range."""
    for a in actions:
        if a.action in _ENTRY_ACTIONS:
            return 0.0
    return 1.0


def _score_drawdown_script(
    actions: list[AttemptAction],
    scenario: "ScenarioFull",
) -> float:
    """drawdown-script drill: average process score across all sub-sessions.

    Bar-encoding convention (canonical, enforced by test_drawdown_script.py):
        session_index = bar // 1000
        local_bar     = bar % 1000

    Bars 0..999 → session 0, bars 1000..1999 → session 1, etc.  The action
    log is a flat append-only event log; splitting by session index is purely
    arithmetic with no schema migration required.

    Each session's score is derived from answer_key.management_notes, which
    for drawdown-script carries entries of the form:
        {"session": int, "decision_bar": int, "correct_action": str}

    The terminal decision action in that session at/after decision_bar
    (in local-bar space) is compared to correct_action.  Absent any user
    action in a session → treated as skip (explicit do-nothing).

    The final decision_correctness is the simple average across sessions.
    If management_notes has no parseable session entries, fall back to
    _score_decision_point so the function always returns a meaningful float.

    IMPORTANT: outcome_pnl is never an input to this function.  A user who
    followed all rules and was stopped out in every session gets 1.0.  This
    is the core anti-gambling guarantee for the drawdown-script variant.
    (PLAN-M6-TRAINING.md §0 principle 1)
    """
    notes = scenario.answer_key.management_notes  # list[dict]

    # Build a map: session_index -> {"decision_bar": int, "correct_action": str}
    session_specs: dict[int, dict] = {}
    for note in notes:
        sess_idx = note.get("session")
        db = note.get("decision_bar")
        ca = note.get("correct_action")
        if sess_idx is None or db is None or ca is None:
            continue
        try:
            # Validate the correct_action string is a known enum value.
            AttemptActionType(ca)
        except ValueError:
            continue
        session_specs[int(sess_idx)] = {"decision_bar": int(db), "correct_action": ca}

    if not session_specs:
        # No parseable session specs — fall back to decision-point scoring.
        return _score_decision_point(actions, scenario)

    # Split the flat action log into per-session buckets using bar // 1000.
    # Within each bucket, translate to local-bar space (bar % 1000).
    session_actions: dict[int, list[AttemptAction]] = {}
    for a in actions:
        sess_idx = a.bar // 1000
        local_bar = a.bar % 1000
        # Re-create the action with the local bar so _terminal_decision_action
        # can compare against local decision_bar values.
        local_action = AttemptAction(
            bar=local_bar,
            action=a.action,
            price=a.price,
            qty=a.qty,
            ts=a.ts,
        )
        session_actions.setdefault(sess_idx, []).append(local_action)

    # Score each session.
    session_scores: list[float] = []
    for sess_idx, spec in session_specs.items():
        sess_acts = session_actions.get(sess_idx, [])
        decision_bar = spec["decision_bar"]
        correct_action_str = spec["correct_action"]
        correct_action = AttemptActionType(correct_action_str)

        # Find the terminal decision at/after decision_bar in local-bar space.
        terminal = _terminal_decision_action(sess_acts, decision_bar)
        if terminal is None:
            # No actions in this session → treat as explicit skip.
            terminal = AttemptActionType.skip

        session_scores.append(1.0 if terminal == correct_action else 0.0)

    return sum(session_scores) / len(session_scores)


def _score_management_only(
    actions: list[AttemptAction],
    scenario: "ScenarioFull",
) -> float:
    """management-only drill: score on exit/stop actions.

    If management_notes is populated (bar-indexed per-bar guidance), each bar
    present in the notes is scored: 1.0 if the user's terminal action at that
    bar matches the note's 'action' field, else 0.0. The result is averaged.

    If management_notes is empty, fall back to matching the user's last exit
    action (anywhere in the range) against answer_key.correct_actions.

    v1 is intentionally simple — moved-stop / revenge-sizing detection is
    deferred to task 6.3.5.
    """
    notes = scenario.answer_key.management_notes  # list[dict]

    if notes:
        # Score each bar in the management notes.
        scores: list[float] = []
        bar_actions: dict[int, list[AttemptActionType]] = {}
        for a in actions:
            bar_actions.setdefault(a.bar, []).append(a.action)

        for note in notes:
            bar = note.get("bar")
            expected_action_str = note.get("action")
            if bar is None or expected_action_str is None:
                continue
            try:
                expected = AttemptActionType(expected_action_str)
            except ValueError:
                continue

            bar_last = bar_actions.get(bar)
            if bar_last is None:
                # User took no action at this bar → wrong by omission.
                scores.append(0.0)
            else:
                scores.append(1.0 if bar_last[-1] == expected else 0.0)

        if scores:
            return sum(scores) / len(scores)
        # Notes exist but had no parseable entries — fall through.

    # No parseable notes: look for any exit action and match against correct_actions.
    correct_set = set(scenario.answer_key.correct_actions)
    exit_related = {AttemptActionType.exit, AttemptActionType.skip}
    terminal_exit: AttemptActionType | None = None
    for a in sorted(actions, key=lambda x: (x.bar, x.ts)):
        if a.action in exit_related or a.action in _ENTRY_ACTIONS:
            terminal_exit = a.action

    if terminal_exit is None:
        return 0.0  # user never exited — management failure
    return 1.0 if terminal_exit in correct_set else 0.0


# --------------------------------------------------------------------------- #
# Checklist adherence                                                           #
# --------------------------------------------------------------------------- #

def _score_checklist(checklist: list[dict]) -> float:
    """Fraction of checklist items marked 'checked'.

    Empty checklist → neutral 0.5 (no template gap should penalise the user).
    """
    if not checklist:
        return 0.5

    checked = sum(1 for item in checklist if item.get("checked", False))
    return checked / len(checklist)


# --------------------------------------------------------------------------- #
# outcome_pnl (recorded, NEVER rewarded — see PLAN §0 principle 1)            #
# --------------------------------------------------------------------------- #

def _compute_outcome_pnl(actions: list[AttemptAction]) -> float:
    """Mock-first P&L from the action log.

    Looks for the first entry action (enter_long / enter_short) and the first
    subsequent exit action with a price. Returns (exit - entry) * qty * direction.

    If no entry action is found, returns 0.0.

    IMPORTANT: This value is recorded and displayed for educational transparency.
    It is NEVER an input to process_score (PLAN-M6-TRAINING.md §0 principle 1).
    """
    sorted_actions = sorted(actions, key=lambda x: (x.bar, x.ts))

    entry_price: float | None = None
    entry_qty: float | None = None
    side_multiplier: float = 1.0  # +1 long, -1 short

    for a in sorted_actions:
        if entry_price is None:
            if a.action == AttemptActionType.enter_long and a.price is not None:
                entry_price = a.price
                entry_qty = a.qty or 1.0
                side_multiplier = 1.0
            elif a.action == AttemptActionType.enter_short and a.price is not None:
                entry_price = a.price
                entry_qty = a.qty or 1.0
                side_multiplier = -1.0
        else:
            if a.action == AttemptActionType.exit and a.price is not None:
                qty = entry_qty or 1.0
                return (a.price - entry_price) * qty * side_multiplier

    return 0.0


# --------------------------------------------------------------------------- #
# Main public function                                                           #
# --------------------------------------------------------------------------- #

def score_attempt(
    attempt: Attempt,
    scenario: "ScenarioFull",
) -> AttemptFinishResponse:
    """Score a completed drill attempt against its scenario.

    Returns AttemptFinishResponse with:
    - process_score: weighted average of decision_correctness (0.7) and
      checklist_adherence (0.3), clamped to [0, 1].
    - outcome_pnl: P&L from the action log (recorded, NEVER rewarded per
      PLAN-M6-TRAINING.md §0 principle 1).
    - checklist: pass-through from attempt (or []).
    - answer_key: the scenario's answer key (first time it's exposed).
    - verdict: None in v1 (verdict enum is for the debrief interview, 6.2.x).
    """
    actions = attempt.actions or []

    # Attempt.checklist_json may be present as a model field in a future version;
    # for now the Attempt Pydantic model (models/training.py) does not include it
    # (it lives only in the DB row). Accept it gracefully if present, else [].
    checklist = getattr(attempt, "checklist_json", None) or []

    drill_type_val = scenario.drill_type.value if scenario.drill_type else "decision-point"

    # --- Decision correctness (0.7 weight) ---
    if drill_type_val == "do-nothing":
        decision_correctness = _score_do_nothing(actions)
    elif drill_type_val == "management-only":
        decision_correctness = _score_management_only(actions, scenario)
    elif drill_type_val == "drawdown-script":
        decision_correctness = _score_drawdown_script(actions, scenario)
    else:
        # decision-point, spot-setup, or any future variant
        decision_correctness = _score_decision_point(actions, scenario)

    # --- Checklist adherence (0.3 weight) ---
    checklist_adherence = _score_checklist(checklist)

    # --- Weighted total (clamped defensively) ---
    raw_score = 0.7 * decision_correctness + 0.3 * checklist_adherence
    process_score = max(0.0, min(1.0, raw_score))

    # --- outcome_pnl (educational display only — never rewarded) ---
    outcome_pnl = _compute_outcome_pnl(actions)

    return AttemptFinishResponse(
        attempt=attempt,
        process_score=process_score,
        outcome_pnl=outcome_pnl if outcome_pnl != 0.0 else None,
        checklist=checklist,
        answer_key=scenario.answer_key,
        verdict=None,  # v1: verdict is for debrief (6.2.x)
    )
