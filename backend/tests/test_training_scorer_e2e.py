"""E2E round-trip test for the process scorer (M6 task 6.1.1).

Exercises the full HTTP path:
  1. Create scenario with correct_actions=[enter_long] at decision_bar=5.
  2. Create attempt.
  3. PATCH one action {bar: 5, action: 'enter_long', ts: <utc>}.
  4. POST /training/attempts/{id}/finish.
  5. Assert process_score ≈ 0.85, answer_key.correct_actions == ['enter_long'],
     and the attempt shows finished_at set.

Placed in a separate file rather than test_training_contract.py because that
file has grown; adding scorer-specific e2e tests here keeps concerns separated.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(isolated_db):
    from app.main import create_app
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# Scorer e2e round-trip                                                         #
# --------------------------------------------------------------------------- #

def test_finish_attempt_returns_correct_process_score(client):
    """Full attempt round-trip via HTTP.

    correct entry at decision_bar → process_score ≈ 0.85, answer_key exposed.
    """
    # 1. Create a scenario with enter_long as the correct action at bar 5.
    scenario_payload = {
        "title": "E2E scorer test",
        "archetype": "custom",
        "source_type": "real",
        "drill_type": "decision-point",
        "symbol": "AAPL",
        "session_date": "2026-06-10",
        "start_bar": 330,
        "decision_bar": 5,
        "end_bar": 430,
        "annotations": [],
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "Classic breakout setup.",
            "management_notes": [],
        },
        "blind_default": False,
    }
    r = client.post("/api/training/scenarios", json=scenario_payload)
    assert r.status_code == 200, r.text
    scenario = r.json()
    scenario_id = scenario["id"]

    # 2. Create an attempt for this scenario.
    r = client.post("/api/training/attempts", json={"scenario_id": scenario_id})
    assert r.status_code == 200, r.text
    attempt = r.json()
    attempt_id = attempt["id"]
    assert attempt["finished_at"] is None

    # 3. PATCH one action: enter_long at bar 5 (the decision bar).
    action_ts = "2026-07-09T14:00:00Z"
    patch_body = {
        "actions": [
            {
                "bar": 5,
                "action": "enter_long",
                "price": 182.5,
                "qty": 100,
                "ts": action_ts,
            }
        ]
    }
    r = client.patch(f"/api/training/attempts/{attempt_id}", json=patch_body)
    assert r.status_code == 200, r.text
    patched = r.json()
    assert len(patched["actions"]) == 1
    assert patched["actions"][0]["action"] == "enter_long"
    assert patched["actions"][0]["bar"] == 5

    # 4. POST /finish — scorer computes the process score server-side.
    r = client.post(f"/api/training/attempts/{attempt_id}/finish")
    assert r.status_code == 200, r.text
    result = r.json()

    # 5. Assertions.
    # process_score: correct entry, no checklist → 0.7*1.0 + 0.3*0.5 = 0.85
    assert result["process_score"] == pytest.approx(0.85, abs=1e-4), (
        f"expected process_score ≈ 0.85 (correct entry, no checklist), "
        f"got {result['process_score']}"
    )

    # answer_key is now exposed (first and only time).
    assert "answer_key" in result, "finish response must contain answer_key"
    assert result["answer_key"]["correct_actions"] == ["enter_long"], (
        f"expected correct_actions == ['enter_long'], "
        f"got {result['answer_key']['correct_actions']}"
    )

    # attempt.finished_at must now be set.
    assert result["attempt"]["finished_at"] is not None, (
        "attempt.finished_at must be set after /finish"
    )

    # outcome_pnl is recorded (entry price was provided so it can be computed
    # once an exit is present — since we only provided an entry with no exit,
    # outcome_pnl should be None here).
    # This verifies the scorer handles single-side positions gracefully.
    assert result["outcome_pnl"] is None, (
        "no exit action → outcome_pnl should be None"
    )


# --------------------------------------------------------------------------- #
# Frame-mismatch regression (found during C1 6.1.4 integration)                #
# --------------------------------------------------------------------------- #

def test_local_to_global_bar_translation_from_session_meta(client):
    """Regression: drill player must translate session-local bars back to the
    scenario's global frame before PATCHing actions.

    Discovered during 6.1.4 seed-scenario integration: the session response
    exposes decision_bar in LOCAL slice coordinates (a convenience for the
    UI), while scenario.decision_bar / start_bar / end_bar and every
    AttemptAction.bar are compared in GLOBAL coordinates by the scorer.
    A naive player that PATCHes bar=session.decision_bar (local) will get
    scored against the wrong bar.

    The contract this test locks in:
        globalBar = session.meta.start_bar + local_bar

    Fixed in DrillPlayerPage.tsx by threading `toGlobalBar()` through every
    action PATCH. This test catches any future regression at the HTTP layer.
    """
    # Scenario with start_bar=330 (09:30 ET open), decision_bar=335 (09:35).
    scenario_payload = {
        "title": "Frame-mismatch regression",
        "archetype": "custom",
        "source_type": "real",
        "drill_type": "decision-point",
        "symbol": "AAPL",
        "session_date": "2026-06-10",
        "start_bar": 330,
        "decision_bar": 335,  # 5 bars past open
        "end_bar": 400,
        "annotations": [],
        "answer_key": {
            "correct_actions": ["enter_long"],
            "explanation": "PDH break at 09:35.",
            "management_notes": [],
        },
        "blind_default": False,
    }
    scenario_id = client.post(
        "/api/training/scenarios", json=scenario_payload
    ).json()["id"]

    # Fetch the session — this is what the drill player sees. decision_bar is
    # in LOCAL frame; start_bar rides in meta for the local→global translation.
    session = client.get(
        f"/api/training/scenarios/{scenario_id}/session"
    ).json()
    assert session["decision_bar"] == 5, (
        "session should carry local decision_bar (335 - 330 = 5)"
    )
    assert session["meta"]["start_bar"] == 330, (
        "session.meta.start_bar is the anchor for local→global translation"
    )

    # Open attempt.
    attempt_id = client.post(
        "/api/training/attempts", json={"scenario_id": scenario_id}
    ).json()["id"]

    # A CORRECT drill player PATCHes with the global bar (start_bar + local).
    global_bar = session["meta"]["start_bar"] + session["decision_bar"]
    client.patch(
        f"/api/training/attempts/{attempt_id}",
        json={
            "actions": [
                {
                    "bar": global_bar,  # 335, matches scenario.decision_bar
                    "action": "enter_long",
                    "price": None,
                    "qty": None,
                    "ts": "2026-07-09T14:00:00Z",
                }
            ]
        },
    )

    # Finish — the scorer walks the action log against scenario.decision_bar
    # (global). A correct enter_long at the global decision bar should give
    # decision_correctness = 1.0 → process_score = 0.7*1.0 + 0.3*0.5 = 0.85.
    result = client.post(
        f"/api/training/attempts/{attempt_id}/finish"
    ).json()

    assert result["process_score"] == pytest.approx(0.85, abs=1e-4), (
        f"local→global translation must yield the correct-decision score. "
        f"Got {result['process_score']}. If this fails, either the drill "
        f"player is PATCHing in the wrong frame or the scorer's frame "
        f"contract changed."
    )


def test_naive_local_bar_scores_wrong_when_start_bar_nonzero(client):
    """Companion to the above: prove the bug exists when the translation is
    skipped, so future refactors can't quietly re-introduce it (e.g. by
    "simplifying" scoring to accept local bars — that would break every
    seed scenario, since all real scenarios have start_bar > 0).
    """
    scenario_id = client.post(
        "/api/training/scenarios",
        json={
            "title": "Naive-local regression companion",
            "archetype": "custom",
            "source_type": "real",
            "drill_type": "decision-point",
            "symbol": "AAPL",
            "session_date": "2026-06-10",
            "start_bar": 330,
            "decision_bar": 335,
            "end_bar": 400,
            "annotations": [],
            "answer_key": {
                "correct_actions": ["enter_long"],
                "explanation": "same as above",
                "management_notes": [],
            },
            "blind_default": False,
        },
    ).json()["id"]

    session = client.get(
        f"/api/training/scenarios/{scenario_id}/session"
    ).json()
    attempt_id = client.post(
        "/api/training/attempts", json={"scenario_id": scenario_id}
    ).json()["id"]

    # NAIVE: use the local bar directly. This is what the pre-fix player did.
    client.patch(
        f"/api/training/attempts/{attempt_id}",
        json={
            "actions": [
                {
                    "bar": session["decision_bar"],  # local (5), NOT 335
                    "action": "enter_long",
                    "price": None,
                    "qty": None,
                    "ts": "2026-07-09T14:00:00Z",
                }
            ]
        },
    )

    result = client.post(
        f"/api/training/attempts/{attempt_id}/finish"
    ).json()

    # The scorer sees no action at global bar 335, treats the attempt as a
    # skip, and scores decision_correctness = 0.0.
    # process_score = 0.7*0.0 + 0.3*0.5 = 0.15
    assert result["process_score"] == pytest.approx(0.15, abs=1e-4), (
        f"naive local-bar PATCH must NOT accidentally score as correct. "
        f"Got {result['process_score']}. If this drifts, the frame contract "
        f"has silently changed — check the scorer."
    )
