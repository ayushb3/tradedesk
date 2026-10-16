# DEMO — M7a: Trade Review page + calendar day drill-in

**Goal:** turn a closed trade into something you can *study*. Click a day on the
Journal calendar → see that day's trades as cards with a sparkline of the
underlying during each hold → click a card → a full **Trade Review** page: the
underlying candle chart with your entry/exit fills marked, a fills table
hover-linked to those markers, and the same journal enrichment (notes, tags,
grade, checklist) you edit elsewhere — all with prev/next navigation across the
day. Process-focused, never P&L celebration.

M7a ships **share trades** (options render the underlying chart only — the
option premium chart is M7b). The review endpoint already carries nullable
`option`/`premium_bars` fields so M7b extends it without a breaking change.

## Run
```bash
make dev   # backend :9000 + frontend :5173, mock mode (TRADEDESK_MODE=mock)
```
Everything below works with zero credentials in mock mode.

## Verify (click-path)

### 1. Calendar day → day drill-in (`/journal/:date`)

1. Open **http://localhost:5173** → **Journal**.
2. The P&L calendar shows day tiles with net + W/L record (unchanged from M6).
3. **Click a day tile** (e.g. Jul 10). You navigate to **`/journal/2026-07-10`**
   (a route now — not the old `/daily-review?date=` jump).
4. The day page header reads **"Jul 10 · 27 trades"** with a back link to Journal.
5. Each trade is a **card**: contract descriptor (`contract_label ?? symbol`),
   direction badge, net P&L (neutral color), R (`—` when no stop recorded), hold
   time, and a **sparkline of the underlying over the hold window**. Empty days
   render a friendly empty state, not an error.


### 2. Trade card → Trade Review (`/trades/:id`)

1. **Click a card** (e.g. QQQ). You land on **`/trades/67`**.
2. **Header**: descriptor + direction + `Closed`, open→close timestamps + hold
   time, QTY, Entry→Exit avg prices, Net P&L, P&L %, R-multiple (`—` when no
   stop), fees. No streak/celebration copy — neutral formatters throughout.
3. **Underlying chart** (`QQQ underlying`) with a **1m/5m timeframe selector**.
   Candles come from the existing `useCandles` path — no new provider seam.
4. **Entry/exit markers**: one per fill — ▲ below the bar for buys, ▼ above for
   sells — with a `entry 1@1.47` / `exit 3@1.16` qty+price label, entry vs exit
   visually distinct. Partial fills all shown.


### 3. Fills table, hover-linked to the chart

1. Scroll to **Fills**: TIME / SIDE / ROLE / QTY / PRICE / FEES, ordered by fill
   sequence.
2. **Role** is derived from side vs. direction: for a long trade `buy → Entry`,
   `sell → Exit` (swapped for shorts). The QQQ example is a 6-fill scale-in /
   scale-out — four entries, two exits.
3. Hovering a fill row highlights its chart marker and vice-versa (shared
   hovered-fill state on the page).


### 4. Enrichment edits persist

1. In the **Journal** panel (right column) set a **Grade** (e.g. B), or edit
   notes / tags / checklist / stop, then **Save**.
2. **Reload the page** — the grade is still selected. Enrichment writes through
   the existing `PATCH /api/trades/{id}` and survives reload. (The panel is the
   same `TradeEnrichmentPanel` the Journal drawer uses, extracted so both share
   one implementation; the drawer's save-from-footer behavior is unchanged.)


### 5. Prev / next within the day

1. The header shows **‹ Prev · N / total · Next ›**. Neighbors are derived from
   the same-day trade list (`useDayTrades(session_date)`), so navigation stays
   within the day in chronological order.
2. Click **Next** → the next trade's review page loads (e.g. QQQ `/trades/67` →
   TSLA `/trades/221`).


## Backend (additive, read-only)

Two new endpoints in a new `review.py` router (the frozen M3 `trades.py`
contract is untouched):

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/trades/{trade_id}/review` | trade (hydrated w/ tags+enrichment) + ordered fills (with entry/exit role) + underlying descriptor; `option`/`premium_bars` null in M7a |
| `GET` | `/api/journal/days/{date}/trades` | that day's trade cards, each with a downsampled underlying sparkline over the hold window |

- **Sparkline** = underlying close prices over `[open_ts, close_ts]`,
  downsampled server-side to **≤ 40 points** by uniform stride (first + last
  always included). Zero candles in window → `sparkline: null` and the card
  omits it. Pure `downsample_closes` helper, unit-tested without HTTP.
- **Fills** join `trade_fills → fills` ordered by `seq`; role derived per
  direction.

## Scope fence (what M7a deliberately does NOT do)

- ❌ No option premium chart, `get_option_bars`, or synthetic Black-Scholes (M7b).
  Options trades render the **underlying** chart only — `option` is null.
- ❌ No MFE/MAE (R9 — options-scoped, deferred).
- ❌ No backtest handoff, replay URL params, ghost markers, or `origin_trade_id`
  (M7c).
- ❌ No new indicators, no provider changes, no order-entry code (read-only
  guarantee).

## Tests

- Backend: `cd backend && ./.venv/bin/python -m pytest` — **427 passed**
  (17 new across `test_review.py` + `test_day_trades.py`: payload shape, fills
  ordering + role, 404, multi-fill trade, sparkline downsample bound, empty day).
- Frontend: M7a files add **zero** new `tsc` errors. (The repo has 7 pre-existing
  `tsc` errors in Replay/Scenario files, present on `main`, outside M7a scope.)
- Live: full click-path above driven with `playwright-cli` against `make dev` in
  mock mode; screenshots in `docs/media/m7a/`.
