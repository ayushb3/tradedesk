# DEMO — M6a: Training / Decision-Point Drills

**Goal:** author scenarios on real/mock tape → take a drill (blind or not) → be scored on **process, not P&L** → see the answer key + full action log on finish.

M6 phase C1 lands the training module's core loop: **strategy drills**. Later phases add the agent debrief (C2), psychology + leaks (C3), and gamification (C4).

## Run
```bash
make dev
```

## Seed (one-shot)
```bash
cd backend
./.venv/bin/python -m scripts.seed_scenarios       # 10 scenarios covering M3 default tags
./.venv/bin/python -m scripts.seed_scenarios       # second run: "10 already present, 0 inserted"
```

Idempotent — safe to re-run.

## Verify (click-path)

1. Open **http://localhost:5173** → **Scenarios** (left nav, "Training" section). Table shows the 10 seeded drills: title, archetype badge, drill-type badge, symbol/date (or "hidden" for blind ones), created_at.

2. Click **New Scenario** → **/training/scenarios/new**. Author a fresh drill:
   - Pick a symbol + date; the replay chart loads that session's full tape.
   - Scrub the tape with the replay controls (▶ / ⏸ / speed).
   - At any bar, hit **Pin start**, **Pin decision**, **Pin end** to mark the drill's bar range.
   - Type a note and hit **Add annotation at cursor** to pin a teaching note at the current bar (e.g. "PDH at 12.40 — first clean touch").
   - Under **Answer key**, tick one or more `AttemptActionType`s (`enter_long`, `skip`, …), then write the explanation the drill player will reveal on finish.
   - Optional: **Blind by default** — the drill player will hide ticker/date when this scenario is taken.
   - **Save**. Toast confirms, navigation returns to the list. The API response omits the answer key by contract (no cheating).

3. Click **Drills** (left nav). The lobby shows every active scenario as a card: title, archetype, drill-type, blind flag. Click **Start drill** on the seeded **PDH break: RIVN opening drive**.

4. **Ready** state: scenario metadata + Start button. Click **Start**.

5. **Playing** state: the tape auto-advances from the start bar; the chart streams new candles bar-by-bar.

6. **Decision** state (auto-paused at the decision bar):
   - Four big buttons: **Long / Short / Skip / Wait**.
   - **Wait** advances one bar and re-pauses (deferral — the scorer treats it that way too).
   - **Long / Short / Skip** commits and moves to Resolving.
   - Behind the scenes, each click PATCHes an `AttemptAction` with `bar = session.meta.start_bar + currentLocalBar` (global frame — the scorer's coordinate system).

7. **Resolving** state: the tape plays out from the decision bar to the end at a faster tick.

8. **Finished** state:
   - Large process-score badge (0..100). "Process, not P&L" tooltip.
   - Answer-key card: the `correct_actions` list + `explanation` prose the author wrote. This is the **only** place in the whole API the answer key is exposed (contract-enforced in `test_training_contract.py`).
   - Your action list (from your PATCH history, replayable).
   - `outcome_pnl` labeled **not scored** when non-null (anti-gambling guardrail, PLAN §0.1).
   - Buttons: **Take another drill** (back to lobby), **Take this drill again** (fresh attempt for the same scenario).

9. **Blind mode:** open **PDH break BLIND: HOOD open** from the lobby. Header shows "Blind drill — ticker/date hidden", and the chart's tickmarks are normalized to a fixed anchor. Annotations still render — they're the teaching notes. Score works the same.

10. API sanity:
    ```bash
    # List active scenarios
    curl "http://localhost:9000/api/training/scenarios?active=true"

    # Session payload for a drill (answer key NEVER included)
    curl "http://localhost:9000/api/training/scenarios/1/session" | jq '.meta.start_bar, .decision_bar, .blind'

    # Take a drill end-to-end
    curl -X POST http://localhost:9000/api/training/attempts \
      -H 'content-type: application/json' -d '{"scenario_id": 1}'
    curl -X PATCH http://localhost:9000/api/training/attempts/1 \
      -H 'content-type: application/json' \
      -d '{"actions":[{"bar":335,"action":"enter_long","ts":"2026-07-09T14:00:00Z"}]}'
    curl -X POST http://localhost:9000/api/training/attempts/1/finish
    # -> {"process_score": 0.85, "answer_key": {...}, ...}
    ```

## Design principles enforced (PLAN-M6-TRAINING §0)

- **Score process, never outcome.** `outcome_pnl` is recorded and displayed but excluded from every reward path. Grep audit runs in C4 (`test_anti_gambling_audit`).
- **Attempts are replayable events.** `PATCH /api/training/attempts/{id}` is append-only; the bar-indexed action log is the source of truth for later behavior detection (moved stops, revenge sizing — C3.5).
- **The user's own tape writes the curriculum.** Every seed scenario is real/mock tape at a real session date. Debrief-authored scenarios (C2) will slot into the same schema via the agent's `create_scenario` tool.
- **LLM is optional.** No LLM in C1 — the drill player is pure state machine + backend scorer.
- **Training trades never pollute journal analytics.** `trades.context` defaults to `real`; `training` trades are filtered out of `/api/analytics/summary` and journal listings.

## Notes

- **Bar-frame contract:** `AttemptAction.bar`, `scenario.decision_bar`, `scenario.start_bar`, `scenario.end_bar`, and `AnswerKey.management_notes[*].bar` are **all in the global session frame** (bar 0 = 04:00 ET, bar 330 = 09:30 ET). The `/session` response's `decision_bar` field is in local (slice-relative) coordinates for the UI; the drill player translates back via `session.meta.start_bar` before every PATCH. Regression tests in `test_training_scorer_e2e.py::test_local_to_global_bar_translation_from_session_meta` and its companion lock this in on both sides.
- **Blind-mode timestamps** are rebased to a fixed anchor (`2001-01-01T13:30:00Z`) so the ticker/date can't be inferred from wall-clock.
- **Scoring formula (v1):** `process_score = 0.7 * decision_correctness + 0.3 * checklist_adherence`. `decision_correctness` walks the action log at `decision_bar`, treating `wait` as a one-bar deferral. Full spec: `backend/app/analytics/training.py::score_attempt`.

## Tests
```bash
make test    # 207 tests
```
New in C1:
- `test_training_scorer.py` — 13 hand-computed scorer fixtures (correct/wrong entry, wait deferral, do-nothing, management-only, checklist boost/penalty, edge clamps)
- `test_training_scorer_e2e.py` — HTTP round-trips + the two frame-mismatch regression tests
- `test_attempt_event_log.py` — 10 event-log invariants (append-only, order preservation, ts round-trip, scorer-walks-log)
- `test_seed_scenarios.py` — bit-rot guard on the 10 seed scenarios
- `test_training_contract.py` (C0, augmented) — answer-key hiding, blind-mode blanking, training-context journal exclusion
