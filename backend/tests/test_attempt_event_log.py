"""M6 task 6.1.5 — attempt event-log contract tests.

Guards invariant #2 from PLAN-M6-TRAINING.md §0:
    "Attempts are replayable events, not just outcomes."

Every test drives the HTTP layer via TestClient against an isolated DB.
The event-log contract that the scorer, analytics, and future behavioral
detection (6.3.5) all depend on:

    1. PATCH is append-only — multiple calls accumulate.
    2. Within-bar action order is preserved exactly.
    3. Cross-batch insertion order is preserved (NOT sorted by bar number).
    4. All AttemptActionType variants round-trip correctly.
    5. Actions survive POST /finish and remain visible on subsequent GETs.
    6. ts field is a valid UTC datetime that round-trips cleanly.
    7. PATCH on unknown attempt_id returns 404, not 500 or 200.
    8. PATCH with empty actions list is a no-op (returns attempt unchanged).
    9. bar is persisted as an integer (guards against JSON serialization drift).
    10. Scorer replays the log: wait@decision_bar then enter_long@next_bar
        yields process_score ~0.85 (confirming the log is actually walked).

These tests do NOT duplicate what already exists in:
    test_training_contract.py  — answer-key hiding, blind mode, context filter
    test_training_scorer.py    — hand-computed scorer math (unit, no HTTP)
    test_training_scorer_e2e.py — one HTTP round-trip (create/patch/finish)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(isolated_db):
    from app.main import create_app
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# Shared helpers                                                                #
# --------------------------------------------------------------------------- #

_BASE_TS = "2026-07-09T14:00:00Z"


def _scenario_payload(*, decision_bar: int = 5) -> dict:
    """Minimal valid ScenarioCreate payload for event-log tests."""
    return {
        "title": "Event-log test scenario",
        "archetype": "custom",
        "source_type": "real",
        "drill_type": "decision-point",
        "symbol": "AAPL",
        "session_date": "2026-06-10",
        "start_bar": 0,
        "decision_bar": decision_bar,
        "end_bar": 50,
        "annotations": [],
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "Classic PDH reclaim.",
            "management_notes": [],
        },
        "blind_default": False,
    }


def _create_scenario(client: TestClient, **kwargs) -> dict:
    r = client.post("/api/training/scenarios", json=_scenario_payload(**kwargs))
    assert r.status_code == 200, r.text
    return r.json()


def _create_attempt(client: TestClient, scenario_id: int) -> dict:
    r = client.post("/api/training/attempts", json={"scenario_id": scenario_id})
    assert r.status_code == 200, r.text
    return r.json()


def _action(bar: int, action: str, *,
            price: float | None = None,
            qty: float | None = None,
            ts: str = _BASE_TS) -> dict:
    d: dict = {"bar": bar, "action": action, "ts": ts}
    if price is not None:
        d["price"] = price
    if qty is not None:
        d["qty"] = qty
    return d


def _patch(client: TestClient, attempt_id: int, actions: list[dict]) -> dict:
    r = client.patch(
        f"/api/training/attempts/{attempt_id}",
        json={"actions": actions},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _get_attempt(client: TestClient, attempt_id: int) -> dict:
    r = client.get("/api/training/attempts")
    assert r.status_code == 200, r.text
    for a in r.json():
        if a["id"] == attempt_id:
            return a
    raise AssertionError(f"attempt {attempt_id} not found in GET /training/attempts")


# --------------------------------------------------------------------------- #
# Test 1: PATCH is append-only across multiple calls                           #
# --------------------------------------------------------------------------- #

def test_patch_is_append_only_multiple_calls(client):
    """POST attempt → PATCH 1 action (bar 3) → PATCH 2 more (bar 4, bar 5)
    → GET → 3 entries in insertion order.

    The second PATCH must NOT overwrite the first; it appends.
    """
    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    # First PATCH: one action at bar 3
    after_first = _patch(client, aid, [_action(3, "enter_long", price=10.0, qty=100)])
    assert len(after_first["actions"]) == 1
    assert after_first["actions"][0]["bar"] == 3

    # Second PATCH: two more actions at bar 4 and bar 5
    after_second = _patch(client, aid, [
        _action(4, "set_stop", price=9.5),
        _action(5, "exit", price=11.0, qty=100),
    ])
    assert len(after_second["actions"]) == 3, (
        f"Expected 3 cumulative actions after second PATCH, "
        f"got {len(after_second['actions'])}"
    )

    # GET confirms persistence and order
    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == 3

    # Insertion order: bar3 first, then bar4, then bar5
    bars = [a["bar"] for a in fetched["actions"]]
    assert bars == [3, 4, 5], f"Expected insertion order [3, 4, 5], got {bars}"
    actions = [a["action"] for a in fetched["actions"]]
    assert actions == ["enter_long", "set_stop", "exit"], (
        f"Unexpected action sequence: {actions}"
    )


# --------------------------------------------------------------------------- #
# Test 2: Within-bar action order is preserved exactly                         #
# --------------------------------------------------------------------------- #

def test_patch_preserves_action_order_within_same_bar(client):
    """PATCH [{bar:5, action:'set_stop'}, {bar:5, action:'enter_long'}] in a
    single call → GET → order is preserved: set_stop first, then enter_long.

    Analytics depend on within-bar ordering (e.g. stop was set before entry).
    """
    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    _patch(client, aid, [
        _action(5, "set_stop", price=9.5),
        _action(5, "enter_long", price=10.0, qty=100),
    ])

    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == 2

    first_action = fetched["actions"][0]
    second_action = fetched["actions"][1]
    assert first_action["bar"] == 5
    assert first_action["action"] == "set_stop", (
        f"Expected set_stop first (order of submission), got {first_action['action']!r}"
    )
    assert second_action["bar"] == 5
    assert second_action["action"] == "enter_long", (
        f"Expected enter_long second, got {second_action['action']!r}"
    )


# --------------------------------------------------------------------------- #
# Test 3: Cross-batch order is insertion order, not bar-sorted                 #
# --------------------------------------------------------------------------- #

def test_patch_preserves_action_order_across_batches(client):
    """PATCH bar 3, then PATCH bar 5, then PATCH bar 4 (out-of-order bars).

    GET → actions appear in the order they were PATCHed (3, 5, 4), not sorted
    by bar number. The event log is a real-time decision log; arrival order
    tells you the user first acted on bar 3, revisited bar 5, then bar 4.
    This distinction matters for hesitation/revenge detection (task 6.3.5).
    """
    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    _patch(client, aid, [_action(3, "enter_long", price=10.0, qty=100)])
    _patch(client, aid, [_action(5, "exit", price=11.0, qty=100)])
    _patch(client, aid, [_action(4, "move_stop", price=9.8)])

    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == 3

    bars = [a["bar"] for a in fetched["actions"]]
    assert bars == [3, 5, 4], (
        f"Expected insertion order [3, 5, 4] (NOT bar-sorted), got {bars}. "
        "The event log is a real-time decision log — order must reflect "
        "when the user acted, not which bar they acted on."
    )


# --------------------------------------------------------------------------- #
# Test 4: All AttemptActionType variants round-trip                            #
# --------------------------------------------------------------------------- #

def test_attempt_actions_include_all_action_types(client):
    """PATCH one action of every AttemptActionType value → GET → every one
    persisted with expected fields (bar, action, ts).
    """
    from app.models.training import AttemptActionType

    all_action_types = [v.value for v in AttemptActionType]

    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    # Build one action per type, each at a distinct bar.
    actions_to_patch = [
        _action(bar_idx, action_str, price=10.0, qty=100)
        for bar_idx, action_str in enumerate(all_action_types)
    ]
    _patch(client, aid, actions_to_patch)

    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == len(all_action_types), (
        f"Expected {len(all_action_types)} actions (one per AttemptActionType), "
        f"got {len(fetched['actions'])}"
    )

    persisted_actions = {a["action"] for a in fetched["actions"]}
    for action_str in all_action_types:
        assert action_str in persisted_actions, (
            f"AttemptActionType '{action_str}' did not round-trip "
            f"through the action log"
        )

    # Verify each action has the required fields
    for a in fetched["actions"]:
        assert "bar" in a, f"action missing 'bar' field: {a}"
        assert "action" in a, f"action missing 'action' field: {a}"
        assert "ts" in a, f"action missing 'ts' field: {a}"


# --------------------------------------------------------------------------- #
# Test 5: Actions survive POST /finish                                          #
# --------------------------------------------------------------------------- #

def test_attempt_actions_survive_finish(client):
    """PATCH 3 actions → POST /finish → the finish response's attempt.actions
    still contains all 3. Also: GET /attempts?scenario_id=X returns the
    finished attempt with the actions intact.
    """
    scenario = _create_scenario(client, decision_bar=5)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    _patch(client, aid, [
        _action(5, "enter_long", price=10.0, qty=100),
        _action(6, "set_stop", price=9.5),
        _action(8, "exit", price=11.5, qty=100),
    ])

    # POST /finish — scorer runs server-side
    r = client.post(f"/api/training/attempts/{aid}/finish")
    assert r.status_code == 200, r.text
    result = r.json()

    # The finish response must carry the full action log
    attempt_in_result = result["attempt"]
    assert len(attempt_in_result["actions"]) == 3, (
        f"Expected 3 actions in finish response attempt, "
        f"got {len(attempt_in_result['actions'])}"
    )
    assert attempt_in_result["finished_at"] is not None

    # GET /training/attempts?scenario_id=X must also return the actions intact
    r2 = client.get(f"/api/training/attempts?scenario_id={scenario['id']}")
    assert r2.status_code == 200, r2.text
    attempts_list = r2.json()
    assert len(attempts_list) == 1
    finished_attempt = attempts_list[0]
    assert len(finished_attempt["actions"]) == 3, (
        f"GET /attempts?scenario_id returned attempt with "
        f"{len(finished_attempt['actions'])} actions — expected 3"
    )
    assert finished_attempt["finished_at"] is not None


# --------------------------------------------------------------------------- #
# Test 6: ts is a valid UTC datetime that round-trips cleanly                  #
# --------------------------------------------------------------------------- #

def test_patch_ts_is_wall_clock_utc(client):
    """PATCH an action with an explicit UTC ts → GET → the persisted ts is a
    valid datetime and matches the submitted value (within 2-second epsilon to
    account for any normalization the store might apply).

    ts is the hesitation metric — it must round-trip cleanly.
    """
    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    submitted_ts = "2026-07-09T14:05:30Z"
    _patch(client, aid, [_action(5, "enter_long", price=10.0, qty=100, ts=submitted_ts)])

    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == 1

    persisted_ts_str = fetched["actions"][0]["ts"]
    assert persisted_ts_str is not None, "ts field is None after round-trip"
    assert isinstance(persisted_ts_str, str), f"ts is not a string: {persisted_ts_str!r}"

    # Must parse as a valid datetime
    try:
        # FastAPI/Pydantic may return with +00:00 or Z; normalize for fromisoformat
        normalized = persisted_ts_str.replace("Z", "+00:00")
        persisted_dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        pytest.fail(f"Persisted ts '{persisted_ts_str}' is not a valid ISO datetime: {exc}")

    # Compare naive UTC values (strip tzinfo to avoid comparison complexity)
    submitted_naive = datetime(2026, 7, 9, 14, 5, 30)
    persisted_naive = persisted_dt.replace(tzinfo=None)
    diff_seconds = abs((persisted_naive - submitted_naive).total_seconds())
    assert diff_seconds < 2, (
        f"Persisted ts '{persisted_ts_str}' differs from submitted "
        f"'{submitted_ts}' by {diff_seconds}s — ts must round-trip cleanly "
        "(hesitation metric)"
    )


# --------------------------------------------------------------------------- #
# Test 7: PATCH unknown attempt returns 404                                    #
# --------------------------------------------------------------------------- #

def test_patch_unknown_attempt_returns_404(client):
    """PATCH /api/training/attempts/99999 → 404 (not 500, not 200-with-empty).

    Guards against regression where the route silently ignores missing attempts
    and returns an empty/null response.
    """
    r = client.patch(
        "/api/training/attempts/99999",
        json={"actions": [_action(1, "enter_long", price=10.0, qty=100)]},
    )
    assert r.status_code == 404, (
        f"Expected 404 for unknown attempt_id=99999, got {r.status_code}: {r.text}"
    )


# --------------------------------------------------------------------------- #
# Test 8: PATCH empty actions list is a no-op                                  #
# --------------------------------------------------------------------------- #

def test_patch_empty_actions_is_noop(client):
    """PATCH {actions: []} → returns the attempt unchanged (200, no new actions).

    The route passes the empty list straight to append_attempt_actions, which
    does existing + [] = existing. This is a no-op, not an error.
    """
    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    # First add one real action so the attempt is not empty
    _patch(client, aid, [_action(3, "skip")])

    # Now PATCH with empty list
    r = client.patch(
        f"/api/training/attempts/{aid}",
        json={"actions": []},
    )
    assert r.status_code == 200, (
        f"Empty PATCH must return 200 (no-op), got {r.status_code}: {r.text}"
    )
    result = r.json()
    assert len(result["actions"]) == 1, (
        f"Empty PATCH must not alter the action log (expected 1 action, "
        f"got {len(result['actions'])})"
    )
    assert result["actions"][0]["action"] == "skip"


# --------------------------------------------------------------------------- #
# Test 9: bar is persisted as integer (not converted to timestamp)             #
# --------------------------------------------------------------------------- #

def test_actions_json_bar_indexed_not_ts_indexed(client):
    """The persisted actions retain 'bar' as an integer field — not converted
    to a timestamp or any other type.

    Guards against a JSON-column serialization mistake where bar (e.g. 42)
    could be coerced to a date/timestamp or stored as a string.
    """
    scenario = _create_scenario(client)
    attempt = _create_attempt(client, scenario["id"])
    aid = attempt["id"]

    test_bars = [0, 1, 7, 42, 99]
    _patch(client, aid, [_action(b, "wait") for b in test_bars])

    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == len(test_bars)

    for i, a in enumerate(fetched["actions"]):
        bar_val = a["bar"]
        assert isinstance(bar_val, int), (
            f"bar field at position {i} should be int, got {type(bar_val).__name__!r}: "
            f"{bar_val!r}"
        )
        assert bar_val == test_bars[i], (
            f"bar value mismatch at position {i}: expected {test_bars[i]}, got {bar_val}"
        )


# --------------------------------------------------------------------------- #
# Test 10: Replay by bar — wait at decision_bar defers to next bar             #
# --------------------------------------------------------------------------- #

def test_replay_by_bar_reconstructs_terminal_decision_at_decision_bar(client):
    """Integration test: the action log is actually replayed by the scorer.

    Scenario: decision_bar=5, correct_actions=[enter_long].
    User log: [wait@bar5, enter_long@bar6].

    Expected: scorer treats wait@5 as deferral, sees enter_long@6 as the
    terminal decision at the deferred decision point, and scores it as
    correct → process_score ≈ 0.85 (correct decision, no checklist).

    This is the end-to-end confirmation that the action log is replayable —
    the scorer literally walks the log to find the terminal decision.
    """
    # Create scenario with decision_bar=5, enter_long is correct
    scenario_payload = {
        "title": "Replay deferral test",
        "archetype": "custom",
        "source_type": "real",
        "drill_type": "decision-point",
        "symbol": "AAPL",
        "session_date": "2026-06-10",
        "start_bar": 0,
        "decision_bar": 5,
        "end_bar": 50,
        "annotations": [],
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "Wait one bar, then enter on confirmation.",
            "management_notes": [],
        },
        "blind_default": False,
    }
    r = client.post("/api/training/scenarios", json=scenario_payload)
    assert r.status_code == 200, r.text
    scenario = r.json()

    # Create attempt
    r = client.post(
        "/api/training/attempts",
        json={"scenario_id": scenario["id"]},
    )
    assert r.status_code == 200, r.text
    attempt = r.json()
    aid = attempt["id"]

    # PATCH: wait at bar 5, then enter_long at bar 6
    _patch(client, aid, [
        _action(5, "wait", ts="2026-07-09T14:00:00Z"),
        _action(6, "enter_long", price=10.5, qty=100, ts="2026-07-09T14:01:00Z"),
    ])

    # Verify the log was stored in submission order before scoring
    fetched = _get_attempt(client, aid)
    assert len(fetched["actions"]) == 2
    assert fetched["actions"][0]["action"] == "wait"
    assert fetched["actions"][0]["bar"] == 5
    assert fetched["actions"][1]["action"] == "enter_long"
    assert fetched["actions"][1]["bar"] == 6

    # POST /finish — scorer must walk the log, treat wait@5 as deferral,
    # and score enter_long@6 as the terminal decision.
    r = client.post(f"/api/training/attempts/{aid}/finish")
    assert r.status_code == 200, r.text
    result = r.json()

    # Correct decision (enter_long), no checklist → 0.7*1.0 + 0.3*0.5 = 0.85
    assert result["process_score"] == pytest.approx(0.85, abs=1e-4), (
        f"Expected process_score ≈ 0.85 (wait@5 defers to enter_long@6 = correct), "
        f"got {result['process_score']}. "
        "The scorer must walk the action log from decision_bar, skip wait actions, "
        "and find the terminal decision at the next bar."
    )

    # Confirm the action log is intact in the finish response
    assert len(result["attempt"]["actions"]) == 2, (
        "Finish response must carry the full 2-action log"
    )
