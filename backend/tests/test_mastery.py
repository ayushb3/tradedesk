"""M6 task 6.4.1 — mastery model + spaced-repetition scheduler tests.

Tests exercise `app.analytics.mastery.compute_progress` (pure unit) and the
`GET /api/training/progress` endpoint (HTTP / integration).

All unit tests (1–11) use hand-built fixtures with injected `now` — no
database.  The two endpoint tests (12–13) use `isolated_db` + TestClient,
matching the pattern from test_leaks.py.

Anti-gambling assertion (test 5): two attempts with identical process_score
but wildly different outcome_pnl must be treated identically by the streak.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

UTC = timezone.utc

# Fixed injection point — used wherever we need a stable "now".
_NOW = datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _scenario(scenario_id: int, archetype: str = "gap-and-go") -> object:
    """Build a minimal ScenarioPublic for the given archetype."""
    from app.models.training import (
        ScenarioPublic, Archetype, DrillType, ScenarioSource,
    )
    return ScenarioPublic(
        id=scenario_id,
        title=f"Scenario {scenario_id}",
        archetype=Archetype(archetype),
        source_type=ScenarioSource.synthetic,
        drill_type=DrillType.decision_point,
        start_bar=0,
        active=True,
        created_at=_NOW,
    )


def _attempt(
    attempt_id: int,
    scenario_id: int,
    process_score: float | None,
    *,
    finished_at: datetime | None = None,
    outcome_pnl: float | None = None,
) -> object:
    """Build a finished Attempt with a dynamic process_score attribute.

    outcome_pnl is accepted here only to populate the dynamic attr for
    test assertions — compute_progress must never read it.
    """
    from app.models.training import Attempt

    started = _NOW - timedelta(hours=1)
    finished = finished_at or _NOW

    a = Attempt(
        id=attempt_id,
        scenario_id=scenario_id,
        started_at=started,
        finished_at=finished,
    )
    if process_score is not None:
        object.__setattr__(a, "process_score", process_score)
    if outcome_pnl is not None:
        object.__setattr__(a, "outcome_pnl", outcome_pnl)
    return a


# --------------------------------------------------------------------------- #
# Test 1: unattempted archetypes return level=None, label="unattempted"       #
# --------------------------------------------------------------------------- #

def test_unattempted_archetype_is_null_level():
    """No attempts → every archetype has level=None, label='unattempted'."""
    from app.analytics.mastery import compute_progress

    report = compute_progress(attempts=[], scenarios=[], now=_NOW)

    assert len(report.mastery) > 0, "Must return mastery entries for all archetypes"
    for m in report.mastery:
        assert m.level is None, (
            f"Archetype '{m.archetype}' with no attempts should have level=None, "
            f"got {m.level}"
        )
        assert m.level_label == "unattempted", (
            f"Archetype '{m.archetype}' label should be 'unattempted', "
            f"got '{m.level_label}'"
        )
        assert m.rolling_mean_score is None
        assert m.attempts_count == 0


# --------------------------------------------------------------------------- #
# Test 2: mastery band parametrize                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("mean_score,expected_level,expected_label", [
    (0.40, 0, "learning"),
    (0.60, 1, "developing"),
    (0.78, 2, "proficient"),
    (0.92, 3, "mastered"),
])
def test_mastery_bands(mean_score, expected_level, expected_label):
    """Build 5 attempts on 'gap-and-go' with a mean landing in each band."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="gap-and-go")

    # Build 5 attempts with identical score = mean_score (mean == mean_score).
    attempts = []
    for i in range(5):
        ts = _NOW - timedelta(hours=5 - i)
        attempts.append(_attempt(i + 1, 1, mean_score, finished_at=ts))

    report = compute_progress(attempts=attempts, scenarios=[scenario], now=_NOW)

    mastery_map = {m.archetype: m for m in report.mastery}
    m = mastery_map.get("gap-and-go")
    assert m is not None, "gap-and-go archetype must be in mastery list"
    assert m.level == expected_level, (
        f"Mean score {mean_score}: expected level {expected_level}, got {m.level}"
    )
    assert m.level_label == expected_label, (
        f"Mean score {mean_score}: expected '{expected_label}', got '{m.level_label}'"
    )


# --------------------------------------------------------------------------- #
# Test 3: rolling window uses last N only                                      #
# --------------------------------------------------------------------------- #

def test_rolling_window_uses_last_n():
    """8 attempts: first 3 scored 0.1, last 5 scored 0.9 → mastered (level 3)."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="gap-and-go")
    attempts = []
    base = _NOW - timedelta(hours=10)

    # 3 bad attempts (scores 0.1) then 5 great attempts (scores 0.9).
    for i in range(3):
        attempts.append(_attempt(i + 1, 1, 0.10, finished_at=base + timedelta(hours=i)))
    for i in range(5):
        attempts.append(_attempt(i + 4, 1, 0.90, finished_at=base + timedelta(hours=i + 3)))

    report = compute_progress(attempts=attempts, scenarios=[scenario], now=_NOW)

    mastery_map = {m.archetype: m for m in report.mastery}
    m = mastery_map["gap-and-go"]
    assert m.level == 3, (
        f"Last 5 attempts all 0.9: expected mastered (level 3), got {m.level} "
        f"(mean={m.rolling_mean_score})"
    )
    assert m.level_label == "mastered"
    assert m.attempts_count == 8, "Should count ALL attempts, not just the window"


# --------------------------------------------------------------------------- #
# Test 4: process_streak counts consecutive high-scoring attempts              #
# --------------------------------------------------------------------------- #

def test_process_streak_counts_consecutive_high_scores():
    """Scores [0.8, 0.9, 0.75, 0.4, 0.9, 0.85] → current=2, longest=3."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="chop")
    base = _NOW - timedelta(hours=6)
    scores = [0.8, 0.9, 0.75, 0.4, 0.9, 0.85]

    attempts = [
        _attempt(i + 1, 1, s, finished_at=base + timedelta(hours=i))
        for i, s in enumerate(scores)
    ]

    report = compute_progress(attempts=attempts, scenarios=[scenario], now=_NOW)

    # First three (0.8, 0.9, 0.75) are all >= 0.7 → longest = 3.
    # Then 0.4 breaks. Then 0.9, 0.85 → current = 2.
    assert report.process_streak == 2, (
        f"Expected current streak=2, got {report.process_streak}"
    )
    assert report.longest_process_streak == 3, (
        f"Expected longest streak=3, got {report.longest_process_streak}"
    )


# --------------------------------------------------------------------------- #
# Test 5: anti-gambling — streak never uses outcome_pnl                       #
# --------------------------------------------------------------------------- #

def test_process_streak_never_uses_pnl():
    """Two attempts: identical process_score=0.9 but wildly different pnl.

    One is +1000, one is -1000. Both must count identically toward the streak.
    This is the core anti-gambling assertion for the mastery module.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="trend-day")
    base = _NOW - timedelta(hours=2)

    # Attempt 1: great process score, big winner.
    a1 = _attempt(1, 1, 0.90, finished_at=base, outcome_pnl=1000.0)
    # Attempt 2: identical process score, big loser.
    a2 = _attempt(2, 1, 0.90, finished_at=base + timedelta(hours=1), outcome_pnl=-1000.0)

    report = compute_progress(attempts=[a1, a2], scenarios=[scenario], now=_NOW)

    assert report.process_streak == 2, (
        f"Both attempts score 0.9 → streak should be 2 regardless of pnl. "
        f"Got streak={report.process_streak}. "
        f"outcome_pnl must NOT influence the streak."
    )
    assert report.longest_process_streak == 2


# --------------------------------------------------------------------------- #
# Test 6: due_review fires after failure + delay                               #
# --------------------------------------------------------------------------- #

def test_due_review_after_failure():
    """Archetype fails at day 0; not due at day 0, due at day 1."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="failed-breakout")
    day0 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)

    fail_attempt = _attempt(1, 1, 0.30, finished_at=day0)

    # Not due yet at the same moment.
    now_same_day = day0
    report_same = compute_progress(
        attempts=[fail_attempt], scenarios=[scenario], now=now_same_day
    )
    arch_due = {r.archetype for r in report_same.due_reviews}
    assert "failed-breakout" not in arch_due, (
        "Should NOT be due at day 0 (interval is +1 day)"
    )

    # Due at day+1.
    now_next_day = day0 + timedelta(days=1)
    report_due = compute_progress(
        attempts=[fail_attempt], scenarios=[scenario], now=now_next_day
    )
    due_archs = {r.archetype for r in report_due.due_reviews}
    assert "failed-breakout" in due_archs, (
        f"Should be due at day+1. due_reviews={report_due.due_reviews}"
    )

    due_item = next(r for r in report_due.due_reviews if r.archetype == "failed-breakout")
    assert due_item.suggested_seed is not None
    assert isinstance(due_item.suggested_seed, int)
    assert due_item.suggested_seed > 0


# --------------------------------------------------------------------------- #
# Test 7: passing attempt clears due status                                    #
# --------------------------------------------------------------------------- #

def test_due_review_cleared_by_passing():
    """Fail at day 0, pass at day 2, now = day 5 → NOT due."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="chop")
    day0 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    day2 = day0 + timedelta(days=2)
    day5 = day0 + timedelta(days=5)

    fail_attempt = _attempt(1, 1, 0.30, finished_at=day0)
    pass_attempt = _attempt(2, 1, 0.80, finished_at=day2)

    report = compute_progress(
        attempts=[fail_attempt, pass_attempt],
        scenarios=[scenario],
        now=day5,
    )
    due_archs = {r.archetype for r in report.due_reviews}
    assert "chop" not in due_archs, (
        f"Passing attempt at day 2 should have cleared the due status. "
        f"due_reviews={report.due_reviews}"
    )


# --------------------------------------------------------------------------- #
# Test 8: escalating schedule for repeated failures                            #
# --------------------------------------------------------------------------- #

def test_due_review_escalates_schedule():
    """3 consecutive failures: review offsets escalate +1, +3, +7 days.

    fail#1 at day 0  → review_count=1 → interval=+1 day  → due at day 1
    fail#2 at day 1  → review_count=2 → interval=+3 days → due at day 4
    fail#3 at day 4  → review_count=3 → interval=+7 days → due at day 11

    We test the 3-failure case: now = day 11 → due_at should be day 11.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="gap-and-go")
    day0 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    day1 = day0 + timedelta(days=1)
    day4 = day0 + timedelta(days=4)
    day11 = day0 + timedelta(days=11)

    # 3 consecutive failures (no passing attempt in between).
    a1 = _attempt(1, 1, 0.20, finished_at=day0)
    a2 = _attempt(2, 1, 0.30, finished_at=day1)
    a3 = _attempt(3, 1, 0.25, finished_at=day4)

    # At day 11 all three have failed; review_count=3 → interval=+7 from day4 = day11.
    report = compute_progress(
        attempts=[a1, a2, a3], scenarios=[scenario], now=day11
    )
    due_archs = {r.archetype for r in report.due_reviews}
    assert "gap-and-go" in due_archs, (
        f"After 3 consecutive failures, archetype should be due at day 11. "
        f"due_reviews={report.due_reviews}"
    )

    due_item = next(r for r in report.due_reviews if r.archetype == "gap-and-go")
    assert due_item.review_count == 3, (
        f"Expected review_count=3, got {due_item.review_count}"
    )

    # Verify escalation: at day 10 (one day before day 11) should NOT be due yet.
    day10 = day0 + timedelta(days=10, hours=23)
    report_before = compute_progress(
        attempts=[a1, a2, a3], scenarios=[scenario], now=day10
    )
    due_archs_before = {r.archetype for r in report_before.due_reviews}
    assert "gap-and-go" not in due_archs_before, (
        "Should not be due 1 hour before the 7-day interval completes"
    )


# --------------------------------------------------------------------------- #
# Test 9: suggested_seed is deterministic                                      #
# --------------------------------------------------------------------------- #

def test_suggested_seed_is_deterministic():
    """Same archetype + review_count → same seed; different count → different seed."""
    from app.analytics.mastery import _deterministic_seed

    seed_a = _deterministic_seed("gap-and-go", 1)
    seed_b = _deterministic_seed("gap-and-go", 1)
    assert seed_a == seed_b, "Same inputs must always produce the same seed"

    seed_c = _deterministic_seed("gap-and-go", 2)
    assert seed_a != seed_c, (
        f"Different review_count should produce a different seed: "
        f"count=1 → {seed_a}, count=2 → {seed_c}"
    )

    seed_d = _deterministic_seed("failed-breakout", 1)
    assert seed_a != seed_d, (
        f"Different archetype should produce a different seed: "
        f"gap-and-go:{seed_a} vs failed-breakout:{seed_d}"
    )


# --------------------------------------------------------------------------- #
# Test 10: ProgressReport serializes to dict (JSON round-trip)                #
# --------------------------------------------------------------------------- #

def test_progress_report_serializes():
    """JSON round-trip of ProgressReport must succeed and have expected keys."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="gap-and-go")
    a1 = _attempt(1, 1, 0.90, finished_at=_NOW - timedelta(hours=1))

    report = compute_progress(attempts=[a1], scenarios=[scenario], now=_NOW)
    d = report.to_dict()

    # Must be JSON-serializable.
    json_str = json.dumps(d)
    parsed = json.loads(json_str)

    assert "mastery" in parsed
    assert "process_streak" in parsed
    assert "longest_process_streak" in parsed
    assert "due_reviews" in parsed
    assert "quests" in parsed
    assert "generated_at" in parsed

    assert isinstance(parsed["mastery"], list)
    assert len(parsed["mastery"]) > 0

    gap_mastery = next(m for m in parsed["mastery"] if m["archetype"] == "gap-and-go")
    assert "level" in gap_mastery
    assert "level_label" in gap_mastery
    assert "rolling_mean_score" in gap_mastery
    assert "attempts_count" in gap_mastery


# --------------------------------------------------------------------------- #
# Test 11: deterministic given injected now                                    #
# --------------------------------------------------------------------------- #

def test_progress_deterministic_given_now():
    """Same attempts + same injected now → identical report."""
    from app.analytics.mastery import compute_progress

    scenario = _scenario(1, archetype="trend-day")
    a1 = _attempt(1, 1, 0.80, finished_at=_NOW - timedelta(hours=2))
    a2 = _attempt(2, 1, 0.40, finished_at=_NOW - timedelta(hours=1))

    r1 = compute_progress(attempts=[a1, a2], scenarios=[scenario], now=_NOW)
    r2 = compute_progress(attempts=[a1, a2], scenarios=[scenario], now=_NOW)

    assert r1.generated_at == r2.generated_at
    assert r1.process_streak == r2.process_streak
    assert r1.longest_process_streak == r2.longest_process_streak
    assert [m.level for m in r1.mastery] == [m.level for m in r2.mastery]
    assert [d.archetype for d in r1.due_reviews] == [d.archetype for d in r2.due_reviews]


# --------------------------------------------------------------------------- #
# Endpoint integration helpers                                                 #
# --------------------------------------------------------------------------- #

def _seed_scenario_and_finished_attempt(
    engine,
    *,
    archetype: str = "gap-and-go",
    process_score: float = 0.85,
) -> tuple[int, int]:
    """Insert a scenario + finished attempt; return (scenario_id, attempt_id)."""
    from app.db.connection import init_db
    from app.db.schema import scenarios as scenarios_t, exercise_attempts as attempts_t

    init_db(engine)
    now = datetime.now(UTC).replace(tzinfo=None)

    answer_key_json = {
        "correct_actions": ["skip"],
        "explanation": "test",
        "management_notes": [],
    }

    with engine.begin() as conn:
        r = conn.execute(
            scenarios_t.insert().values(
                title=f"Test {archetype}",
                archetype=archetype,
                source_type="synthetic",
                drill_type="decision-point",
                symbol=None,
                session_date=None,
                synth_seed=1,
                synth_params_json=None,
                start_bar=0,
                decision_bar=5,
                end_bar=20,
                annotations_json=[],
                answer_key_json=answer_key_json,
                checklist_template_id=None,
                source_trade_id=None,
                blind_default=False,
                active=True,
                created_at=now,
            )
        )
        scenario_id = int(r.inserted_primary_key[0])

    with engine.begin() as conn:
        r = conn.execute(
            attempts_t.insert().values(
                scenario_id=scenario_id,
                started_at=now,
                finished_at=now,
                mood_checkin=None,
                actions_json=[],
                process_score=process_score,
                outcome_pnl=None,
                checklist_json=None,
                verdict=None,
                notes=None,
            )
        )
        attempt_id = int(r.inserted_primary_key[0])

    return scenario_id, attempt_id


# --------------------------------------------------------------------------- #
# Test 12: endpoint returns progress end-to-end                               #
# --------------------------------------------------------------------------- #

def test_endpoint_returns_progress(isolated_db):
    """Seed a finished attempt; GET /api/training/progress → 200, mastery list."""
    from app.db.connection import get_engine
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = get_engine()
    _seed_scenario_and_finished_attempt(engine, archetype="gap-and-go", process_score=0.90)
    _seed_scenario_and_finished_attempt(engine, archetype="chop", process_score=0.50)

    client = TestClient(create_app())
    r = client.get("/api/training/progress")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    body = r.json()
    assert "mastery" in body, "Response must contain 'mastery' key"
    assert "process_streak" in body
    assert "due_reviews" in body
    assert "generated_at" in body

    # All canonical archetypes must appear.
    from app.models.training import Archetype
    all_archetypes = {a.value for a in Archetype}
    returned_archetypes = {m["archetype"] for m in body["mastery"]}
    assert all_archetypes <= returned_archetypes, (
        f"Missing archetypes in response: {all_archetypes - returned_archetypes}"
    )

    # The seeded gap-and-go attempt (0.90) should be mastered.
    gap_mastery = next(
        (m for m in body["mastery"] if m["archetype"] == "gap-and-go"), None
    )
    assert gap_mastery is not None
    assert gap_mastery["level"] == 3, (
        f"gap-and-go with score 0.90 should be mastered (level 3), "
        f"got {gap_mastery['level']}"
    )

    # The seeded chop attempt (0.50) should be developing.
    chop_mastery = next(
        (m for m in body["mastery"] if m["archetype"] == "chop"), None
    )
    assert chop_mastery is not None
    assert chop_mastery["level"] == 1, (
        f"chop with score 0.50 should be developing (level 1), "
        f"got {chop_mastery['level']}"
    )


# --------------------------------------------------------------------------- #
# Test 13: endpoint returns 501 when mastery module absent                    #
# --------------------------------------------------------------------------- #

def test_endpoint_501_when_module_absent(isolated_db, monkeypatch):
    """Monkeypatch mastery module to None → GET /training/progress returns 501."""
    monkeypatch.setitem(sys.modules, "app.analytics.mastery", None)

    from app.main import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    r = client.get("/api/training/progress")
    assert r.status_code == 501, (
        f"Expected 501 when mastery module absent, got {r.status_code}: {r.text}"
    )
