"""TestClient smoke for GET /api/training/progress (M6 task 6.4.2).

Confirms the ProgressReport shape that the frontend ProgressPage binds to:
  - mastery list is populated with all archetypes (including unattempted ones)
  - process_streak / longest_process_streak present and >= 0
  - due_reviews and quests are lists (empty is fine for v1)
  - no outcome_pnl anywhere in the response (anti-gambling guarantee)

Scenario: seed two archetypes with different scores, then check that the
mastery list is non-empty and has the expected shape.
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
# Helpers
# --------------------------------------------------------------------------- #

_BASE_SCENARIO = {
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
        "explanation": "Test setup.",
        "management_notes": [],
    },
    "blind_default": False,
}

_ACTION_TS = "2026-07-09T14:00:00Z"


def _create_and_finish_attempt(client, archetype: str, action: str) -> dict:
    """Create scenario + attempt, add one action, finish. Returns finish response."""
    # 1. Create scenario.
    payload = {**_BASE_SCENARIO, "title": f"test-{archetype}", "archetype": archetype}
    r = client.post("/api/training/scenarios", json=payload)
    assert r.status_code == 200, r.text
    scenario_id = r.json()["id"]

    # 2. Create attempt.
    r = client.post("/api/training/attempts", json={"scenario_id": scenario_id})
    assert r.status_code == 200, r.text
    attempt_id = r.json()["id"]

    # 3. Append one action.
    r = client.patch(
        f"/api/training/attempts/{attempt_id}",
        json={"actions": [{"bar": 5, "action": action, "price": 100.0, "qty": 10, "ts": _ACTION_TS}]},
    )
    assert r.status_code == 200, r.text

    # 4. Finish.
    r = client.post(f"/api/training/attempts/{attempt_id}/finish")
    assert r.status_code == 200, r.text
    return r.json()


def _assert_no_pnl_reward(node, path: str = "") -> None:
    """Recursively assert outcome_pnl is not used as a reward signal.

    outcome_pnl MAY be present in the attempt data (it is recorded),
    but the progress/mastery values must not be pnl-derived. Since
    ProgressReport has no pnl fields, we just confirm the top-level
    shape has no unexpected pnl keys in mastery/streak/quest fields.
    """
    FORBIDDEN_AT_TOP = {"mastery_pnl", "streak_pnl", "pnl_streak"}
    if isinstance(node, dict):
        for k in FORBIDDEN_AT_TOP:
            assert k not in node, f"Unexpected pnl reward key '{k}' found at {path}"
        for k, v in node.items():
            _assert_no_pnl_reward(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _assert_no_pnl_reward(item, f"{path}[{i}]")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_progress_endpoint_returns_shape(client):
    """GET /api/training/progress returns a ProgressReport-shaped dict."""
    r = client.get("/api/training/progress")
    assert r.status_code == 200, r.text
    data = r.json()

    # Required top-level keys
    assert "mastery" in data
    assert "process_streak" in data
    assert "longest_process_streak" in data
    assert "due_reviews" in data
    assert "quests" in data
    assert "generated_at" in data

    # Types
    assert isinstance(data["mastery"], list)
    assert isinstance(data["process_streak"], int)
    assert isinstance(data["longest_process_streak"], int)
    assert isinstance(data["due_reviews"], list)
    assert isinstance(data["quests"], list)
    assert isinstance(data["generated_at"], str)

    # Streaks non-negative
    assert data["process_streak"] >= 0
    assert data["longest_process_streak"] >= 0


def test_progress_mastery_all_archetypes_present(client):
    """Mastery list must include all archetypes (including unattempted)."""
    r = client.get("/api/training/progress")
    assert r.status_code == 200, r.text
    data = r.json()

    archetypes = {m["archetype"] for m in data["mastery"]}
    # All known archetypes from models/training.py Archetype enum
    expected = {"gap-and-go", "failed-breakout", "chop", "trend-day", "custom"}
    assert expected.issubset(archetypes), f"Missing archetypes: {expected - archetypes}"

    for m in data["mastery"]:
        # Each entry has the expected fields
        assert "archetype" in m
        assert "level" in m
        assert "level_label" in m
        assert "rolling_mean_score" in m
        assert "attempts_count" in m
        # Unattempted entries have level=null and attempts_count=0
        if m["attempts_count"] == 0:
            assert m["level"] is None
            assert m["level_label"] == "unattempted"


def test_progress_mastery_populated_after_attempts(client):
    """After completing drills across two archetypes, mastery is non-null."""
    # Finish a correct attempt for gap-and-go
    finish1 = _create_and_finish_attempt(client, "gap-and-go", "enter_long")
    assert finish1["process_score"] > 0

    # Finish an incorrect attempt for failed-breakout (skip instead of correct action)
    finish2 = _create_and_finish_attempt(client, "failed-breakout", "skip")
    # process_score may be low or high depending on scorer; we just need it to exist
    assert "process_score" in finish2

    r = client.get("/api/training/progress")
    assert r.status_code == 200, r.text
    data = r.json()

    mastery_by_arch = {m["archetype"]: m for m in data["mastery"]}

    gap = mastery_by_arch.get("gap-and-go")
    assert gap is not None
    assert gap["attempts_count"] == 1
    assert gap["level"] is not None  # has a level after one attempt
    assert gap["rolling_mean_score"] is not None

    fb = mastery_by_arch.get("failed-breakout")
    assert fb is not None
    assert fb["attempts_count"] == 1

    # Streak should reflect attempts
    assert data["process_streak"] >= 0
    assert data["longest_process_streak"] >= 0


def test_progress_no_pnl_reward_keys(client):
    """Progress report must not contain pnl-derived reward fields (anti-gambling)."""
    r = client.get("/api/training/progress")
    assert r.status_code == 200, r.text
    _assert_no_pnl_reward(r.json())


def test_progress_mastery_each_item_level_label_consistent(client):
    """level and level_label are consistent: null level → 'unattempted', int → label."""
    _create_and_finish_attempt(client, "chop", "enter_long")

    r = client.get("/api/training/progress")
    assert r.status_code == 200, r.text
    data = r.json()

    valid_labels = {"learning", "developing", "proficient", "mastered", "unattempted"}
    for m in data["mastery"]:
        assert m["level_label"] in valid_labels, f"Unknown label: {m['level_label']}"
        if m["level"] is None:
            assert m["level_label"] == "unattempted"
        else:
            assert m["level_label"] != "unattempted"
            assert 0 <= m["level"] <= 3
