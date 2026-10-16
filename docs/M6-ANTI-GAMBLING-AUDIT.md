# M6 Anti-Gambling Audit — Task 6.4.3

## Principle (PLAN-M6-TRAINING.md §0.1)

> **Score process, never outcome.**
>
> Every reward, mastery level, streak, spaced-repetition scheduling, and quest
> completion in the training module is derived solely from `process_score`
> (rule adherence × decision correctness). `outcome_pnl` is computed and
> displayed for educational transparency but is **never an input to any reward
> path**.
>
> A drill where the trader followed every rule and lost money scores higher than
> one where they broke rules and got lucky. The training module is a discipline
> tool, not a gambling reinforcement loop.

---

## Three-Way Distinction

There are exactly three ways P&L signals appear in the training codebase, and
only the first two are permitted:

| Category | Description | Verdict |
|----------|-------------|---------|
| ✅ **Record / display** | `outcome_pnl` is computed from the action log and returned in `AttemptFinishResponse` labeled "recorded · not scored". Shown to the user for educational context. | **Allowed** |
| ✅ **Signal for problem-detection** | `leaks.py` reads `net_pnl < 0` to detect revenge trading (loss→upsize) and chop overtrading. `behavior.py`'s `detect_revenge_size` reads `outcome_pnl < 0` as a precondition to identify a problematic prior attempt. These DETECT a problem; they do not GRANT a reward. | **Allowed** |
| ❌ **Reward / progression** | Any `process_score`, mastery `level`, discipline streak, spaced-repetition scheduling, due-review clearance, or quest completion that is a function of P&L. | **FORBIDDEN** |

---

## Classified P&L Reference Table

Every reference to `outcome_pnl` or `net_pnl` in the training module, classified:

### `backend/app/analytics/training.py`

| Line(s) | Code / Context | Category | Verdict |
|---------|----------------|----------|---------|
| 9–12, 57–58, 171–173 | Docstring / comment: "outcome_pnl is computed … but NEVER an input to process_score" | Comment (non-executable) | ✅ Allowed |
| 309–344 | `_compute_outcome_pnl(actions)` function — computes P&L from action log | Record: produces the value | ✅ Allowed |
| 393–399 | `outcome_pnl = _compute_outcome_pnl(actions)` in `score_attempt` — computed **after** `process_score` is fully determined, packed into `AttemptFinishResponse` | Record: display only | ✅ Allowed |

Scoring helpers (`_score_decision_point`, `_score_do_nothing`, `_score_management_only`, `_score_drawdown_script`, `_score_checklist`) contain **zero references** to `outcome_pnl` or `net_pnl`. Confirmed by AST audit in `test_no_pnl_in_score_computation`.

### `backend/app/analytics/mastery.py`

| Line(s) | Code / Context | Category | Verdict |
|---------|----------------|----------|---------|
| 10–14, 203, 290, 429 | Docstring / comment: "outcome_pnl is NEVER read … Any reference here would be a false positive" | Comment (non-executable) | ✅ Allowed |

**Zero executable references** to `outcome_pnl` or `net_pnl` anywhere in `mastery.py`. Confirmed by strong-form AST audit in `test_no_pnl_in_mastery`.

### `backend/app/analytics/leaks.py`

| Line(s) | Code / Context | Category | Verdict |
|---------|----------------|----------|---------|
| 136 | Docstring: "Requires attempts to carry … outcome_pnl as dynamic attrs" | Comment | ✅ Allowed |
| 186, 200, 212 | `_detect_revenge_trading_from_journal`: `net_pnl < 0` to identify losing trades as evidence of a problem | Signal — problem detection | ✅ Allowed |
| 254, 270 | `_detect_chop_overtrading`: `net_pnl < 0` to count losing trades in a session | Signal — problem detection | ✅ Allowed |
| 325 | `_detect_moved_stops`: `t.net_pnl is not None` to filter closed trades | Signal — data filtering | ✅ Allowed |
| 413, 455, 468, 494 | `_enrich_attempts_from_db`: loads `outcome_pnl` from DB to attach to Attempt objects for behavioral detectors | Record — enrichment for signal layer | ✅ Allowed |

No leak detection emits a reward, mastery advance, or streak extension — they emit `Leak` objects that prescribe remedial drills.

### `backend/app/analytics/behavior.py`

| Line(s) | Code / Context | Category | Verdict |
|---------|----------------|----------|---------|
| 80, 291–305 | Docstring: "attempt N-1 had process_score < 0.5 AND outcome_pnl < 0" | Comment | ✅ Allowed |
| 318 | `prev_pnl = getattr(prev, "outcome_pnl", 0.0)` — reads pnl to confirm the prior attempt was a LOSS before flagging revenge sizing | Signal — precondition for problem detection | ✅ Allowed |
| 321 | `if prev_score >= 0.5 or prev_pnl >= 0.0: continue` — skips pairs where prior was not a loss | Signal — gate for detection | ✅ Allowed |

No reward is granted by `detect_revenge_size`. It returns a `RevengeSizeSignal` (a problem indicator), which the leak detector then packages as a `Leak`.

### `backend/app/models/training.py`

| Line(s) | Code / Context | Category | Verdict |
|---------|----------------|----------|---------|
| 223 | `outcome_pnl: float | None = None  # recorded, displayed, NEVER rewarded` | Record — model field definition | ✅ Allowed |

### `frontend/src/pages/ProgressPage.tsx`

| Reference | Category | Verdict |
|-----------|----------|---------|
| Lines 16–17: comment only — `/* outcome_pnl is NEVER read … */` | Comment (non-executable) | ✅ Allowed |

**Zero executable references** to `outcome_pnl`, `net_pnl`, or dollar figures. The reward-facing UI (streaks, mastery, due reviews, quests) is completely free of P&L. Confirmed by `test_progress_page_has_no_pnl`.

### `frontend/src/pages/DrillPlayerPage.tsx`

| Line(s) | Code / Context | Category | Verdict |
|---------|----------------|----------|---------|
| 677–686 | `FinishedScreen` displays `result.outcome_pnl` labeled "(recorded · not scored)" | Record — display only, not a reward surface | ✅ Allowed |

The drill player's finished screen is the **display** surface, not the reward/gamification surface. `ProgressPage.tsx` is the reward surface and is clean.

---

## Reward Column

The reward column of the table above is **empty**. No `process_score` computation,
mastery level, discipline streak, due-review scheduling, or quest completion
is a function of `outcome_pnl` or `net_pnl`.

---

## Behavioral Tests That Lock This In

The following tests in `backend/tests/test_anti_gambling_audit.py` provide
machine-checkable behavioral proofs:

| Test | Assertion |
|------|-----------|
| `test_no_pnl_in_score_computation` | AST: no P&L name in any scoring helper function body |
| `test_no_pnl_in_mastery` | AST: zero executable P&L references in mastery.py |
| `test_progress_page_has_no_pnl` | Grep: ProgressPage.tsx has no pnl identifier or dollar currency display |
| `test_identical_process_score_regardless_of_pnl` | Behavioral: identical actions → identical process_score regardless of outcome_pnl |
| `test_mastery_identical_regardless_of_pnl` | Behavioral: identical process_scores → identical mastery regardless of outcome_pnl sign |
| `test_high_pnl_low_process_does_not_advance` | Behavioral: high pnl + low process does NOT extend streak or advance mastery |
| `test_low_pnl_high_process_does_advance` | Behavioral: low pnl + high process DOES advance mastery and extend streak |
| `test_streak_survives_a_loss_if_process_high` | Behavioral: losing money keeps the streak alive; making money with bad process breaks it |
| `test_due_review_cleared_by_process_not_pnl` | Behavioral: profitable-but-low-process does NOT clear due-review; loss-but-high-process DOES |
| `test_leaks_may_reference_losses_as_signal` | Guard: leak detector correctly fires on losses (allowed signal use) |

---

## Sign-off

M6 training module contains no P&L-keyed reward path as of commit `0a96d24`.

All 10 behavioral tests pass. Audit complete.

---

# M9a Extension — Trade Critic (issue #58)

## Why the M6 principle binds the critic

The Trade Critic (PRD-M9 §4.2, R6–R11) is a new surface where a P&L-keyed
judgment could leak in: it reads a real trade — including its `net_pnl` — and
emits a verdict. The M6 principle applies unchanged: **the critic scores process
adherence to course rules, never outcome.** A rule-following loser must critique
*better* than a rule-breaking winner. The mechanisms are different from the
drill scorer (there is no numeric `process_score` here — the critic emits
falsifiable rule-checks), so the guarantee is enforced in three places instead
of one:

1. **Schema — no P&L field by construction.** `CriticResult`,
   `CriticRuleCheck`, `CriticPhaseVerdicts`, and `CriticPrescription`
   (`backend/app/models/agents.py`) have **no** dollar / pnl / profit / loss /
   win-rate field. There is nowhere for a P&L judgment to be stored. The module
   docstring states this is a contract invariant, not an omission.

2. **Prompt — explicit anti-gambling clause + rewarded abstention.**
   `build_critic_prompt` (`backend/app/agents/critic_prompts.py`) embeds a
   non-negotiable clause verbatim:

   > ANTI-GAMBLING CLAUSE (non-negotiable): Evaluate PROCESS ADHERENCE to the
   > user's course material ONLY. Never praise or blame based on P&L or whether the trade made or
   > lost money. A rule-following loser is a GOOD trade; a rule-breaking winner
   > is a BAD trade. Do not mention dollars, P&L, profit, or loss in any verdict
   > or rule_check. Phrase every verdict as a falsifiable rule-check.

   Abstention is a first-class value: when the chart/data don't clearly show
   whether a rule was followed, the model is instructed to use adherence
   `unclear`, and told a wrong confident `pass`/`fail` is **worse** than
   `unclear` (PRD §7 risk #2). `unclear` is never inferred from P&L.

3. **Adherence is three-valued and P&L-independent.** `CriticRuleCheck.adherence`
   is `pass | fail | unclear` — a judgment about a rule, not an outcome. The
   critic's drift guard (`app.agents.critic.critique_trade`) coerces any unknown
   adherence to `unclear`; nothing in the pipeline reads `net_pnl` to decide it.

## Three-way distinction, applied to the critic

| Category | In the critic | Verdict |
|----------|---------------|---------|
| ✅ **Record / display** | The M7 review page still shows the trade's `net_pnl` (existing journal surface). The critic does not remove or re-score it. | **Allowed** |
| ✅ **Signal for problem-detection** | `detected_patterns` = the trade's tag names (e.g. `revenge-size`, `chop`) feed the retrieval query — same allowed "detect a problem" use as M6's leak detector. Tags may have been derived from losses upstream (allowed M6 signal), but the critic never re-reads P&L. | **Allowed** |
| ❌ **Reward / progression / verdict-from-P&L** | Any critic verdict, rule-check, or prescription that is a function of whether the trade made or lost money. | **FORBIDDEN — enforced by schema + prompt + tests** |

## Behavioral tests that lock this in

`backend/tests/test_agents_anti_gambling.py`:

| Test | Assertion |
|------|-----------|
| disciplined-loser vs lucky-winner | Two fixture trades through a canned runner: the rule-following loser receives **strictly more `pass` rule-checks** than the rule-breaking winner — critiqued no worse for losing. |
| no P&L tokens in output | No `$` / pnl / profit / loss / dollar token appears in any `verdict_by_phase` string or `rule_check` rule/note of the produced `CriticResult`. |
| prompt contains the clause | `build_critic_prompt(...)`'s system string contains the process-not-outcome anti-gambling language. |
| no P&L field on the schema | `CriticResult` and `CriticRuleCheck` `model_fields` contain no pnl / profit / dollar field. |

## Sign-off (M9a)

The Trade Critic emits process-adherence rule-checks with no P&L-keyed judgment
path — enforced at the schema, prompt, and behavioral-test layers — as of the
M9a feature branch (`feat/issue-58-m9a-critic`). The anti-gambling regression
tests pass alongside the M6 audit suite.
