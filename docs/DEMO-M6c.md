# DEMO — M6c: Psychology & Leak Loop

**Goal:** the app names a real weakness from your own trading data → prescribes a targeted drill playlist → you work it, with mood check-ins and do-nothing / drawdown-script drills that score discipline, never P&L.

M6 phase C3 lands the psychology loop (pillar 2) and the self-analysis leak detector (pillar 4). It composes everything before it: synthetic tape (6.3.1), drill variants (6.3.2), the leak detector (6.3.3), mood + leaks UI (6.3.4), and behavioral metrics (6.3.5).

## Run
```bash
make dev
```

## Seed
```bash
cd backend
./.venv/bin/python -m scripts.seed_scenarios   # 11 scenarios incl. do-nothing, drawdown-script
```

## Verify (click-path)

### 1. Leak detection names a real weakness

1. Build up some journal history first (import fills via M3, or take a few drills). The leak detector mines: trades, debrief verdicts, mood check-ins, and drill-attempt behavior.
2. Open **http://localhost:5173** → **Leaks** (left nav, Training section) → **/training/leaks**.
3. Each detected leak renders as a card:
   - **Severity dots** (1 grey → 3 red) — how strong the pattern is.
   - **Description** referencing concrete evidence ("Sized up 3.0× after 2 consecutive losses in the journal (trades #1, #2, #3)").
   - **Prescribed drills** — titles of the scenarios that target this leak.
   - **Show evidence** — expands the backing trade/attempt/debrief IDs.
   - **Start playlist** — the mechanic.

   Named leaks (v1): `revenge_trading`, `chop_overtrading`, `moved_stops`, `psychology_loss`.

### 2. Prescribed playlist walks the drills in sequence

1. Click **Start playlist** on the revenge-trading leak. It prescribes, e.g., a do-nothing chop drill + the drawdown script.
2. You land in the drill player with a **"Playlist: 1 of 2"** badge in the header. URL carries the remaining IDs: `/training/drills/6?playlist=11&mood=`.
3. Complete the first drill → the player **auto-advances** to the next: `/training/drills/11?playlist=&mood=`.
4. Complete the last → success toast "Playlist complete" → back to **/training/leaks**.
5. All playlist state rides in the URL — refresh mid-playlist is safe, and bailing (navigate away) is harmless.

### 3. Mood check-in before a drill

1. From the drill lobby (**/training/drills**), click **Start drill** on any scenario.
2. A **mood check-in** intercepts: **Calm / Rushed / Frustrated / Skip check-in**.
3. Pick one → the drill player POSTs the attempt with `mood_checkin` set; the value pins across a whole playlist via the URL (`?mood=rushed`).
4. Mood is captured, not displayed here — the C4 progress page (6.4.2) will join it against performance.

### 4. Do-nothing drill: passing = zero trades

1. Take the **Midday chop: do nothing all day** drill.
2. The tape is engineered chop — every entry loses. The correct answer is **Skip** (or simply never entering).
3. Enter a trade anyway → process score tanks (decision_correctness = 0). Sit on your hands → high process score.
4. This is the anti-FOMO / overtrading discipline drill (PLAN §1, pillar 2).

### 5. Drawdown script: engineered losing streak, scored on discipline

1. Take the **Drawdown script: 3-session engineered losing streak** drill.
2. Three sub-sessions in sequence (failed-breakout → chop → failed-breakout), each designed so any entry loses money.
3. Skip all three (the correct play) → **high process score even though outcome_pnl is negative**. This is the anti-gambling guardrail: discipline scores, not money (PLAN §0.1).
4. By session 3 you're "down" in drill dollars — the drill's purpose is to reveal whether you start size-jumping. The behavioral metrics (6.3.5) flag revenge-sizing from the action log; it feeds back into the leak detector.

## Behavioral signals mined from the action log

The replayable `attempt.actions_json` (invariant from C0) is rich enough to detect:

- **Moved stops** — `set_stop` then `move_stop` in a loosening direction.
- **Hesitation** — multiple `wait` actions at the decision bar, or a long ts gap before committing.
- **Revenge sizing** — an up-sized entry after a losing attempt.

These are pure functions in `backend/app/analytics/behavior.py`; the leak detector consumes them.

## Design principles enforced

- **Score process, never outcome** (§0.1). The drawdown-script's anti-gambling test (`test_drawdown_script_high_score_despite_negative_pnl`) proves a rule-following run scores ~0.85 on a tape engineered to lose.
- **Attempts are replayable events** (§0.2). Every behavioral signal is derived from the bar-indexed action log, not from a collapsed outcome.
- **The user's own trading writes the curriculum** (§0.3). Leaks are detected from real journal data; the prescribed playlist targets the specific weakness.
- **Training trades never pollute analytics** (§0.5). The leak detector excludes `context='training'` trades — verified in `test_leaks.py::test_endpoint_excludes_training_trades`.

## API sanity

```bash
# Detect leaks (reads journal + debriefs + attempts; excludes training trades)
curl "http://localhost:9000/api/training/leaks" | jq '.leaks[] | {name, severity, prescribed_scenario_ids}'

# Take a drill with a mood check-in
curl -X POST http://localhost:9000/api/training/attempts \
  -H 'content-type: application/json' \
  -d '{"scenario_id": 6, "mood_checkin": "rushed"}'

# A synthetic drill session (gap-and-go tape, deterministic from seed)
curl "http://localhost:9000/api/training/scenarios/<synthetic-scenario-id>/session" | jq '.candles | length'
```

## Notes

- **Synthetic tape is deterministic.** Same `(archetype, seed, params)` → byte-identical candles. Drawdown scripts compose multiple seeded synthetic sessions, so a leak-prescribed drill is reproducible across machines.
- **Drawdown-script bar encoding:** sub-session actions use `bar = session_index * 1000 + local_bar`. The scorer splits on `bar // 1000`. 1000 bars/session is far above any real session (78 five-minute bars), so no collision.
- **`list_fills` gap:** the leak detector accepts a `fills` param for future signal expansion but v1 passes `[]` (there's no `list_fills` in `ingest/query` yet). Fills-based leaks are a v2 follow-up if needed.

## Tests
```bash
make test    # 341 tests
```
New in C3:
- `test_synthetic_scenarios.py` — 40 cases: seed determinism, archetype recognizability, OHLC invariants, causal indicators on synthetic tape
- `test_drawdown_script.py` — 7 cases incl. the anti-gambling regression
- `test_behavior_metrics.py` — 18 cases: moved-stop, hesitation, revenge-size detectors
- `test_leaks.py` — 13 cases incl. the PLAN §6 acceptance (revenge-trading detected from a seeded fixture) + training-trade exclusion
