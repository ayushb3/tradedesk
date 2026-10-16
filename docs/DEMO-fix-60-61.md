# DEMO — fix #60/#61: date correctness (ET trading day)

**Goal:** two related frontend date bugs where the browser's local timezone
leaked into trading-date logic. Both now anchor to **US Eastern**, the trading
day the backend already stores.

Scope: `frontend/` only. Issues #60, #61.

## What lands

- **#61 — default "today" now uses the ET trading day, not the browser clock.**
  `DailyReviewPage`'s inline `today()` helper built `YYYY-MM-DD` from
  `new Date().getFullYear()/getMonth()/getDate()` — the browser's *local*
  calendar date. A user west of ET late in the day (or anyone abroad) could
  default the Daily Review to the wrong session. Replaced with a shared
  `todayET()` util in `frontend/src/lib/format.ts`:

  ```ts
  export function todayET(): string {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit",
    }).format(new Date());
  }
  ```

  `en-CA` renders `YYYY-MM-DD`; the explicit `timeZone` mirrors the existing
  `formatDayMonth`/`formatClock` pattern in the same file. Used as the
  fallback session date (`latestSession ?? todayET()`).

- **#60 — Date column no longer shows the day *after* the session.**
  `TradesTable`'s "Date" column keyed off `open_ts` (a full UTC timestamp)
  rendered via `formatDayMonth`, which formats in UTC. A trade opened in the
  evening ET is *next-day UTC*, so it rendered as tomorrow. The column now uses
  `trade.session_date` (the correct ET trading date, already stored as
  `YYYY-MM-DD`). `formatDayMonth` anchors `≤10`-char strings to `T00:00:00Z`,
  so `session_date` renders correctly. Default sort state changed from
  `open_ts` → `session_date`; `YYYY-MM-DD` sorts lexically the same as the
  timestamp, so ordering is unchanged.

## Before / After

| | Before | After |
|---|---|---|
| Daily Review default date | browser-local calendar day | ET calendar day (`todayET()`) |
| Trades "Date" column | `open_ts` formatted UTC → off-by-one for evening-ET trades | `session_date` (ET trading day) → correct |

## Verify (click-path)

```bash
# .env: TRADEDESK_MODE=mock
make dev            # backend :9000 + frontend :5173
```

1. Open **`/daily-review?date=2026-07-10`**. The Trades table Date column shows
   **Jul 10** for every row — matching the session, not a UTC-shifted date.
2. Open **`/daily-review`** with no `?date`. The Session picker defaults to the
   latest session with trades, falling back to **today in ET** when trades
   haven't loaded yet (previously the browser-local day).
3. Click the **Date** column header → sort toggles asc/desc with no error
   (lexical `YYYY-MM-DD` order).

## Live-run evidence (this session)

Mock mode, `/daily-review?date=2026-07-10`: 27 rows, Date column renders
`Jul 10` across rows (matches `session_date`), zero console errors. Date header
sort toggles cleanly. `/daily-review` with no query param resolved the default
to `2026-07-13` (today, ET) — confirming `todayET()` returns the ET calendar
date. `npm run build` (tsc + vite) green.

Note: the mock `open_ts` values sit around midday UTC (≈ midday ET), so the
off-by-one isn't visible in mock data itself — the fix is structural (use the
ET-anchored `session_date` field the backend already provides).

## Screenshots

- `.agents/screenshots/daily-review-dates-fix.png` — Daily Review for the
  2026-07-10 session; Date column shows Jul 10.

## Files changed

- `frontend/src/lib/format.ts` — new `todayET()` util.
- `frontend/src/pages/DailyReviewPage.tsx` — use `todayET()`, drop inline
  local-clock `today()`.
- `frontend/src/components/journal/TradesTable.tsx` — Date column + default
  sort keyed on `session_date` instead of `open_ts`.

## Manual test plan (for reviewer, real env)

```bash
git checkout fix/issue-60-61-date-correctness
cd frontend && npm run build            # green
# .env: TRADEDESK_MODE=mock
make dev
#   → /daily-review?date=2026-07-10 : Date column all "Jul 10"
#   → /daily-review (no query)      : Session defaults to ET day / latest session
#   → click Date header             : sort toggles, no console error
# (optional) set OS timezone to e.g. Pacific/Auckland, reload /daily-review
#   → default date still resolves to the ET trading day, not tomorrow local
```
