# DEMO — #67: Collapse the two day surfaces into one (`/journal/:date`)

**Goal:** TradeDesk had two parallel "review a session day" surfaces — the
chart-anchored day-cards page at `/journal/:date` and a separate `/daily-review`
page that carried a day-summary stat row + reflection prompts but reached each
trade through a **chartless drawer** (`TradeDetail`). Two funnels, one of them
leading away from the chart. This collapses them into ONE surface: `/journal/:date`
absorbs the stat row and prompts, its cards expand to a mini chart in place, and
both `/daily-review` and the `TradeDetail` drawer are retired.

Scope: `frontend/` only. Epic #64, workstream WS-C. Issue #67. The backend
contract is unchanged — `useDailyReview` already returned summary + prompts.

## What lands

- **Day-summary stat row on `/journal/:date`.** `JournalDayPage` now also calls
  `useDailyReview(date)` (keyed on the same `:date` route param as the cards) and
  renders a `DailySummary` row — Net P&L, Trades, Win rate, Profit factor, Avg R,
  Avg hold — ABOVE the trade-card grid. Moved as-is from the retired page.
- **Reflection prompts below the cards.** `JournalPrompts` (checklist + local
  `<Textarea>` reflections) moved over verbatim, keyed on `date` so answers reset
  per day. The "answers are local to the session" behavior is unchanged;
  persistence is a separate issue (#68), explicitly out of scope here.
- **Mini expandable chart on trade cards.** Each card's neutral `Sparkline` is now
  a click affordance (chevron + sparkline) that expands IN PLACE to a compact
  read-only candle chart with entry/exit fill markers, scoped to that trade's hold
  window; a **Collapse** control restores the grid. This is a new
  `TradeMiniChart` component that **reuses the existing `ReviewChart` wrapper**
  (same marker vocabulary, ET axis, teal/red-by-side entries + neutral-grey exits)
  at a fixed `h-40`, with self-contained hover state and no replay controls.
- **Expand vs. open the cockpit are separate affordances.** Clicking the
  sparkline/chevron expands the mini chart (a quick look, stays on the page). A
  distinct **Open** button (`Maximize2` icon) on each card navigates to
  `/trades/:id` for full analysis. The whole card is no longer one big navigate
  button.
- **`TradeDetail` drawer retired everywhere and deleted.** On the main Journal
  page, `TradesTable` rows now **navigate** to `/trades/:id` (same destination as
  day cards) instead of opening the drawer — the `selectedId` state and
  `<TradeDetail>` usage are gone. `DailyReviewPage` (the drawer's other host) is
  deleted. `frontend/src/components/journal/TradeDetail.tsx` is deleted; no
  importers remain.
- **`/daily-review` becomes a redirect; nav keeps its label.** `App.tsx` renders a
  small `DailyReviewRedirect` (`/daily-review` → `/journal/<todayET()>`), mirroring
  the existing `DebriefRedirect`. The SideNav "Daily Review" item keeps its label
  and `CalendarCheck` icon and points at `/daily-review`, so old bookmarks and the
  nav both land on today's ET session; pick another day from the Journal P&L
  calendar.
- **All dates shown are ET session dates.** The page renders `session_date` /
  `todayET()` throughout (#60/#61 fix preserved); the mini chart frames its window
  from fill timestamps but never re-derives a *calendar day* from a timestamp.

## Mini-chart approach

Reused `ReviewChart` rather than instantiating lightweight-charts fresh, so the
mini chart's markers, axis, and colors are identical to the cockpit. `TradeMiniChart`
fetches `useTradeReview(id)` (fills + underlying descriptor) and `useCandles({symbol,
date: session_date, timeframe: "1m"})`, both enabled only while the card is expanded
(collapsed cards cost nothing). It clips the day's candles to the fill window
(`min(fill ts) − 5m … max(fill ts) + 5m`), falling back to the full day if the
window yields nothing, and passes a local no-op `hoveredFillId` state (there's no
fill table to link to). No timeframe switch, no replay — full analysis stays on the
cockpit.

## Before / After

| | Before | After |
|---|---|---|
| Day surfaces | `/journal/:date` (cards) **and** `/daily-review` (stats + prompts + table) | one: `/journal/:date` (stats + cards + prompts) |
| Per-trade view from a day | chartless `TradeDetail` drawer | mini chart in place, or `/trades/:id` cockpit |
| Main Journal table row | opened the `TradeDetail` drawer | navigates to `/trades/:id` |
| `/daily-review` | rendered `DailyReviewPage` | redirects to `/journal/<todayET()>` |
| `TradeDetail` drawer | used by JournalPage + DailyReviewPage | deleted, no importers |

## Verify (click-path)

```bash
# .env: TRADEDESK_MODE=mock
make dev            # backend :9000 + frontend :5173
```

1. Open **`/journal/2026-07-10`** (a day with trades). The stat row renders above
   the card grid; prompts render below.
2. Click a card's **sparkline/chevron** → a mini candle chart expands in place with
   entry/exit fill markers over the hold window; **Collapse** restores the grid.
3. Click a card's **Open** button → navigates to the cockpit `/trades/:id`.
4. Open **`/journal`** (main analytics), click any **trades-table row** → navigates
   to `/trades/:id` (no drawer opens).
5. Open **`/daily-review`** → URL redirects to **`/journal/<today ET>`**.

## Live-run evidence (this session)

Mock mode. `/journal/2026-07-10`: stat row (Net P&L −$865.00, 27 trades, win rate
40.7%, PF 0.28, avg hold 4m), 27 cards, prompts below. Expanding the first card
(IWM 297C) rendered a 1m candle chart with `entry 1@0.59` / `exit 1@0.61` markers
framed to the 16:45–16:54 ET hold window; Collapse restored the grid. Card **Open**
→ `/trades/11` cockpit. `/journal` table row → `/trades/135` cockpit (no drawer).
`/daily-review` → `/journal/2026-07-13`. Only console entries are the pre-existing
React Router v7 future-flag warnings and a favicon 404 from the harness — no app
errors. `npm run build` (tsc + vite) green.

## Screenshots

- `.agents/screenshots/67-journalday-full.png` — `/journal/2026-07-10`: stat row +
  card grid, each card with the expand affordance and an Open button.
- `.agents/screenshots/67-card-expanded.png` — a card expanded to the mini candle
  chart with entry/exit markers over the hold window; Collapse control visible.
- `.agents/screenshots/67-cockpit-nav.png` — the cockpit reached via a card's Open
  button (`/trades/11`).
- `.agents/screenshots/67-prompts.png` — `/journal/2026-04-15` scrolled to show the
  Journal prompts section below the cards.

## Files changed

- **Added** `frontend/src/components/journal/TradeMiniChart.tsx` — compact
  read-only candle chart scoped to a trade's hold window; reuses `ReviewChart`.
- **Changed** `frontend/src/pages/JournalDayPage.tsx` — added the `DailySummary`
  stat row (from `useDailyReview`) and `JournalPrompts`; cards expand to
  `TradeMiniChart` in place with a separate **Open → /trades/:id** affordance.
- **Changed** `frontend/src/pages/JournalPage.tsx` — `TradesTable` rows navigate to
  `/trades/:id`; removed `selectedId` state, the `<TradeDetail>` usage, and the
  import.
- **Changed** `frontend/src/App.tsx` — dropped `DailyReviewPage` import/route; added
  `DailyReviewRedirect` (`/daily-review` → `/journal/<todayET()>`).
- **Changed** `frontend/src/components/journal/TradesTable.tsx` — doc comment
  (row click navigates to the cockpit, not a drawer).
- **Changed** `frontend/src/lib/debriefs.ts`,
  `frontend/src/components/journal/TradeEnrichmentPanel.tsx` — doc comments no
  longer reference the deleted `TradeDetail`.
- **Deleted** `frontend/src/pages/DailyReviewPage.tsx`.
- **Deleted** `frontend/src/components/journal/TradeDetail.tsx`.

## Manual test plan (for reviewer, real env)

```bash
git checkout feat/issue-67-day-surface-merge
cd frontend && npm run build            # green (tsc + vite)
# .env: TRADEDESK_MODE=mock
make dev
#   → /journal/<day with trades> : stat row + cards + prompts on one surface
#   → click a card's sparkline    : mini chart expands with fill markers; Collapse restores
#   → click a card's Open button  : → /trades/:id cockpit
#   → /journal table row          : → /trades/:id (no drawer)
#   → /daily-review               : redirects to /journal/<today ET>
```

## Out of scope

- Prompt persistence (answers remain session-local) — separate issue #68.
- The now-unused `components/ui/drawer.tsx` primitive is left in place (shared UI
  primitive; deleting it is outside this task's file ownership).
