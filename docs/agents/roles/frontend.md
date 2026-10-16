---
role: frontend
---

# Frontend

Owns the React/Vite/TypeScript UI: the replay chart, journal dashboard, trade
detail, and everything else under `frontend/`.

## Owns (writes)

- `frontend/` — components, pages, providers, lib

## Reads (does not edit)

- `backend/app/api/` — as an HTTP contract only (call it, don't edit the router
  code). If a response shape needs to change, that's a contract change owned by
  `pm`, not something to work around client-side.

## Stack / domain context

React + Vite + TypeScript + Tailwind CSS + shadcn/ui (Radix under the hood) +
lightweight-charts for candles, TanStack Table/Query for the journal grid and
data fetching. Dark, minimal, fast — the TradeZella/TradingView reference look.
Runs on `:5173`, backend on `:9000` (`make dev` boots both).

Non-obvious constraints:
- **Progressive reveal, not recompute.** During replay, indicators/levels are
  already-computed causal series from the backend — the frontend reveals them
  bar by bar. It must never recompute an indicator client-side; that would
  reintroduce the lookahead risk the backend was built specifically to prevent.
- **Anti-gambling UI rules** (`docs/M6-ANTI-GAMBLING-AUDIT.md`): process-focused
  copy, no P&L celebration animations/messaging anywhere in the UI, including
  training/gamification surfaces.
- **Read-only broker UI.** No order-entry forms, buttons, or flows — ever, in any
  feature.

## Conventions

- `npm run build` (`tsc -b && vite build`) must pass — this is also what CI runs.
- Match existing shadcn/ui + Tailwind patterns before introducing a new primitive.

## Verification

`npm run build` clean. For anything with a visible surface, drive it with `make
dev` in mock mode and exercise the actual flow (per the root `CLAUDE.md`'s
working-conventions: "VERIFY by driving the real app, not just tests") — a
passing typecheck doesn't mean the replay progressive-reveal renders correctly or
a chart marker lines up with its fill.
