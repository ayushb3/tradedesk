# PRD — M10: Live Signals Bridge (local listener daemon → TradeDesk)

Status: **Draft — intentionally thinner than M7–M9; firm up after M9 ships
and the listener daemon has run live for a few weeks.**
Depends on: M7 (trade review), M9 (agent infra); M5 for live Webull fills
(the adherence analysis is only meaningful against real fills).
Reference: `docs/prd/ROADMAP.md` §2 (integration via data, not code).

---

## 1. Problem

A separate local listener daemon (its own process, outside this repository)
captures a mentor's live sessions (audio → speech-to-text → LLM + knowledge
base) and pushes phone alerts. Those signals then evaporate — nothing records
what the mentor called, whether you acted, how your execution compared, or how
the call played out. TradeDesk has the trades; the daemon has the calls;
neither can answer *"do I follow the mentor well, and when I deviate, does it
cost me?"*

Chat + voice corroboration is a daemon concern; TradeDesk just receives
whatever signals the daemon emits.

## 2. Goals

1. **Signal ingestion**: the daemon POSTs every detected signal to TradeDesk;
   signals persist and render on a timeline.
2. **Call↔trade matching**: signals auto-match to your trades (same
   ticker/direction within a window) → each trade review shows "mentor
   context", each signal shows "what you did".
3. **Adherence analytics**: followed / skipped / deviated / faded rates,
   entry-lag distribution, and outcome-by-adherence — process-framed, per the
   anti-gambling rules.
4. **Replay the call**: a signal deep-links into bar replay at the call
   timestamp (M7's URL-param work) to practice the setup the mentor called.

### Non-goals

- **No order placement, no auto-follow, no "act now" UX.** Signals inform.
  The read-only guarantee is absolute; the daemon's manual-execution constraint
  carries over.
- No audio capture/transcription inside TradeDesk (stays in the daemon).
- No signal-quality leaderboard of the mentor — the analysis is about *your*
  process, not grading the mentor.
- Any chat-platform ingest is a daemon feature, not a TradeDesk one.

## 3. Requirements (sketch)

| # | Requirement |
|---|---|
| R1 | `POST /api/signals` (localhost, token-authed via shared secret in both `.env`s): `{source, ts, ticker, direction, kind: watch|entry|trim|exit, levels, confidence, transcript_excerpt, raw_json}` — idempotent on a externally supplied signal UID |
| R2 | The daemon adds a `tradedesk_notify` output alongside its phone push (config-gated, fire-and-forget; TradeDesk being down never breaks phone alerts) |
| R3 | `signals` table + Signals timeline page (filter by day/ticker/kind); signal detail shows the transcript excerpt |
| R4 | Matching job: signal ↔ trade candidates by ticker + direction + time window (default entry within 15 min of call); auto-match with confidence, manual confirm/reject UI; stored in `signal_trades(signal_id, trade_id, relation: followed|deviated|faded, matched_by)` |
| R5 | Trade Review page (M7) gains a "Mentor context" panel when a matched signal exists: call time vs. your entry time, called levels vs. your fills |
| R6 | Adherence report (weekly, joins M6 leak/report infra): follow rate, median entry lag, R-multiple by adherence bucket — phrased as process observations ("entries lag calls by ~4 min median"), never "you'd have made $X copying every call" |
| R7 | "Replay this call" → `/replay?symbol=&date=&at_ts=` positioned at the call bar (extends M7 R12–R14) |
| R8 | Mock mode: a fixture signal generator (replays a recorded session's signals against mock candles) so all UI demos without the daemon running |

## 4. Open questions (resolve before committing)

1. **Does the daemon's signal schema stabilize?** Its `analyze` output is still
   being tuned. Freeze the POST contract only after a few weeks of live runs;
   until then TradeDesk stores `raw_json` verbatim so early data isn't lost.
2. **Historical backfill** — should the daemon's past session logs import? Nice
   for cold-start adherence stats; decide based on how much usable history
   exists.
3. **One playbook** — switch the daemon's retrieval to TradeDesk's M8 KB
   (`POST /api/knowledge/retrieve`) so both systems cite the same corpus. Leaning
   yes; requires the daemon to tolerate TradeDesk being down (cache or fallback
   to its own index).
4. **Clock skew / call timestamping** — Speech-to-text chunk timestamps vs. bar
   timestamps; a ±1 bar tolerance probably suffices, verify empirically.

## 5. Success metrics

- A live daemon session produces signals visible in TradeDesk within
  seconds, with zero impact on phone-alert latency.
- ≥80% of signals for tickers you traded auto-match correctly (manual audit).
- The weekly report answers "did I follow the plan this week?" in one screen.
- All demos runnable in mock mode with the fixture generator.
