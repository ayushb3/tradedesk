# DEMO — M6d: Gamification (mastery, streaks, spaced repetition)

**Goal:** the training module rewards *discipline*, not profit. Per-archetype mastery grows from rolling process scores; a discipline streak counts consecutive rule-following drills; failed archetypes resurface days later with a fresh tape via spaced repetition. **No P&L touches any reward.**

M6 phase C4 is the final phase — the gamification layer over C1–C3, with an anti-gambling audit as the capstone guardrail.

## Run
```bash
make dev
```

## Seed
```bash
cd backend
./.venv/bin/python -m scripts.seed_scenarios
```

## Verify (click-path)

### 1. Progress page

1. Open **http://localhost:5173** → **Progress** (left nav, first in the Training section) → **/training/progress**.
2. With no drills taken: empty state — all archetypes "unattempted", streak 0, a "take your first drill" CTA.
3. **Discipline streak cards**: "drills in a row following your rules" + "your best run". These count consecutive attempts with `process_score >= 0.7`. **There is no win streak, no P&L, no dollar figure anywhere on this page** — by design (PLAN §0.1).
4. **Mastery by archetype**: one row per archetype with a 4-dot level meter (learning → developing → proficient → mastered), the rolling mean process score, and attempt count. Level comes from the last N process scores, never from money.

### 2. Fail a drill → streak resets, archetype drops

1. Take **PDH break: RIVN opening drive** (a decision-point drill, correct action = enter long).
2. At the pause, choose **Skip** (the wrong call — you should have entered).
3. Finish → process score ~0.15.
4. Back on **/training/progress**: the discipline streak has **reset to 0**, and `failed-breakout`/`custom` mastery drops toward "learning". Verified end-to-end: a 0.15 attempt resets `process_streak` to 0 and sets the archetype's rolling mean to 0.15.

### 3. Pass a drill even at a loss → mastery advances

1. Take a drawdown-script or any drill and **follow the rules** — make the correct decision even on a tape engineered to lose money.
2. Finish → high process score (~0.85) with a **negative `outcome_pnl`** shown, labeled "not scored".
3. On **/training/progress**: mastery advances and the discipline streak extends — **despite losing money**. This is the anti-gambling guarantee in action: the disciplined loser is rewarded; the lucky gambler (profit + broken rules) is not. Proven by `test_anti_gambling_audit.py::test_low_pnl_high_process_does_advance` and its mirror.

### 4. Spaced repetition resurfaces failed archetypes

1. After failing an archetype (step 2), the **Due for review** section shows it — but not immediately. The SM-2-lite scheduler holds it: first review is due **+1 day** after a failure, then +3 days, then +7 if you keep failing it.
2. When due, the card reads "failed-breakout — failed 1 day ago (scored 0.30)" with a **Review now → fresh tape** button.
3. Click it: the app POSTs a **new synthetic scenario** for that archetype using a `suggested_seed` derived deterministically from `(archetype, review_count)` — so **the resurfaced drill is a different tape each cycle**, not the one you memorized. You land straight in the drill player.
4. Pass the review (high process score) → the due status clears (`review_count` resets). Keep failing → the interval escalates (+1 → +3 → +7 days) and the seed changes each time.

   *(To observe the +1-day gate without waiting, patch a drill attempt's `finished_at` into the past, or trust the audit tests — `test_due_review_after_failure`, `test_due_review_escalates_schedule`, `test_suggested_seed_is_deterministic` — which lock the schedule down with injected time.)*

### 5. Weekly review quests

- The progress page shows quest progress bars ("Grade all trades", "Complete N debriefs") when quest data is available. v1 shows a muted "No quests this week" if none apply.

### 6. Mood check-in feeds the loop

- Every drill can be started with a mood check-in (calm/rushed/frustrated) from C3. Those check-ins are recorded on the attempt and available to the leak detector; the progress page focuses on process outcomes.

## The anti-gambling guarantee (the capstone)

The whole module is built on PLAN §0.1: **score process, never outcome.** C4 makes this mechanically enforceable with `docs/M6-ANTI-GAMBLING-AUDIT.md` + `test_anti_gambling_audit.py` (10 tests):

**Three legitimate uses of P&L, one forbidden:**

| Use | Example | Verdict |
|---|---|---|
| Record / display | `AttemptFinishResponse.outcome_pnl`, shown labeled "not scored" | ✅ allowed |
| Problem-detection signal | leak detector reads `net_pnl < 0` to spot a losing trade; revenge-size detector reads `outcome_pnl < 0` to identify a losing prior attempt | ✅ allowed (detection, not reward) |
| Reward / progression | any `process_score`, mastery level, streak, quest, or due-review keyed on P&L | ❌ forbidden — none exists |

**Behavioral proofs (all passing):**
- Identical process score regardless of P&L sign.
- Identical mastery/streak/due-reviews regardless of P&L sign.
- **Lucky gambler** (high P&L, low process) does NOT advance mastery, breaks the streak.
- **Disciplined loser** (negative P&L, high process) DOES advance mastery, extends the streak.
- Due-review cleared by a high-process attempt even at a loss; NOT cleared by a profitable-but-sloppy one.

**Static proofs:** AST audit of the scorer + mastery module (no P&L in executable reward code); frontend grep of the progress page (no P&L, no dollar figures).

Sign-off: *the M6 training module contains no P&L-keyed reward path.*

## API sanity

```bash
# Per-archetype mastery + streaks + due reviews + quests
curl "http://localhost:9000/api/training/progress" | jq '{process_streak, mastery: (.mastery | map(select(.attempts_count > 0))), due_reviews}'
```

## Tests
```bash
make test    # 359 tests
```
New in C4:
- `test_mastery.py` — 16 cases: band thresholds, rolling window, discipline streak (with a P&L-independence assertion), SM-2 due-review schedule + escalation, deterministic suggested seed
- `test_progress_api.py` — 5 endpoint smoke tests
- `test_anti_gambling_audit.py` — 10 capstone tests (static AST + frontend grep + behavioral proofs + signal-guard)

## M6 complete

All four sub-modules shipped and demo'd:
- **C0** Foundations — `DEMO` covered inline; contracts, migrations, form backend, causal slicer, contract tests.
- **C1** Decision-point drills — `docs/DEMO-M6a.md`
- **C2** Agent debrief — `docs/DEMO-M6b.md`
- **C3** Psychology & leak loop — `docs/DEMO-M6c.md`
- **C4** Gamification — this doc

Every existing guarantee held throughout: local-only by default, read-only broker access, causal (no-lookahead) indicators, training trades excluded from journal analytics, and — the module's own north star — process rewarded over profit.
