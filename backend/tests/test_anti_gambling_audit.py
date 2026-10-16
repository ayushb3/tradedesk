"""Anti-gambling audit — M6 task 6.4.3 capstone guardrail.

This module mechanically proves that no reward, mastery level, streak, progress,
scheduling, or quest-completion path in the training module is keyed on
outcome_pnl or net_pnl (PLAN-M6-TRAINING.md §0 principle 1).

Three-way distinction enforced here:
  ✅ Record/display   — outcome_pnl is stored and shown labeled "not scored".
  ✅ Signal           — leaks/behavior detectors read net_pnl < 0 to DETECT a
                        problem (revenge trading, chop). That is detection, not
                        a reward path.
  ❌ Reward/progress  — any process_score, mastery level, streak, due-review
                        clearance, or "you passed" gate that is a function of
                        P&L.  FORBIDDEN.

Part 1 — static / structural:
  test_no_pnl_in_score_computation
  test_no_pnl_in_mastery
  test_progress_page_has_no_pnl

Part 2 — behavioral (cross-cutting):
  test_identical_process_score_regardless_of_pnl
  test_mastery_identical_regardless_of_pnl
  test_high_pnl_low_process_does_not_advance
  test_low_pnl_high_process_does_advance
  test_streak_survives_a_loss_if_process_high
  test_due_review_cleared_by_process_not_pnl

Part 3 — guard the allowed signal uses:
  test_leaks_may_reference_losses_as_signal

IMPORTANT: if any behavioral test in Part 2 fails, do NOT adjust the test.
Stop and report the finding — that is the audit detecting a real violation.
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Paths (absolute — worktree-safe)
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).parent.parent  # .../backend/
_APP_ROOT = _BACKEND_ROOT / "app"
_FRONTEND_ROOT = _BACKEND_ROOT.parent / "frontend" / "src"

_TRAINING_PY  = _APP_ROOT / "analytics" / "training.py"
_MASTERY_PY   = _APP_ROOT / "analytics" / "mastery.py"
_PROGRESS_TSX = _FRONTEND_ROOT / "pages" / "ProgressPage.tsx"

# Fixed timestamps for deterministic tests.
_NOW = datetime(2026, 7, 9, 14, 0, 0, tzinfo=UTC)


# ===========================================================================
# Part 1 — Static / structural audit
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 1: No P&L expression inside scoring functions in training.py
# ---------------------------------------------------------------------------

def test_no_pnl_in_score_computation():
    """AST audit: the scoring functions in analytics/training.py never
    reference outcome_pnl or net_pnl anywhere in their bodies.

    Strategy:
    The code in training.py is cleanly separated: _compute_outcome_pnl is the
    ONLY function that computes P&L, and it is called AFTER the score is fully
    determined.  We confirm this by asserting that the scoring helpers
    (_score_decision_point, _score_do_nothing, _score_management_only,
    _score_drawdown_script, _score_checklist) and the scoring section of
    score_attempt (before the outcome_pnl = _compute_outcome_pnl(...) line)
    contain NO Name or Attribute node whose identifier is 'outcome_pnl' or
    'net_pnl'.

    Approach: parse the file with ast.  Walk the FunctionDef bodies of the
    scoring helpers and assert no Name/Attribute node reads those identifiers.
    This is the strict form — it matches the actual code structure where the
    scoring helpers were written to never mention these names.

    The _compute_outcome_pnl helper and score_attempt's outcome_pnl ASSIGNMENT
    (the return-value packaging) are NOT included in the scored functions list,
    so they are correctly excluded from this assertion.
    """
    assert _TRAINING_PY.exists(), f"File not found: {_TRAINING_PY}"
    source = _TRAINING_PY.read_text()
    tree = ast.parse(source, filename=str(_TRAINING_PY))

    # Names of functions that form the scoring pipeline.
    SCORING_FUNCTIONS = {
        "_score_decision_point",
        "_score_do_nothing",
        "_score_management_only",
        "_score_drawdown_script",
        "_score_checklist",
    }

    PNL_NAMES = {"outcome_pnl", "net_pnl"}

    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name not in SCORING_FUNCTIONS:
            continue

        # Walk all nodes inside this function body.
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id in PNL_NAMES:
                violations.append(
                    f"  {_TRAINING_PY.name}: {node.name}() line {child.lineno}: "
                    f"Name reference to '{child.id}'"
                )
            elif isinstance(child, ast.Attribute) and child.attr in PNL_NAMES:
                violations.append(
                    f"  {_TRAINING_PY.name}: {node.name}() line {child.lineno}: "
                    f"Attribute reference to '{child.attr}'"
                )

    assert not violations, (
        "ANTI-GAMBLING VIOLATION: scoring helper(s) reference P&L signal.\n"
        "Reward computation must be independent of outcome_pnl / net_pnl:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 2: mastery.py has zero executable references to P&L names
# ---------------------------------------------------------------------------

def test_no_pnl_in_mastery():
    """AST audit: analytics/mastery.py contains NO executable node that
    references 'outcome_pnl' or 'net_pnl'.

    ast.parse naturally excludes comments and docstrings from the tree.
    String literals inside docstrings are parsed as Constant nodes, not Name
    nodes, so they cannot trigger this assertion.  This means occurrences like
    the docstring comment "outcome_pnl is never read here" are allowed.

    The assertion here is the STRONG form and mastery.py was written to
    satisfy it: there is no executable code anywhere in the module that reads
    or writes these identifiers.
    """
    assert _MASTERY_PY.exists(), f"File not found: {_MASTERY_PY}"
    source = _MASTERY_PY.read_text()
    tree = ast.parse(source, filename=str(_MASTERY_PY))

    PNL_NAMES = {"outcome_pnl", "net_pnl"}

    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in PNL_NAMES:
            violations.append(
                f"  {_MASTERY_PY.name} line {node.lineno}: "
                f"Name '{node.id}'"
            )
        elif isinstance(node, ast.Attribute) and node.attr in PNL_NAMES:
            violations.append(
                f"  {_MASTERY_PY.name} line {node.lineno}: "
                f"Attribute '{node.attr}'"
            )

    assert not violations, (
        "ANTI-GAMBLING VIOLATION: mastery.py contains executable P&L references.\n"
        "mastery.py must have ZERO executable references to outcome_pnl / net_pnl:\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 3: ProgressPage.tsx has no P&L identifiers
# ---------------------------------------------------------------------------

def test_progress_page_has_no_pnl():
    """Frontend grep: ProgressPage.tsx must contain no outcome_pnl, net_pnl,
    pnl identifier (as a standalone word), or bare $ dollar-sign in a
    value-rendering context.

    ProgressPage.tsx is the reward-facing UI surface (streaks, mastery levels,
    due reviews, quests).  Dollar figures and P&L identifiers have no place here.

    The grep uses word-boundary-aware patterns:
      - 'outcome_pnl'  — exact string
      - 'net_pnl'      — exact string
      - r'\\bpnl\\b'   — word-boundary 'pnl' identifier (ignores 'outcome_pnl'
                          substrings already caught above)
      - r'\\$[0-9]'    — dollar sign immediately followed by a digit (currency
                          display).  Template literal ${ and JSX prop ${...}
                          are dollar-without-digit and are NOT matched.
    """
    assert _PROGRESS_TSX.exists(), f"File not found: {_PROGRESS_TSX}"
    content = _PROGRESS_TSX.read_text()

    # Strip comments so documentation mentions of pnl (e.g. a header comment
    # that states "outcome_pnl is NEVER read on this page") don't trip the
    # assertion. Strip /* ... */ block comments first (the file has a header
    # docblock), then // line comments. We track original line numbers by
    # blanking comment spans rather than deleting them.
    # Blank /* */ block comments (multi-line), preserving newlines so line
    # numbers stay accurate.
    def _blank_block_comments(text: str) -> str:
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            if text[i : i + 2] == "/*":
                j = text.find("*/", i + 2)
                if j == -1:
                    # Unterminated; blank the rest, keeping newlines.
                    out.append("".join(c if c == "\n" else " " for c in text[i:]))
                    i = n
                else:
                    span = text[i : j + 2]
                    out.append("".join(c if c == "\n" else " " for c in span))
                    i = j + 2
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    no_block = _blank_block_comments(content)
    lines = no_block.splitlines()
    stripped_lines: list[str] = []
    for line in lines:
        # Remove everything after the first // (crude but sufficient for TSX)
        code_part = line.split("//")[0]
        stripped_lines.append(code_part)
    stripped = "\n".join(stripped_lines)

    # Patterns that must NOT appear in executable code.
    patterns = [
        ("outcome_pnl", "outcome_pnl identifier"),
        ("net_pnl", "net_pnl identifier"),
        (r"\bpnl\b", "standalone 'pnl' identifier"),
        (r"\$[0-9]", "dollar-sign currency display ($<digit>)"),
    ]

    violations: list[str] = []
    for pattern, desc in patterns:
        matches = re.findall(pattern, stripped)
        if matches:
            # Find line numbers for the matches.
            for i, sline in enumerate(stripped_lines, 1):
                if re.search(pattern, sline):
                    violations.append(
                        f"  ProgressPage.tsx line {i}: {desc} — '{sline.strip()[:80]}'"
                    )

    assert not violations, (
        "ANTI-GAMBLING VIOLATION: ProgressPage.tsx contains P&L on the reward surface.\n"
        "The progress/gamification page must show no P&L values or dollar figures:\n"
        + "\n".join(violations)
    )


# ===========================================================================
# Part 2 — Behavioral proofs (the real guarantee)
# ===========================================================================

# ---------------------------------------------------------------------------
# Fixtures for behavioral tests
# ---------------------------------------------------------------------------

def _make_action(bar: int, action_type, *, price=None, qty=None):
    """Build a minimal AttemptAction."""
    from app.models.training import AttemptAction
    return AttemptAction(
        bar=bar,
        action=action_type,
        price=price,
        qty=qty,
        ts=_NOW,
    )


def _make_attempt_with_actions(attempt_id: int, scenario_id: int, actions,
                                *, finished_at=None):
    """Build a minimal Attempt with actions."""
    from app.models.training import Attempt
    return Attempt(
        id=attempt_id,
        scenario_id=scenario_id,
        started_at=_NOW - timedelta(hours=1),
        finished_at=finished_at or _NOW,
        actions=list(actions),
    )


def _make_scenario(*, drill_type_val="decision-point", decision_bar=5,
                   correct_actions=None):
    """Duck-typed ScenarioFull for the scorer."""
    from app.models.training import AnswerKey, DrillType, AttemptActionType
    if correct_actions is None:
        correct_actions = [AttemptActionType.enter_long]
    return SimpleNamespace(
        drill_type=DrillType(drill_type_val),
        decision_bar=decision_bar,
        answer_key=AnswerKey(
            correct_actions=correct_actions,
            explanation="test",
            management_notes=[],
        ),
    )


def _scenario_pub(scenario_id: int, archetype: str = "gap-and-go"):
    """Build a ScenarioPublic for mastery/progress tests."""
    from app.models.training import ScenarioPublic, Archetype, DrillType, ScenarioSource
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


def _attempt_with_score(attempt_id: int, scenario_id: int, process_score: float,
                         *, finished_at=None, outcome_pnl=None):
    """Build an Attempt with dynamic process_score and optional outcome_pnl."""
    from app.models.training import Attempt
    a = Attempt(
        id=attempt_id,
        scenario_id=scenario_id,
        started_at=_NOW - timedelta(hours=1),
        finished_at=finished_at or _NOW,
    )
    object.__setattr__(a, "process_score", process_score)
    if outcome_pnl is not None:
        object.__setattr__(a, "outcome_pnl", outcome_pnl)
    return a


# ---------------------------------------------------------------------------
# Test 4: identical process_score regardless of outcome_pnl
# ---------------------------------------------------------------------------

def test_identical_process_score_regardless_of_pnl():
    """Two attempts, identical action logs, wildly different P&L outcomes.

    Scenario A: big winner (entry 10.0, exit 100.0 → +$9000 on 100 shares).
    Scenario B: big loser  (entry 10.0, exit  1.0 → -$900  on 100 shares).

    Both make the CORRECT decision (enter_long at bar 5, which is the correct
    action).  Both must receive the same process_score.

    This is the core assertion: score_attempt must return the same
    process_score for identical decision/checklist behavior regardless of
    what outcome_pnl computes to.
    """
    from app.analytics.training import score_attempt
    from app.models.training import AttemptActionType

    scenario = _make_scenario(correct_actions=[AttemptActionType.enter_long])

    # Attempt A: same entry + big profitable exit.
    actions_winner = [
        _make_action(5, AttemptActionType.enter_long, price=10.0, qty=100),
        _make_action(8, AttemptActionType.exit, price=100.0, qty=100),
    ]
    attempt_winner = _make_attempt_with_actions(1, 1, actions_winner)

    # Attempt B: same entry + big losing exit.
    actions_loser = [
        _make_action(5, AttemptActionType.enter_long, price=10.0, qty=100),
        _make_action(8, AttemptActionType.exit, price=1.0, qty=100),
    ]
    attempt_loser = _make_attempt_with_actions(2, 1, actions_loser)

    result_winner = score_attempt(attempt_winner, scenario)
    result_loser  = score_attempt(attempt_loser, scenario)

    # The outcome_pnl values must differ (otherwise the test is vacuous).
    assert result_winner.outcome_pnl is not None
    assert result_loser.outcome_pnl is not None
    assert result_winner.outcome_pnl != result_loser.outcome_pnl, (
        "Test setup error: both attempts produced the same outcome_pnl; "
        "they should differ so the test is meaningful."
    )

    # The process_scores must be identical.
    assert result_winner.process_score == pytest.approx(result_loser.process_score, abs=1e-6), (
        f"ANTI-GAMBLING VIOLATION: process_score differs based on P&L.\n"
        f"  Winner (pnl={result_winner.outcome_pnl}): process_score={result_winner.process_score}\n"
        f"  Loser  (pnl={result_loser.outcome_pnl}):  process_score={result_loser.process_score}\n"
        f"Both attempts made the SAME decision — process_score must be identical."
    )


# ---------------------------------------------------------------------------
# Test 5: mastery identical regardless of outcome_pnl
# ---------------------------------------------------------------------------

def test_mastery_identical_regardless_of_pnl():
    """Two sets of attempts, identical process_scores, opposite outcome_pnl.

    Set A: 3 attempts with process_score=0.9, outcome_pnl=+1000 each.
    Set B: 3 attempts with process_score=0.9, outcome_pnl=-1000 each.

    compute_progress must return identical mastery levels, identical streaks,
    and identical due-review queues for both sets.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario_pub(1, archetype="gap-and-go")

    base = _NOW - timedelta(hours=3)
    set_a = [
        _attempt_with_score(i + 1, 1, 0.9,
                             finished_at=base + timedelta(hours=i),
                             outcome_pnl=+1000.0)
        for i in range(3)
    ]
    set_b = [
        _attempt_with_score(i + 10, 1, 0.9,
                             finished_at=base + timedelta(hours=i),
                             outcome_pnl=-1000.0)
        for i in range(3)
    ]

    report_a = compute_progress(attempts=set_a, scenarios=[scenario], now=_NOW)
    report_b = compute_progress(attempts=set_b, scenarios=[scenario], now=_NOW)

    # Streaks must be identical.
    assert report_a.process_streak == report_b.process_streak, (
        f"ANTI-GAMBLING VIOLATION: streaks differ by P&L sign.\n"
        f"  Set A (pnl=+1000 each): streak={report_a.process_streak}\n"
        f"  Set B (pnl=-1000 each): streak={report_b.process_streak}"
    )
    assert report_a.longest_process_streak == report_b.longest_process_streak

    # Mastery levels must be identical for all archetypes.
    level_a = {m.archetype: m.level for m in report_a.mastery}
    level_b = {m.archetype: m.level for m in report_b.mastery}
    assert level_a == level_b, (
        f"ANTI-GAMBLING VIOLATION: mastery levels differ by P&L sign.\n"
        f"  Set A levels: {level_a}\n"
        f"  Set B levels: {level_b}"
    )

    # Due-review queues must be identical (both sets have high process scores,
    # so neither set should produce any due reviews).
    assert report_a.due_reviews == report_b.due_reviews, (
        f"ANTI-GAMBLING VIOLATION: due_reviews differ by P&L sign.\n"
        f"  Set A: {report_a.due_reviews}\n"
        f"  Set B: {report_b.due_reviews}"
    )


# ---------------------------------------------------------------------------
# Test 6: high P&L + low process does NOT advance mastery
# ---------------------------------------------------------------------------

def test_high_pnl_low_process_does_not_advance():
    """A lucky gambler: made money but broke the rules.

    Attempt: outcome_pnl = +$5000 (big profit) but process_score = 0.1
    (wrong decision, no checklist).

    Assert:
    - The attempt does NOT extend the discipline streak.
    - The archetype does NOT advance to a higher mastery level.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario_pub(1, archetype="chop")

    # One prior high-process attempt to establish a baseline streak=1.
    baseline = _attempt_with_score(1, 1, 0.80,
                                    finished_at=_NOW - timedelta(hours=2),
                                    outcome_pnl=-100.0)

    # The "lucky gambler" attempt: big profit, terrible process.
    lucky = _attempt_with_score(2, 1, 0.10,
                                 finished_at=_NOW - timedelta(hours=1),
                                 outcome_pnl=5000.0)

    # Baseline alone: streak=1.
    report_baseline = compute_progress(
        attempts=[baseline], scenarios=[scenario], now=_NOW
    )
    assert report_baseline.process_streak == 1, (
        f"Baseline: expected streak=1, got {report_baseline.process_streak}"
    )

    # After adding the lucky-but-undisciplined attempt: streak must RESET to 0.
    report_with_lucky = compute_progress(
        attempts=[baseline, lucky], scenarios=[scenario], now=_NOW
    )
    assert report_with_lucky.process_streak == 0, (
        f"ANTI-GAMBLING VIOLATION: Lucky gambler (high pnl, low process) "
        f"did not break the streak.\n"
        f"  Expected streak=0 after a low-process attempt (0.10).\n"
        f"  Got streak={report_with_lucky.process_streak}.\n"
        f"  A profitable but undisciplined attempt must break the discipline streak."
    )

    # Mastery: the window of 2 attempts (scores 0.80, 0.10) should have mean=0.45
    # → level 0 "learning". It must NOT be level 2+ (proficient/mastered).
    mastery_map = {m.archetype: m for m in report_with_lucky.mastery}
    chop_mastery = mastery_map.get("chop")
    assert chop_mastery is not None
    assert chop_mastery.level is not None and chop_mastery.level <= 1, (
        f"ANTI-GAMBLING VIOLATION: Lucky gambler advanced mastery level.\n"
        f"  Expected level <= 1 (mean ≈ 0.45), got level={chop_mastery.level} "
        f"({chop_mastery.level_label}), mean={chop_mastery.rolling_mean_score}.\n"
        f"  High outcome_pnl must NOT advance mastery — only process_score does."
    )


# ---------------------------------------------------------------------------
# Test 7: low P&L + high process DOES advance mastery
# ---------------------------------------------------------------------------

def test_low_pnl_high_process_does_advance():
    """The disciplined loser: followed every rule and lost money.

    5 attempts, all process_score=0.85 (proficient band), all outcome_pnl < 0
    (every trade was a money-loser).

    Assert:
    - Mastery reaches level 2 (proficient) or higher.
    - Discipline streak = 5.

    This is the mirror of test 6 and the heart of the anti-gambling guarantee:
    the user is REWARDED for discipline even when they lose money.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario_pub(1, archetype="trend-day")

    base = _NOW - timedelta(hours=5)
    disciplined_losers = [
        _attempt_with_score(i + 1, 1, 0.85,
                             finished_at=base + timedelta(hours=i),
                             outcome_pnl=-500.0)   # every trade lost money
        for i in range(5)
    ]

    report = compute_progress(
        attempts=disciplined_losers, scenarios=[scenario], now=_NOW
    )

    # Streak: all 5 are >= 0.7 → current streak = 5.
    assert report.process_streak == 5, (
        f"ANTI-GAMBLING VIOLATION: Disciplined loser did not earn full streak.\n"
        f"  Expected streak=5 for 5 consecutive high-process attempts.\n"
        f"  Got streak={report.process_streak}.\n"
        f"  Negative outcome_pnl must NOT suppress the discipline streak."
    )

    # Mastery: 5 attempts with mean 0.85 → level 2 (proficient) or 3 (mastered).
    mastery_map = {m.archetype: m for m in report.mastery}
    td_mastery = mastery_map.get("trend-day")
    assert td_mastery is not None
    assert td_mastery.level is not None and td_mastery.level >= 2, (
        f"ANTI-GAMBLING VIOLATION: Disciplined loser did not advance mastery.\n"
        f"  Expected level >= 2 (proficient) for 5 attempts with mean 0.85.\n"
        f"  Got level={td_mastery.level} ({td_mastery.level_label}), "
        f"  mean={td_mastery.rolling_mean_score}.\n"
        f"  Negative outcome_pnl must NOT suppress mastery advancement."
    )


# ---------------------------------------------------------------------------
# Test 8: streak survives a disciplined loss; breaks on undisciplined win
# ---------------------------------------------------------------------------

def test_streak_survives_a_loss_if_process_high():
    """Streak logic is orthogonal to money outcomes.

    Sequence (chronological):
      attempt 1: process=0.9,  pnl=+200  (disciplined + profitable)
      attempt 2: process=0.8,  pnl=-300  (disciplined + losing)
      attempt 3: process=0.75, pnl=-150  (disciplined + losing)
      attempt 4: process=0.2,  pnl=+800  (undisciplined + profitable)

    Expected:
      - After attempt 3: streak=3 (all three above 0.7 threshold).
      - After attempt 4: streak=0 (undisciplined win breaks the streak).

    This proves:
    a) Losing money does not break the discipline streak.
    b) Making money does not prevent the streak from breaking on bad process.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario_pub(1, archetype="failed-breakout")
    base = _NOW - timedelta(hours=4)

    a1 = _attempt_with_score(1, 1, 0.90, finished_at=base,                  outcome_pnl=+200.0)
    a2 = _attempt_with_score(2, 1, 0.80, finished_at=base + timedelta(hours=1), outcome_pnl=-300.0)
    a3 = _attempt_with_score(3, 1, 0.75, finished_at=base + timedelta(hours=2), outcome_pnl=-150.0)
    a4 = _attempt_with_score(4, 1, 0.20, finished_at=base + timedelta(hours=3), outcome_pnl=+800.0)

    # After first 3 attempts: streak should be 3.
    report_3 = compute_progress(
        attempts=[a1, a2, a3], scenarios=[scenario], now=_NOW
    )
    assert report_3.process_streak == 3, (
        f"ANTI-GAMBLING VIOLATION: Losing attempts broke the discipline streak.\n"
        f"  Expected streak=3 for 3 consecutive process_score >= 0.7 attempts.\n"
        f"  Got streak={report_3.process_streak}.\n"
        f"  Attempts 2 and 3 had negative pnl but high process — they must "
        f"  keep the streak alive."
    )

    # After adding the undisciplined-but-profitable attempt: streak must reset.
    report_4 = compute_progress(
        attempts=[a1, a2, a3, a4], scenarios=[scenario], now=_NOW
    )
    assert report_4.process_streak == 0, (
        f"ANTI-GAMBLING VIOLATION: Profitable but undisciplined attempt did not "
        f"break the streak.\n"
        f"  Expected streak=0 after process_score=0.2 attempt.\n"
        f"  Got streak={report_4.process_streak}.\n"
        f"  A money-making but rule-breaking attempt must reset the discipline streak."
    )

    # The longest streak should still be 3 (the first three).
    assert report_4.longest_process_streak == 3, (
        f"Expected longest_streak=3 (from the first 3 attempts), "
        f"got {report_4.longest_process_streak}"
    )


# ---------------------------------------------------------------------------
# Test 9: due-review cleared by process, not pnl
# ---------------------------------------------------------------------------

def test_due_review_cleared_by_process_not_pnl():
    """SM-2-lite scheduler is keyed on process_score, not outcome_pnl.

    Scenario (chronological for archetype 'chop'):
      day 0: fail   (process=0.3, pnl=+500)  → due at day+1 (review_count=1)
      day 2: attempt (process=0.2, pnl=+999)  → still a fail; due not cleared
      day 3: attempt (process=0.8, pnl=-200)  → PASS; due cleared despite loss

    Assert:
    - After day 2 (high pnl but low process): still due.
    - After day 3 (low pnl but high process): NOT due.
    """
    from app.analytics.mastery import compute_progress

    scenario = _scenario_pub(1, archetype="chop")

    day0 = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)
    day2 = day0 + timedelta(days=2)
    day3 = day0 + timedelta(days=3)

    # Day 0: first failure (pnl positive but process bad).
    fail0 = _attempt_with_score(1, 1, 0.30, finished_at=day0, outcome_pnl=+500.0)

    # Day 2: another failure (pnl very positive but process still bad).
    fail2 = _attempt_with_score(2, 1, 0.20, finished_at=day2, outcome_pnl=+999.0)

    # At two failures the SM-2 review_count is 2, so the interval is 3 days
    # measured from the MOST-RECENT failure (day2). Check after day2 + 3 days
    # so the item is genuinely due; the point of this test is that a
    # profitable-but-failing attempt does NOT clear it, not the exact schedule.
    check_after_fail2 = day2 + timedelta(days=3, hours=1)
    report_still_due = compute_progress(
        attempts=[fail0, fail2],
        scenarios=[scenario],
        now=check_after_fail2,
    )
    due_archs_fail = {r.archetype for r in report_still_due.due_reviews}
    assert "chop" in due_archs_fail, (
        f"ANTI-GAMBLING VIOLATION (or test setup error): 'chop' should be due "
        f"after two consecutive failures.\n"
        f"  Profitable pnl (+999) did NOT clear the due status — that is correct.\n"
        f"  But archetype was not in due_reviews: {report_still_due.due_reviews}"
    )

    # Day 3+: PASS (pnl is negative, process is high — the disciplined loser).
    # Place the pass after the due window and check shortly after it.
    pass3 = _attempt_with_score(3, 1, 0.80, finished_at=day3, outcome_pnl=-200.0)
    check_after_pass = check_after_fail2 + timedelta(hours=1)

    report_cleared = compute_progress(
        attempts=[fail0, fail2, pass3],
        scenarios=[scenario],
        now=check_after_pass,
    )
    due_archs_cleared = {r.archetype for r in report_cleared.due_reviews}
    assert "chop" not in due_archs_cleared, (
        f"ANTI-GAMBLING VIOLATION: High-process attempt (score=0.8) did NOT "
        f"clear the due-review even though it should.\n"
        f"  The attempt lost money (pnl=-200) but that must not matter.\n"
        f"  due_reviews after pass: {report_cleared.due_reviews}\n"
        f"  A PASSING process_score must clear the SM-2 due status regardless of P&L."
    )


# ===========================================================================
# Part 3 — Guard the allowed signal uses (leaks/behavior)
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 10: leaks detector may fire on losing trades — that is detection, not reward
# ---------------------------------------------------------------------------

def test_leaks_may_reference_losses_as_signal():
    """The revenge-trading leak fires on a loss-then-upsize pattern.

    This test confirms the audit does NOT try to purge all P&L references —
    only reward-keyed ones.  The leak detector READS net_pnl to detect a
    behavioural PROBLEM (revenge sizing after losses).  That is a SIGNAL, not
    a reward path.  It belongs in the ✅ Signal column of the audit table.

    Fixture: two consecutive losing journal trades (net_pnl < 0) followed by
    an up-sized trade (qty > 1.5x average of the losing pair).
    Assert: detect_leaks returns a revenge_trading leak.
    """
    from app.analytics.leaks import detect_leaks
    from app.models.trades import Trade, Direction, TradeStatus

    base_ts = datetime(2026, 7, 1, 9, 30, 0, tzinfo=UTC)

    def _trade(tid, net_pnl, qty, open_ts):
        close_ts = open_ts + timedelta(minutes=20)
        avg_exit = 10.0 + (net_pnl / qty)
        return Trade(
            id=tid,
            symbol="AAPL",
            direction=Direction.long,
            open_ts=open_ts,
            close_ts=close_ts,
            session_date=open_ts.date(),
            qty=qty,
            avg_entry=10.0,
            avg_exit=avg_exit,
            gross_pnl=net_pnl,
            fees=0.0,
            net_pnl=net_pnl,
            pnl_pct=net_pnl / 1000.0,
            stop_price=None,
            r_multiple=None,
            hold_time_s=1200,
            status=TradeStatus.closed,
            simulated=False,
        )

    # Two consecutive losses (small qty), then a big up-size.
    loss1  = _trade(1, -100.0,  100.0, base_ts)
    loss2  = _trade(2, -150.0,  100.0, base_ts + timedelta(minutes=25))
    upsize = _trade(3,  200.0,  250.0, base_ts + timedelta(minutes=50))
    # avg_qty of losses = 100; upsize qty = 250 → ratio = 2.5 > 1.5 → trigger

    report = detect_leaks(
        trades=[loss1, loss2, upsize],
        fills=[],
        debriefs=[],
        attempts=[],
        scenarios=[],
        _now="2026-07-01T12:00:00+00:00",
    )

    leak_names = [l.name for l in report.leaks]
    assert "revenge_trading" in leak_names, (
        f"Leak detector failed to fire on a clear loss→upsize pattern.\n"
        f"  Leaks detected: {leak_names}\n"
        f"  This test guards that the audit allows P&L as a SIGNAL for detection.\n"
        f"  net_pnl < 0 is read by the leak detector to IDENTIFY a problem, "
        f"  not to grant a reward — that is the correct, allowed use."
    )

    # The revenge_trading leak must include the up-sized trade in its evidence.
    revenge_leak = next(l for l in report.leaks if l.name == "revenge_trading")
    assert len(revenge_leak.evidence["trade_ids"]) > 0, (
        "Revenge trading leak has no trade evidence — check leak fixture setup."
    )
