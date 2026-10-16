# TradeDesk — Decisions (open questions + my recommendations)

Each item has my recommended answer so work isn't blocked. **Override any of these and I'll adjust before Phase 1.**
None of these block writing PLAN.md; they shape implementation.

| # | Question | Recommendation | Why |
|---|----------|----------------|-----|
| 1 | Mock candle source: synthetic vs cached real bars? | **Seeded synthetic** (GBM + realistic intraday session shape: open drive, midday chop, close), deterministic per (symbol, date). Optional cached-real fixtures later. | Reproducible tests, zero external dependency on day one, deterministic indicator fixtures. |
| 2 | Round-trip grouping: FIFO vs average-cost? | **FIFO lot matching**; report avg entry/exit + net P&L. Supports scale-in/out and shorts. | Matches how most journals (incl. TradeZella) reason about lots; deterministic and testable. |
| 3 | Opening Range window length? | **5 minutes** (09:30–09:35 ET) default; configurable `{1,5,15,30}` in config. | Common day-trade default; cheap to make configurable. |
| 4 | R-multiple without a logged stop? | `r_multiple = null` unless `stop_price` present. Sim trades capture intended stop at entry; live trades let you add stop in trade detail. Avg R computed only over trades with a stop. | Avoids fabricating risk denominators; keeps the metric honest. |
| 5 | Timezone handling? | Store **UTC** (API JSON ends in `Z`); sessions keyed by ET trading date. **Display clock times in America/Los_Angeles, labelled PT** (changed 2026-10-15; `DISPLAY_TZ` in `frontend/src/lib/format.ts`, `TRADEDESK_DISPLAY_TZ` for backend time-of-day buckets). | US equities session logic (PMH/PML, OR) only makes sense in ET, so that stays. Ayush trades from Pacific time and his rules are written in PT, so every time he reads is PT. |
| 6 | Screenshot storage? | **Files on disk** under `data/screenshots/`, DB stores paths. | SQLite BLOBs bloat the DB and complicate backup; local files are simplest. |
| 7 | DB access layer? | **SQLAlchemy Core** + a tiny hand-rolled migration runner (`db/migrations/NNN_*.sql`). | Boring, typed, testable; avoids ORM overhead and Alembic ceremony for a single-user local app. |
| 8 | Replay reveal architecture? | Server computes full **causal** indicator/level series; frontend **reveals progressively**, never recomputes forward. | Single source of truth for the no-lookahead guarantee; one place for qa to test. |
| 9 | VWAP anchor? | **Session VWAP anchored at 09:30 ET** by default; config flag to include premarket. | Matches most traders' default VWAP; configurable for those who anchor at premarket open. |
| 10 | Fees/commissions? | `fees` field default 0, populated from fill payloads when present (Webull equities are commission-free). | Keeps net P&L correct if a provider does report fees. |
| 11 | Next.js vs Vite SPA? | **Vite SPA** (LOCKED). | lightweight-charts is browser-only (can't SSR the main screen); FastAPI already owns the backend so Next's Node backend/API routes/SEO are dead weight for a localhost single-user tool. |
| 12 | React vs Svelte/Vue/Solid? | **React + Vite + Tailwind + shadcn/ui** (LOCKED). | Deepest lightweight-charts + TanStack ecosystem; shadcn/ui gives the clean dark dashboard for free. Svelte was the honest #2 (nicer code, thinner ecosystem). |
| 13 | Replay transport UX + engine shape? | **Hook-first**: a `useReplay(session)` wrapper over the existing `useReplayEngine` adds `barLabel`, `jumpToTime`, and keyboard bindings; a single floating `ReplayControlBar` below the chart replaces the old top-toolbar transport + separate scrubber. Engine change is additive — speed set expanded to `[0.5,1,2,5,10,"max"]`, no method renames. Bookmarks / loop-range / multi-symbol sync deferred. | Designed three ways (minimal `set/seek`, hook-first, flexible TickStrategy+SyncGroup) and compared. Hook-first gave the tightest common-case wiring and the only unambiguous playing-state signal (filled play button); the flexible design solved problems we don't have yet. Verified by driving the live app (all 10 interaction checks pass). |
| 14 | Robinhood fills: how do they get in? | **Import the read-only order export** (`raw-option-orders-<start>.json`, produced outside this repo) with `backend/scripts/import_robinhood.py`. One fill per order: first execution time, VWAP price, fees = gross premium vs `net_amount` (so net P&L is the real cash), `provider_fill_id = rh-<order id>`. Export-file fills are kept out of a live provider's full-refresh wipe and incremental cursor; demo (mock) fills are refused (409) once real fills exist. Fills without a provider id get a deterministic `nid-<sha1>` id instead of being inserted twice. | Robinhood has no supported read API we will call from this app; the export is already guarded read-only. Order ids are stable across exports, so re-imports are free. Separating sources stops demo data or a Webull refresh from erasing or polluting real Robinhood history. |

## Non-negotiables (restating constraints, not open)
- **No live order placement, ever.** Provider interface exposes no order methods.
- **Local-only.** No cloud, no external auth. Data stays on the machine.
- **Secrets in `.env`** (gitignored); never hardcoded, never logged.
- **Mock-first.** Full end-to-end run with zero Webull credentials; live is a `.env` toggle (M4).
