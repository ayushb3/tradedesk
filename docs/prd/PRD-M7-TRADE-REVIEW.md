# PRD — M7: Trade Review 2.0 (TradeZella-parity per-trade review)

Status: **Draft.** Depends on: nothing new (mock-first; M5 optional for live fills).
Reference: `docs/prd/ROADMAP.md` §3, §5.

---

## 1. Problem

The journal today aggregates well (calendar, day P&L, stats) but reviewing a
*single trade* is shallow. To answer "where exactly did I enter and exit, and
what was the chart doing?" you have to mentally reconstruct it or open the
replay page and scrub manually. TradeZella's core daily-use loop —
calendar → day → trade → annotated chart → replay it — doesn't exist yet.

For options trades (the primary instrument, per the per-contract P&L work),
it's worse: the chart of the *underlying* doesn't show what your *position*
did. A 30%-premium swing can look like a 0.4% blip on the underlying.

## 2. Goals

1. Every closed trade has a **Trade Review page** reachable in ≤2 clicks from
   the calendar (calendar day → trade list → trade).
2. The review page shows **entries and exits as unmistakable markers** on:
   - the **underlying chart** (candles, existing indicator overlays), and
   - for options trades, an **option premium chart** (the contract's own
     price over the holding period) with the same markers.
3. **One-click "Backtest this trade"**: jumps into the existing replay engine
   preloaded to the trade's symbol/date, with the user's actual fills shown
   as ghost markers so the alternative play can be practiced immediately.
4. All of it works in mock mode with zero credentials.

### Non-goals

- No LLM features (that's M9; this page becomes the critic's canvas later).
- No option *chain* browsing, greeks dashboards, or IV analytics.
- No new indicator work.
- No edits to fills/trades data from this page beyond existing
  enrichment (notes, tags, grade — already in `trade_enrichment`).

## 3. User stories

- *As a trader reviewing my day*, I click a calendar day and see each trade as
  a card (contract, direction, net P&L, R, hold time, grade) — not just day
  aggregates.
- *As an options trader*, I open a trade and see where I entered and exited on
  the premium curve, so I can see the 40% giveback that the underlying chart
  hides.
- *As a student of my own mistakes*, I hit "Backtest this trade" and I'm in
  bar replay at the bars *before* my entry, with my actual entry/exit marked,
  so I can immediately drill the better version.
- *As a journaler*, I annotate the trade (notes, tags, grade, checklist) on
  the same page I'm looking at the chart — no context switch.

## 4. Functional requirements

### 4.1 Trade Review page (`/trades/:id`)

| # | Requirement |
|---|---|
| R1 | Header: contract descriptor (e.g. `SPY 07/11 628C`), direction, qty, open/close timestamps, hold time, net P&L, P&L %, R-multiple (when stop recorded), grade |
| R2 | **Underlying chart**: candles for the session (existing `candles_cache` / provider path), timeframe selector (1m/5m), with existing server-side indicators available as overlays |
| R3 | Entry/exit markers on the underlying chart: one marker per fill (▲ buy / ▼ sell), sized/labeled with qty and price, entry vs. exit visually distinct; partial fills all shown |
| R4 | **Option premium chart** (options trades only): the contract's price series over [open−30min, close+30min], same fill markers at fill premium prices |
| R5 | Premium chart data source is seam-based: `DataProvider.get_option_bars(contract, date, timeframe)`; mock + synthetic fallback per §5.2 with a visible "synthetic" badge when estimated |
| R6 | Enrichment panel: notes, tags, grade, checklist (existing `trade_enrichment` API) editable inline |
| R7 | Fill table: every fill with ts, side, qty, price, fees, and which chart marker it corresponds to (hover-link both ways) |
| R8 | Prev/next trade navigation within the same day |
| R9 | MFE/MAE readouts: max favorable/adverse excursion computed from premium series (options) or underlying (shares), displayed as bands on the chart |

### 4.2 Calendar → trade drill-in

| # | Requirement |
|---|---|
| R10 | Calendar day click opens (or expands to) the day view listing trade cards; each card links to `/trades/:id` |
| R11 | Trade cards show a **sparkline** of the premium/underlying during the hold, so scanning a day is visual, not tabular |

### 4.3 Backtest handoff

| # | Requirement |
|---|---|
| R12 | "Backtest this trade" button → `/replay?symbol=…&date=…&from_trade=<id>` |
| R13 | `ReplayPage` accepts URL params (today it's local state only — `ReplayPage.tsx:28-29`); `from_trade` fetches the trade and renders **ghost markers** (actual entries/exits) on the replay chart |
| R14 | Replay starts positioned N bars before the first entry (default 30, configurable), not at session open |
| R15 | Sim trades taken during such a replay session record `origin_trade_id` so "retries of trade X" can be compared later (cheap to capture, powers M9 analysis) |

## 5. Design

### 5.1 API additions (contract-first)

```
GET /api/trades/{id}/review
  → { trade, fills[], enrichment,
      underlying: {symbol, session_date, timeframes: [...]},
      option: {contract | null, synthetic: bool} }

GET /api/trades/{id}/option-bars?timeframe=1m
  → { bars: Candle[], synthetic: bool, method: "provider"|"bs_interp" }

GET /api/replay/session?symbol=&date=&from_trade=   (extend existing)
  → existing payload + ghost_fills[] when from_trade present
```

### 5.2 Option premium data — the seam and the fallback

New optional provider method:

```python
class DataProvider(Protocol):
    def get_option_bars(self, contract: OptionContract, date: str,
                        timeframe: str) -> list[Candle] | None: ...
```

- **mock provider**: synthesizes a premium series from its own underlying
  candles (delta-approx + noise), deterministic per contract seed.
- **webull provider**: returns `None`, permanently. Verified against the docs
  2026-07-10 (`docs/DEMO-M4.md` § API drift notes): the OpenAPI Data API has
  **no historical option bars** (asset categories are `US_STOCK`/`US_ETF`
  only), and even real-time option quotes require a separate OPRA Real-Time
  Non-display subscription. Historical premium data would need a third-party
  provider (Polygon, ThetaData) behind this same seam — out of scope for M7.
- **Synthetic fallback (server-side, provider-agnostic)** when the provider
  returns `None`: Black-Scholes premium estimated from underlying candles,
  with IV solved so the curve passes through the *actual recorded fill
  prices* (anchor points). Between anchors, interpolate IV linearly. Marked
  `synthetic: true` → UI badge "estimated premium". This is honest and good
  enough for review shape (where did it peak, how fast did it decay), which
  is the review use case.

### 5.3 Data model changes

```
trades:            + origin_trade_id NULLABLE (FK trades.id)   -- replay retries
option_bars_cache: (contract_key, timeframe, ts_utc, o,h,l,c,v, synthetic, method)
                   PK (contract_key, timeframe, ts_utc)
```

`contract_key` = canonical `SYMBOL_YYMMDD_C|P_STRIKE` (matches the journal's
per-contract grouping key — see memory: P&L per-contract grouping).

### 5.4 Frontend

- New `TradeReviewPage.tsx` (route `/trades/:id`); reuses the existing
  lightweight-charts wrapper; markers via the library's marker API.
- `JournalPage` day view gains trade cards + sparklines (sparkline = tiny
  canvas, data from the review endpoint's premium series downsampled).
- `ReplayPage` migrated to `useSearchParams` (R13) — an isolated, mechanical
  change; deep-linkability also benefits M6 drills and M9 citations.

## 6. Phasing

| Phase | Scope | Demo |
|---|---|---|
| M7a | Trade Review page for **share** trades (underlying chart + markers + fills + enrichment), calendar drill-in | Review a mock share trade end-to-end |
| M7b | Option premium: seam method, mock impl, synthetic fallback, premium chart + MFE/MAE | Review a mock option trade, premium curve with markers |
| M7c | Backtest handoff: replay URL params, ghost markers, `origin_trade_id` | Calendar → trade → backtest → sim retry recorded |

Each phase independently shippable; M7a alone already beats the status quo.

## 7. Risks & open questions

1. **Synthetic premium credibility.** BS-interpolation can mislead on IV-crush
   days (earnings). Mitigation: badge + method shown; anchor to real fills;
   document limits on the page tooltip. Accept: this is a review aid, not
   analytics input (MFE/MAE from synthetic series is labeled estimated).
2. **Webull option-bars endpoint availability** — ~~unverified~~ **resolved
   2026-07-10: it doesn't exist** (no historical option data in the OpenAPI
   Data API; real-time options need a separate OPRA sub — see
   `docs/DEMO-M4.md` § API drift notes). The synthetic fallback is the
   feature's primary data path, not a stopgap; the seam exists for a future
   third-party provider.
3. **Multi-day option holds** — premium series spans sessions; v1 restricts
   the chart to the close-date session with a "spans N days" note (matches
   the journal's close-date calendar convention).
4. **Open question:** should day view be a route (`/journal/:date`) or an
   expanding panel? Recommend route — shareable, and M9 agents can cite it.

## 8. Success metrics

- Calendar → any trade's annotated chart in ≤2 clicks, ≤1.5s load (mock).
- 100% of closed trades render a review page (shares and options).
- Backtest handoff lands in replay with ghost markers in one click.
- Zero regressions in existing journal/replay tests; new endpoints covered by
  contract tests in mock mode.
