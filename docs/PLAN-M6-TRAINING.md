# TradeDesk — Plan M6: Training Module (Module C)

Gamified lessons and exercises built on the existing replay engine and journal:
strategy drills, psychology exercises, realistic backtesting scenarios, and
agent-guided self-analysis. Local-only, mock-first, read-only — all existing
guarantees hold.

Status: **✅ COMPLETE — all four sub-modules (C0–C4) shipped, demo'd, and tested (2026-07-09).**
Read `docs/PLAN.md` first — this document extends it and inherits every
convention there (contract-first, no shared files, DoD, tech stack).

Demos: `DEMO-M6a` (drills) · `DEMO-M6b` (debrief) · `DEMO-M6c` (leaks) · `DEMO-M6d` (gamification).
Anti-gambling audit: `docs/M6-ANTI-GAMBLING-AUDIT.md`.

---

## 0. Design principles (non-negotiable for this module)

1. **Score process, never outcome.** A drill where the user followed every rule
   and lost money scores higher than one where they broke rules and got lucky.
   No P&L-based rewards, streaks, or celebration. This is the anti-gambling
   guardrail — gamification rewards discipline and accuracy, not activity or profit.
2. **Attempts are replayable events, not just outcomes.** `exercise_attempts`
   stores a bar-indexed action log (what the user did at which bar), so later
   phases can measure *behavior* (hesitation, moved stops, revenge sizing),
   not just results. Cheap to capture from day one, painful to retrofit.
3. **The user's own trading writes the curriculum.** Scenarios are authored as
   a byproduct of debriefing real/sim trades (via the agent interview), plus
   deterministic synthetic generators. No generic course content.
4. **LLM is optional and swappable.** Default config runs with zero credentials
   and no LLM (`TRADEDESK_LLM=none`). The interview protocol, artifacts, and
   schema are identical across all LLM backends — the brain is a seam, exactly
   like `DataProvider` mock ↔ webull.
5. **Training trades never pollute real analytics.** Drill trades land in the
   existing `trades` table flagged `context='training'`, excluded from journal
   analytics by default, analyzable on their own.

---

## 1. What we're building (the four pillars)

| Pillar | Feature | Ships in |
|---|---|---|
| 1. Strategy drills | Decision-point drills (pause at bar N: enter/skip/wait → tape plays out → process score + explanation), blind mode (ticker/date hidden), management-only drills (entry forced, user manages exit), spot-the-setup | C1 (core), C3 (variants) |
| 2. Psychology | "Do nothing" drills (pass = zero trades), drawdown scripts (engineered losing streak → measure session-3 behavior), pre-drill mood check-in joined against performance | C3 |
| 3. Realistic backtesting scenarios | Curated real tape (`symbol, date, bar-range` refs into `candles_cache`) + deterministic synthetic archetypes (gap-and-go, failed breakout, midday chop, trend day); **agentic authoring**: the debrief interview turns a real trade into a scenario | C1 (curated), C2 (agent authoring), C3 (synthetic) |
| 4. Self-analysis | Agent-guided trade debrief (reasoning → plan → feelings → verdict), leak detector → prescribed drill playlists, review quests | C2 (debrief), C3 (leaks), C4 (quests) |
| Gamification layer | Process-score streaks, per-archetype mastery levels, spaced repetition of failed archetypes | C4 |

### The debrief interview (pillar 3+4 centerpiece)

Trigger: user picks a trade (or leak detector suggests one). The agent already
has fills, entry/exit, R-multiple, hold time, tags, and candles around the
trade **before asking anything** — questions must be specific, not generic.

Fixed four-step arc (identical regardless of LLM backend):

1. **Setup & reasoning** — what did you see? what was the confluence?
   (each factor → a bar-pinned annotation)
2. **The plan** — stop? target? R at entry? Agent cross-checks stated plan
   against actual fills and flags drift ("you said stop 12.40 but exited 12.31 — did you move it?")
3. **The feeling** — emotional state at entry / mid-trade, timestamped.
4. **The verdict** — the forced distinction: *did the setup fail, or did you
   fail the setup?* (good-trade-lost / bad-trade-won / psychology loss / clean win …)

Three artifacts per debrief:
- **Enrichment** — notes, grade, checklist, tags written to `trade_enrichment` (debrief *is* journaling)
- **Scenario** — decision point + annotations + answer key, blind-mode ready
- **Psychology data points** — timestamped feelings + verdict, feeding the C3 leak detector

Debriefs work on live **and** sim trades (debrief immediately after a drill = tightest loop).

---

## 2. LLM integration (decided 2026-07-09)

**Provider seam, three backends, protocol-identical:**

```
backend/app/llm/
  base.py           # LLMProvider protocol (frozen v1, see §4)
  claude_cli.py     # DEFAULT PAID PATH: subprocess → `claude -p --output-format json`
                    #   uses the operator's Claude Pro/Max subscription (OAuth, no key).
                    #   Post-2026-06-15 policy: headless usage draws from the plan's
                    #   monthly Agent SDK credit pool at API rates — fine for
                    #   per-day debrief volume. No credentials in this repo, ever.
  anthropic_api.py  # optional: official `anthropic` SDK + ANTHROPIC_API_KEY in .env,
                    #   model claude-opus-4-8, pay-per-token developer account.
  form.py           # fallback (DEFAULT config): no LLM — the same 4-step interview
                    #   as an adaptive structured form. Zero privacy cost.
```

- `.env`: `TRADEDESK_LLM=none|claude-cli|api` — **default `none`** so the
  local-only / zero-credential guarantee holds out of the box. Enabling
  `claude-cli` or `api` is an explicit opt-in that sends trade + candle data
  (never credentials) to Anthropic; README must say so plainly.
- The agent gets **tools, not context blobs**: `get_trade`, `get_candles_around`,
  `save_enrichment`, `create_scenario`. `claude_cli.py` passes tool schemas via
  headless mode / Agent SDK; `form.py` implements the same steps as form pages.
- No LLM output is executed. The agent's writes go only through the two save
  tools, which validate against the frozen schemas.

---

## 3. Data model additions (SQLite, migrations in `backend/app/db/migrations/`)

```
scenarios(id PK, title, archetype,               -- archetype: gap-and-go|failed-breakout|chop|trend-day|custom
          source_type,                           -- real|synthetic|debrief
          symbol NULLABLE, session_date NULLABLE,-- real/debrief: ref into candles_cache
          synth_seed NULLABLE, synth_params_json NULLABLE,  -- synthetic: deterministic regeneration
          start_bar, decision_bar NULLABLE, end_bar NULLABLE,
          drill_type,                            -- decision-point|management-only|do-nothing|spot-setup|drawdown-script
          annotations_json,                      -- [{bar, text, kind}] bar-pinned teaching notes
          answer_key_json,                       -- correct action(s) + explanation shown post-reveal
          checklist_template_id NULLABLE,        -- which rule checklist scores this drill
          source_trade_id NULLABLE FK,           -- debrief-authored: the originating trade
          blind_default BOOLEAN, active, created_at)

exercise_attempts(id PK, scenario_id FK, started_at, finished_at,
          mood_checkin NULLABLE,                 -- calm|rushed|frustrated|null (C3)
          actions_json,                          -- REPLAYABLE: [{bar, action, price?, qty?, ts}]
          process_score REAL,                    -- 0..1, rule adherence + decision correctness
          outcome_pnl REAL NULLABLE,             -- recorded but NEVER scored/rewarded
          checklist_json, verdict, notes)

debriefs(id PK, trade_id FK, llm_backend,        -- none|claude-cli|api
          transcript_json,                       -- full Q/A turns
          plan_json,                             -- stated stop/target/R + drift flags
          feelings_json,                         -- [{ts_or_phase, feeling}]
          verdict,                               -- setup-failed|failed-the-setup|good-loss|bad-win|clean
          scenario_id NULLABLE FK,               -- emitted scenario, if any
          created_at)
```

Plus one column migration: `trades.context TEXT DEFAULT 'real'`
(`real|sim|training`) — journal analytics filter to `context != 'training'`
by default. Existing `simulated` flag is unchanged.

---

## 4. Frozen contracts (v1 — publish before parallel work, PM-owned)

### API surface (all under `/api`, read-only w.r.t. broker as ever)

```
GET  /training/scenarios?archetype=&drill_type=&active=
GET  /training/scenarios/{id}                  → scenario incl. annotations (answer key EXCLUDED)
POST /training/scenarios                       → create (authoring UI + debrief agent both use this)
GET  /training/scenarios/{id}/session          → replay payload: candles+indicators+levels sliced
                                                 to bar range, causal, blind-mode metadata
POST /training/attempts                        → open attempt {scenario_id, mood_checkin?}
PATCH /training/attempts/{id}                  → append actions_json events (bar-indexed)
POST /training/attempts/{id}/finish            → server computes process_score, returns
                                                 answer_key + explanation (first time key is exposed)
GET  /training/attempts?scenario_id=&from=&to=

POST /debriefs                                 → start {trade_id} → first agent turn
POST /debriefs/{id}/turn                       → user reply → next agent turn (or form step)
POST /debriefs/{id}/finish                     → persist artifacts; optionally emit scenario
GET  /debriefs?trade_id=

GET  /training/leaks                           → detected leaks + prescribed scenario playlist (C3)
GET  /training/progress                        → per-archetype mastery, streaks, due reviews (C4)
```

**Scoring is server-authoritative** (same reasoning as causal indicators):
`process_score` is computed in `backend/app/analytics/`, never client-side.
The answer key is never sent before `finish` — that's the no-cheating
equivalent of the no-lookahead guarantee.

### LLMProvider protocol (frozen v1) — `backend/app/llm/base.py`

```python
class LLMProvider(Protocol):
    def start_interview(self, ctx: DebriefContext) -> InterviewTurn: ...
    def next_turn(self, ctx: DebriefContext, history: list[Turn], user_reply: str) -> InterviewTurn: ...
    # InterviewTurn = question | tool_call(save_enrichment/create_scenario) | done(artifacts)
    # Intentionally NO free-form generation endpoints. Interview protocol only.
```

---

## 5. Ownership & orchestration for the agent team (no shared files)

Same roles and contract-first rule as `PLAN.md §4`. New/changed surface only:

| Owner | Owns (writes) in M6 | Reads (does not edit) |
|---|---|---|
| **PM** | `backend/app/api/routes/training.py`, `routes/debriefs.py`, `backend/app/models/training.py`, `docs/PLAN-M6-TRAINING.md`, `docs/DEMO-M6*.md` | everything |
| **data-engineer** | `backend/app/db/migrations/*` (new tables + trades.context), `backend/app/llm/` (all backends), scenario CRUD persistence | `models/` |
| **quant** | `backend/app/analytics/training.py` (process scoring, leak detector), `backend/app/analytics/synth.py` (synthetic scenario generators), replay slicing for bar ranges | `models/`, `indicators/` |
| **frontend** | `frontend/src/pages/Training*`, `frontend/src/pages/Debrief*`, drill player (reuses replay components), progress/quest UI | `api/` (as HTTP) |
| **qa-reviewer** | `backend/tests/test_training_*`, `test_llm_*`, `test_debrief_*` | all source |

**Orchestration order (contract-first):**
1. PM publishes §3 schemas + §4 contracts as code stubs (models + route
   signatures + `llm/base.py`) and freezes them. **Nothing else starts first.**
2. Then C0 tasks run in parallel per the ownership table; teammates message PM
   for contract changes — never edit contract files directly.
3. Per `~/.claude/CLAUDE.md`: worktrees have no dev env — hand off with manual
   test notes; run `make test` / `make dev` only in the primary checkout.
4. Every phase ends with a `docs/DEMO-M6x.md` click-path + qa sign-off (PLAN.md §6 DoD).

---

## 6. Seeded task list

Format per PLAN.md §5: `[id] (owner) — description → acceptance`. `←` = depends on.

### C0 — Foundations (no UI) ✅ **Done.**
- `[6.0.1] (PM)` ✅ Freeze contracts: `models/training.py`, route stubs, `llm/base.py` → importable, typed, reviewed.
- `[6.0.2] (data-eng)` ✅ Migrations: `scenarios`, `exercise_attempts`, `debriefs`, `trades.context` ← 6.0.1 → `make test` migration round-trip green; journal analytics exclude `context='training'`.
- `[6.0.3] (data-eng)` ✅ `llm/form.py` (none-backend): 4-step interview as deterministic state machine ← 6.0.1 → full debrief completes with `TRADEDESK_LLM=none`, artifacts persisted.
- `[6.0.4] (quant)` ✅ Replay slicer supports arbitrary bar ranges + `decision_bar` pause metadata ← 6.0.1 → causal guarantee test extended to sliced ranges.
- `[6.0.5] (qa)` ✅ Contract tests: answer key never in pre-finish responses; training trades invisible to journal stats ← 6.0.2.

### C1 — Decision-point drills (Pillar 1+3 core, no LLM) ✅ **Done. See `docs/DEMO-M6a.md`.**
- `[6.1.1] (quant)` ✅ Process scorer v1: decision correctness vs answer key + checklist adherence ← 6.0.2. `backend/app/analytics/training.py`; 13 hand-computed fixture tests.
- `[6.1.2] (frontend)` ✅ Drill player: load scenario → replay to decision bar → pause → enter/skip/wait → tape plays out → reveal answer key + score ← 6.0.4, 6.1.1. Blind mode hides ticker/date. `frontend/src/pages/DrillPlayerPage.tsx` + `DrillLobbyPage.tsx`.
- `[6.1.3] (frontend+PM)` ✅ Scenario authoring UI: pick symbol/date/bars on the existing replay chart, pin annotations, write answer key ← 6.0.2. `frontend/src/pages/ScenarioAuthorPage.tsx` + `ScenarioListPage.tsx`.
- `[6.1.4] (PM)` ✅ ~10 seed scenarios covering PDH-break, VWAP-reclaim, OR-breakout, failed-breakout, gap-and-go, midday-chop (do-nothing), trend-day (management-only), VWAP-rejection-short, PDH-break blind, gap-fill fade ← 6.1.3. Idempotent seed script: `backend/scripts/seed_scenarios.py` (`--dry-run`, `--force`). Bit-rot guard: `test_seed_scenarios.py`.
- `[6.1.5] (qa)` ✅ Attempt event-log tests: actions_json replayable, ordered, bar-indexed ← 6.1.2. `test_attempt_event_log.py` (10 tests).
- **Bar-frame contract fix (C1 integration):** attempt.actions_json is stored in the scenario's **global** bar frame; the `/session` response's `decision_bar` is in **local** (slice-relative) frame. Drill player must translate local → global via `session.meta.start_bar` on every PATCH. Regression tests in `test_training_scorer_e2e.py`.
- **Demo (DEMO-M6a):** ✅ `docs/DEMO-M6a.md` — open Training → pick a blind drill → decide at the pause → tape resolves → process score + explanation; attempt visible in DB with full action log.

### C2 — Agent debrief (Pillar 3 authoring + Pillar 4) ✅ **Done. See `docs/DEMO-M6b.md`.**
- `[6.2.1] (data-eng)` ✅ `llm/claude_cli.py`: subprocess to `claude -p --output-format json` for the 4-step interview; uses the operator's Claude Pro/Max OAuth session. Graceful fallback on binary-absent / non-zero exit / timeout / invalid JSON / shape mismatch. 22 tests stub subprocess.
- `[6.2.2] (data-eng)` ✅ `llm/anthropic_api.py`: official `anthropic` SDK, `claude-opus-4-8`, key from `.env`. Contract-identical to 6.2.1 — shared `build_prompt_*` factored into `llm/prompts.py`. 17 tests stub the SDK.
- `[6.2.3] (frontend)` ✅ Debrief UI: chat for LLM backends, stepper for form backend; drift-flag callout; artifacts preview before save; "Save & make a drill from this" emits a scenario and navigates to the C1 drill player. `pages/DebriefPage.tsx` + trade-detail entry point.
- `[6.2.4] (PM)` ✅ README + `.env.example`: `TRADEDESK_LLM` modes, subscription-vs-API billing note, explicit privacy opt-in wording ← 6.2.1. Also qualified README top-line "data never leaves" claim ("by default … unless you opt in").
- `[6.2.5] (qa)` ✅ Backend-swap tests: form/cli/api produce schema-identical `Debrief` rows and artifacts; no canary API key in any row or log; factory picks up settings changes per-request; missing `ANTHROPIC_API_KEY` → HTTP 501 (backend-not-configured), not silent fallback. 17 cross-backend equivalence tests in `test_llm_backend_swap.py`.
- **Fix landed alongside 6.2.5:** `llm/base.py::get_llm_provider` wraps `AnthropicAPIProvider.__init__` `RuntimeError` as `NotImplementedError` so the debrief route surfaces missing-key as HTTP 501 (not the previously-observed 500).
- **Demo (DEMO-M6b):** ✅ `docs/DEMO-M6b.md` — debrief a completed trade on all three backends; emitted scenario is immediately playable in the C1 drill player.

### C3 — Psychology & leak loop (Pillars 2+4) ✅ **Done. See `docs/DEMO-M6c.md`.**
- `[6.3.1] (quant)` ✅ Synthetic scenario generators (seeded, deterministic): gap-and-go, failed-breakout, chop, trend-day. `providers/mock/scenarios.py`; wired into the slicer's synthetic-source branch. Same seed ⇒ byte-identical bars.
- `[6.3.2] (quant)` ✅ Drill variants: do-nothing + management-only (scored since 6.1.1), drawdown-script (NEW — multi-session engineered losses, `bar // 1000` session encoding, outcome-agnostic scoring). One drawdown-script seed added.
- `[6.3.3] (quant)` ✅ Leak detector: `analytics/leaks.py` mines trades + debrief verdicts + attempts → named leaks (revenge_trading, chop_overtrading, moved_stops, psychology_loss) + prescribed playlists. `GET /api/training/leaks`. Detects the seeded revenge-trading fixture.
- `[6.3.4] (frontend)` ✅ Mood check-in (calm/rushed/frustrated) pre-drill; `/training/leaks` page with URL-driven one-click playlist walker (mood pinned across the sequence).
- `[6.3.5] (qa)` ✅ Behavioral metrics: `analytics/behavior.py` — moved-stop, hesitation, revenge-size detectors on `actions_json`. 18 tests.
- **Demo (DEMO-M6c):** ✅ `docs/DEMO-M6c.md` — leak page names a real weakness → prescribed playlist → do-nothing + drawdown-script drills score discipline not P&L.

### C4 — Gamification layer ✅ **Done. See `docs/DEMO-M6d.md`.**
- `[6.4.1] (quant)` ✅ Mastery model: per-archetype level from a rolling window of process scores (4 bands); discipline streak (consecutive ≥0.7 by finished_at); SM-2-lite due-review scheduler resurfacing failed archetypes with a deterministic fresh synth_seed. `analytics/mastery.py`, `GET /api/training/progress`. `outcome_pnl` never read.
- `[6.4.2] (frontend)` ✅ Progress page: 4-dot mastery meters, discipline-streak cards (no P&L), due-review queue with "Review now → fresh tape" (Option B — creates a new synthetic scenario from the suggested seed), weekly quest bars. `pages/ProgressPage.tsx`.
- `[6.4.3] (qa)` ✅ Anti-gambling audit: AST audit of scorer + mastery (no P&L in reward code), frontend grep (no P&L on progress page), behavioral proofs (disciplined-loser advances, lucky-gambler doesn't), signal-guard (leaks may read losses). `test_anti_gambling_audit.py` (10 tests) + `docs/M6-ANTI-GAMBLING-AUDIT.md`.
- **Demo (DEMO-M6d):** ✅ `docs/DEMO-M6d.md` — fail a drill → streak resets → archetype resurfaces with a fresh seed → passing on process (even at a loss) advances mastery.

---

## 7. Open items (decide during C0, don't block on them)
- Spot-the-setup drill (precision/recall flagging over a full session) — designed, scheduled opportunistically in C3 if capacity allows.
- Execution friction (slippage/partial fills) in drill fills — v1 fills at bar close; revisit after C1 feedback.
- Local model backend (Ollama) — the seam supports it; only add if `claude-cli` credit pool proves limiting.
