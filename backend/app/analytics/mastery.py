"""Mastery model for the training module (M6 task 6.4.1).

Public entry
------------
compute_progress(attempts, scenarios, *, now) -> ProgressReport

Design contract
---------------
* Score PROCESS, never outcome (PLAN-M6-TRAINING.md §0 principle 1).
  outcome_pnl is never read by this module. Every mastery level, streak, and
  scheduling signal is derived solely from process_score. A user who follows
  rules and loses money advances; one who breaks rules and profits does not.
  The C4 anti-gambling audit (6.4.3) greps for 'outcome_pnl' — any reference
  here would be a false positive. There is intentionally none.

* `now` is INJECTED — never call datetime.now() inside compute_progress.
  Determinism + testability: the route supplies datetime.now at the boundary
  and passes it in. Tests pass fixed datetimes to exercise scheduling logic.

Mastery levels
--------------
Per-archetype rolling window of the last N (default 5) FINISHED attempts,
ordered by finished_at ascending. Mean process_score over the window maps to:
    0.00 <= mean < 0.50  ->  level 0  "learning"
    0.50 <= mean < 0.70  ->  level 1  "developing"
    0.70 <= mean < 0.85  ->  level 2  "proficient"
    0.85 <= mean <= 1.0  ->  level 3  "mastered"
An archetype with zero attempts returns level=None, label="unattempted".

Discipline streak
-----------------
Consecutive most-recent finished attempts (across ALL archetypes, sorted by
finished_at ascending) with process_score >= 0.7. Resets on a sub-0.7 attempt.
This is a DISCIPLINE streak, never a win streak.

SM-2-lite spaced-repetition scheduler
--------------------------------------
An archetype is 'due for review' when:
1. Its most recent finished attempt scored < 0.7 (a failure), AND
2. Enough time has elapsed since that failure per an escalating schedule:
       review_count 0 (first failure)  : due after +1 day
       review_count 1 (second failure) : due after +3 days
       review_count 2+ (third+)        : due after +7 days
   review_count tracks consecutive uncleared failures on the archetype.
3. A PASSING attempt (>= 0.7) CLEARS the due status immediately — the
   review_count resets to 0 for that archetype.

Due items prescribe a FRESH instance: the suggested_seed is derived
deterministically from (archetype_name, review_count) via a stable hash so
the user doesn't replay the same tape repeatedly.

Weekly review quests (v1 minimal)
----------------------------------
v1 returns an empty quests list — quest inputs (graded/tagged trade counts for
the current week) are not plumbed through this function's parameter set yet.
The Quest dataclass is defined so task 6.4.2 can populate it without changing
the ProgressReport shape.
"""
from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.training import Attempt, Archetype, ScenarioPublic


# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

# Rolling window size for mastery computation.
_ROLLING_N: int = 5

# Mastery band boundaries (inclusive lower, exclusive upper — except top).
_BANDS: list[tuple[float, float, int, str]] = [
    (0.00, 0.50, 0, "learning"),
    (0.50, 0.70, 1, "developing"),
    (0.70, 0.85, 2, "proficient"),
    (0.85, 1.01, 3, "mastered"),  # 1.01 so 1.0 lands in mastered
]

# Process-score threshold for passing (streak + SM-2 pass/fail gate).
_PASS_THRESHOLD: float = 0.70

# SM-2-lite review intervals in days, indexed by review_count (0, 1, 2+).
_REVIEW_INTERVALS: list[int] = [1, 3, 7]


# --------------------------------------------------------------------------- #
# Public dataclasses                                                           #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ArchetypeMastery:
    """Mastery snapshot for a single archetype."""

    archetype: str
    level: int | None            # 0-3, or None if unattempted
    level_label: str             # learning | developing | proficient | mastered | unattempted
    rolling_mean_score: float | None
    attempts_count: int

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class DueReview:
    """An archetype that is due for spaced-repetition review."""

    archetype: str
    last_score: float
    failed_at: str            # ISO-8601
    due_at: str               # ISO-8601
    review_count: int
    suggested_seed: int       # deterministic fresh tape seed

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Quest:
    """A weekly review quest (v1: populated by later task 6.4.2)."""

    key: str           # grade_all | tag_all | debrief_n
    label: str
    target: int
    progress: int
    done: bool

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class ProgressReport:
    """Full progress snapshot for a single user."""

    mastery: list[ArchetypeMastery]
    process_streak: int           # current consecutive discipline streak
    longest_process_streak: int
    due_reviews: list[DueReview]
    quests: list[Quest]
    generated_at: str             # ISO-8601

    def to_dict(self) -> dict:
        return {
            "mastery": [m.to_dict() for m in self.mastery],
            "process_streak": self.process_streak,
            "longest_process_streak": self.longest_process_streak,
            "due_reviews": [r.to_dict() for r in self.due_reviews],
            "quests": [q.to_dict() for q in self.quests],
            "generated_at": self.generated_at,
        }


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _level_from_mean(mean: float) -> tuple[int, str]:
    """Map a mean process_score to (level_int, label)."""
    for lo, hi, level, label in _BANDS:
        if lo <= mean < hi:
            return level, label
    # Defensive: clamp to top band (mean == 1.0 hits the 1.01 upper bound)
    return 3, "mastered"


def _review_interval(review_count: int) -> int:
    """Return the due-review interval in days for a given review_count."""
    idx = min(review_count, len(_REVIEW_INTERVALS) - 1)
    return _REVIEW_INTERVALS[idx]


def _deterministic_seed(archetype: str, review_count: int) -> int:
    """Derive a stable positive integer seed from (archetype, review_count).

    Uses SHA-256 of the canonical string representation, truncated to 31 bits
    to stay comfortably within a signed 32-bit range (safe for any downstream
    seed consumer).  Same inputs always produce the same seed; different inputs
    produce different seeds with high probability.
    """
    raw = f"{archetype}:{review_count}"
    digest = hashlib.sha256(raw.encode()).hexdigest()
    # Take the first 8 hex chars (32 bits), mask to 31 bits for positive int.
    return int(digest[:8], 16) & 0x7FFFFFFF


def _get_process_score(attempt: "Attempt") -> float | None:
    """Extract process_score from an attempt.

    The Attempt Pydantic model does not carry process_score (it's returned only
    via AttemptFinishResponse). It may be attached as a dynamic attribute by
    finish_attempt (via object.__setattr__) or a DB-enrichment step. If absent,
    treat the attempt as unscored — excluded from mastery computation.

    NOTE: outcome_pnl is NEVER read here, by design.
    """
    return getattr(attempt, "process_score", None)


def _build_scenario_index(
    scenarios: "list[ScenarioPublic]",
) -> dict[int, str]:
    """Map scenario_id -> archetype value string."""
    return {s.id: s.archetype.value for s in scenarios}


def _finished_attempts_with_score(
    attempts: "list[Attempt]",
) -> list[tuple["Attempt", float]]:
    """Return finished attempts with a non-None process_score.

    Sorted ascending by finished_at. Unfinished attempts and those without
    a process_score are excluded — mastery is about completed, scored drills.
    """
    scored: list[tuple["Attempt", float]] = []
    for a in attempts:
        if a.finished_at is None:
            continue
        score = _get_process_score(a)
        if score is None:
            continue
        scored.append((a, score))
    # Sort ascending so rolling window and streak logic see time-ordered sequence.
    scored.sort(key=lambda t: t[0].finished_at)  # type: ignore[arg-type]
    return scored


# --------------------------------------------------------------------------- #
# Mastery computation                                                          #
# --------------------------------------------------------------------------- #

def _compute_mastery(
    scored_by_archetype: dict[str, list[float]],
    all_archetypes: list[str],
) -> list[ArchetypeMastery]:
    """Compute per-archetype mastery from pre-grouped score lists.

    scored_by_archetype maps archetype_value -> list[process_score] ordered
    ascending by finished_at (most recent is LAST). Only the final _ROLLING_N
    are used for the mean.
    """
    result: list[ArchetypeMastery] = []
    for arch in all_archetypes:
        scores = scored_by_archetype.get(arch, [])
        count = len(scores)
        if count == 0:
            result.append(ArchetypeMastery(
                archetype=arch,
                level=None,
                level_label="unattempted",
                rolling_mean_score=None,
                attempts_count=0,
            ))
            continue

        window = scores[-_ROLLING_N:]
        mean = sum(window) / len(window)
        level, label = _level_from_mean(mean)
        result.append(ArchetypeMastery(
            archetype=arch,
            level=level,
            level_label=label,
            rolling_mean_score=round(mean, 6),
            attempts_count=count,
        ))
    return result


# --------------------------------------------------------------------------- #
# Streak computation                                                           #
# --------------------------------------------------------------------------- #

def _compute_streaks(
    scored: list[tuple["Attempt", float]],
) -> tuple[int, int]:
    """Return (current_streak, longest_streak) from time-ordered scored attempts.

    Iterates the full sequence (ascending by finished_at). current_streak is
    the count of the most-recent consecutive attempts with process_score >= 0.7.
    longest_streak is the maximum run seen anywhere in the history.

    Only process_score drives this — outcome_pnl is never read.
    """
    current_streak: int = 0
    longest_streak: int = 0
    run: int = 0

    for _attempt, score in scored:
        if score >= _PASS_THRESHOLD:
            run += 1
            if run > longest_streak:
                longest_streak = run
        else:
            run = 0

    current_streak = run
    return current_streak, longest_streak


# --------------------------------------------------------------------------- #
# SM-2-lite scheduler                                                          #
# --------------------------------------------------------------------------- #

def _compute_due_reviews(
    scored_by_archetype: dict[str, list[tuple["Attempt", float]]],
    now: datetime,
) -> list[DueReview]:
    """Compute the due-review queue for all archetypes with failures.

    For each archetype we scan the time-ordered attempt+score pairs to
    determine:
    - Whether the most-recent attempt is a failure (< 0.7).
    - The review_count (consecutive uncleared failures from the tail).
    - Whether enough time has elapsed per the SM-2-lite schedule.

    A passing attempt (>= 0.7) clears all prior failures for that archetype
    (review_count resets to 0).

    Returns DueReview entries only for archetypes that are currently due.
    """
    due: list[DueReview] = []

    for arch, pairs in sorted_by_archetype(scored_by_archetype):
        if not pairs:
            continue

        # Walk from the most-recent attempt backwards to count consecutive fails.
        review_count = 0
        last_fail_attempt: "Attempt | None" = None
        last_fail_score: float = 0.0

        for attempt, score in reversed(pairs):
            if score >= _PASS_THRESHOLD:
                # A pass clears the failure run — no review due.
                break
            # It's a failure.
            if last_fail_attempt is None:
                last_fail_attempt = attempt
                last_fail_score = score
            review_count += 1

        if last_fail_attempt is None:
            # Most-recent attempt was a pass — not due.
            continue

        # Compute due_at based on review_count.
        interval_days = _review_interval(review_count - 1)  # 0-indexed for the table
        # finished_at may be tz-naive (stored as naive UTC by training_store).
        failed_at_dt = last_fail_attempt.finished_at
        if failed_at_dt is None:
            continue

        # Normalise: ensure we can compare with injected `now` regardless of tz.
        failed_at_aware: datetime
        if failed_at_dt.tzinfo is None:
            failed_at_aware = failed_at_dt.replace(tzinfo=timezone.utc)
        else:
            failed_at_aware = failed_at_dt

        now_aware: datetime
        if now.tzinfo is None:
            now_aware = now.replace(tzinfo=timezone.utc)
        else:
            now_aware = now

        due_at_dt = failed_at_aware + timedelta(days=interval_days)

        if now_aware < due_at_dt:
            # Not yet due.
            continue

        # Build suggested_seed: derived from (archetype, review_count) so each
        # review cycle gets a fresh tape without repeating the prior one.
        seed = _deterministic_seed(arch, review_count)

        due.append(DueReview(
            archetype=arch,
            last_score=last_fail_score,
            failed_at=failed_at_aware.isoformat(),
            due_at=due_at_dt.isoformat(),
            review_count=review_count,
            suggested_seed=seed,
        ))

    # Sort by archetype for deterministic output.
    due.sort(key=lambda d: d.archetype)
    return due


def sorted_by_archetype(
    d: dict[str, list],
) -> list[tuple[str, list]]:
    """Return dict items sorted by key (archetype) for determinism."""
    return sorted(d.items(), key=lambda kv: kv[0])


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #

def compute_progress(
    attempts: "list[Attempt]",
    scenarios: "list[ScenarioPublic]",
    *,
    now: datetime,
) -> ProgressReport:
    """Per-archetype mastery + due-review queue + streaks, all from process_score.

    Parameters
    ----------
    attempts  : All attempts (finished or not); unfinished ones are skipped.
    scenarios : All scenarios (active=None), used to map scenario_id -> archetype.
    now       : Injected current UTC datetime — NEVER call datetime.now() here.
                Tests pass fixed datetimes to exercise scheduling logic.

    Returns
    -------
    ProgressReport with per-archetype mastery, discipline streak, SM-2-lite
    due-review queue, and weekly quests (v1: empty list).

    Anti-gambling guarantee: outcome_pnl is NEVER accessed in this function or
    any helper it calls. All signals derive exclusively from process_score.
    """
    from app.models.training import Archetype

    # All canonical archetypes, sorted for deterministic output.
    all_archetypes: list[str] = sorted(a.value for a in Archetype)

    # Build scenario -> archetype mapping.
    scenario_to_archetype = _build_scenario_index(scenarios)

    # Collect finished, scored attempts; sort ascending by finished_at.
    scored_global = _finished_attempts_with_score(attempts)

    # Group by archetype.
    # scored_by_archetype_scores: archetype -> list[float] (for mastery mean)
    # scored_by_archetype_pairs: archetype -> list[(Attempt, float)] (for SM-2)
    scored_by_archetype_scores: dict[str, list[float]] = {}
    scored_by_archetype_pairs: dict[str, list[tuple["Attempt", float]]] = {}

    for attempt, score in scored_global:
        arch = scenario_to_archetype.get(attempt.scenario_id)
        if arch is None:
            # Attempt references an unknown scenario — skip it.
            continue
        scored_by_archetype_scores.setdefault(arch, []).append(score)
        scored_by_archetype_pairs.setdefault(arch, []).append((attempt, score))

    # Mastery per archetype.
    mastery = _compute_mastery(scored_by_archetype_scores, all_archetypes)

    # Discipline streak (across all archetypes, time-ordered).
    current_streak, longest_streak = _compute_streaks(scored_global)

    # SM-2-lite due-review queue.
    due_reviews = _compute_due_reviews(scored_by_archetype_pairs, now)

    # Weekly quests — v1: empty (quest inputs not yet wired to this function).
    quests: list[Quest] = []

    return ProgressReport(
        mastery=mastery,
        process_streak=current_streak,
        longest_process_streak=longest_streak,
        due_reviews=due_reviews,
        quests=quests,
        generated_at=now.isoformat() if now.tzinfo is not None
                     else now.replace(tzinfo=timezone.utc).isoformat(),
    )
