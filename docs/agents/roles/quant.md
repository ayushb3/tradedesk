---
role: quant
---

# Quant

Owns the indicator/levels engine and journal analytics — the causal, no-lookahead
computation layer everything else in the app trusts.

## Owns (writes)

- `backend/app/indicators/` — EMA(9/21/50/200), VWAP(session), RSI(14), levels
  engine (PDH/PDL, PMH/PML, day H/L, OR H/L)
- `backend/app/analytics/` — journal stats (win rate, profit factor, avg R,
  expectancy, hold time) and the replay slicer

## Reads (does not edit)

- `backend/app/models/` — domain models you compute against; frozen by `pm`

## Stack / domain context

The single most important invariant in this codebase: **indicators are
server-authoritative and causal.** The server computes each indicator/level as a
rolling series where the value at bar `N` uses only bars `≤ N`. The frontend does
progressive *reveal* of a pre-computed series during replay — it never
recomputes forward. This is what makes the no-lookahead guarantee testable in one
place, and it's the thing a lookahead-leak test (`qa-reviewer`) exists specifically
to catch.

Levels recompute per `session_date` (the ET trading date), not per UTC day.

## Conventions

- Python 3.11+, pandas + pandas-ta.
- Any new indicator/level must be provably causal — if you can't state which bars
  it depends on, it's not ready to ship.
- `r_multiple` is computed only when `stop_price` is present — don't synthesize a
  stop to force an R value.

## Verification

`cd backend && python -m pytest`, specifically the lookahead-leak test
(`indicator[N] ⟂ bars>N`) and indicator correctness against hand-computed
fixtures. A green test suite alone doesn't prove no-lookahead if you didn't add a
fixture for the new indicator — add one.
