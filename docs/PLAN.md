# TradeDesk — Plan (v1)

Local-only desktop web app: **Trade Journal** (Module A) + **Bar-Replay Backtester** (Module B).
Single user, runs on localhost, mock-first, read-only broker integration. **No live order placement, ever.**

Status: **Phase 0 — awaiting approval.** Nothing below is built yet.

---

## 1. Architecture (text diagram)

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (localhost:5173)                                     │
│  React + Vite + TS + Tailwind + shadcn/ui + lightweight-charts│
│                                      [frontend/]              │
│   ├─ Chart + Replay UI      (Module B)                        │
│   ├─ Journal dashboard      (Module A)                        │
│   └─ Trade detail / tagging (Module A)                        │
└───────────────┬─────────────────────────────────────────────┘
                │  HTTP/JSON (fetch)
┌───────────────▼─────────────────────────────────────────────┐
│  FastAPI backend (localhost:8000)   [backend/]                │
│                                                               │
│  api/         routers + request/response schemas  ◀── FROZEN  │
│               (contract v1, owned by PM)          CONTRACT    │
│  models/      pydantic domain models (Candle/Fill/…) ◀────────┤
│                                                               │
│  providers/   DataProvider interface  ── data-engineer        │
│   ├─ mock/    synthetic candles + fake fills                  │
│   └─ webull/  read-only SDK adapter (M4)                      │
│  ingest/      fill import + round-trip builder ── data-eng    │
│  indicators/  levels engine, VWAP/EMA/RSI ──────── quant      │
│  analytics/   journal stats + replay slicer ────── quant      │
│  db/          SQLite access + migrations ───────── data-eng   │
└───────────────┬─────────────────────────────────────────────┘
                │
        ┌───────▼────────┐        ┌──────────────────────┐
        │ SQLite (file)  │        │ data/screenshots/    │
        │ tradedesk.db   │        │ (blob files on disk) │
        └────────────────┘        └──────────────────────┘

.env  → mode toggle (mock|live) + Webull app_key/app_secret (gitignored)
```

**Key architectural principles**
- **Provider interface is the seam.** Webull, mock, and any future provider (Polygon, yfinance) implement the same `DataProvider` protocol. Nothing outside `providers/` imports a vendor SDK.
- **Indicators are server-authoritative and causal.** The server computes each indicator/level as a rolling *causal* series (value at bar `N` uses only bars `≤ N`). The frontend does progressive *reveal* of a pre-computed series during replay — it never recomputes forward. This is what makes the no-lookahead guarantee testable in one place.
- **Simulated and live trades share one schema.** Sim trades from replay land in the same `trades` table with `simulated=true`, so they flow through the identical analytics.

---

## 2. Data model (SQLite)

```
config(key PK, value_json)                     -- watch tickers, OR window, tz, etc.

candles_cache(symbol, timeframe, ts_utc, open, high, low, close, volume,
              provider, session_date)          -- PK (symbol, timeframe, ts_utc)

fills(id PK, provider_fill_id, account, symbol, side,           -- side: buy|sell
      qty, price, ts_utc, fees, simulated, raw_json)            -- raw_json: provider payload

trades(id PK, symbol, direction,               -- direction: long|short
       open_ts_utc, close_ts_utc, session_date,
       qty, avg_entry, avg_exit, gross_pnl, fees, net_pnl, pnl_pct,
       stop_price NULLABLE, r_multiple NULLABLE, hold_time_s,
       status, simulated)                       -- status: open|closed

trade_fills(trade_id FK, fill_id FK, seq)       -- ordered fill membership (FIFO lots)

tags(id PK, name UNIQUE, description, active)
trade_tags(trade_id FK, tag_id FK)              -- many-to-many

trade_enrichment(trade_id PK/FK, notes, grade,  -- grade: A|B|C|null
                 checklist_json, screenshots_json)  -- checklist: [{item,checked}], screenshots: [path]

checklist_template(id PK, item, sort_order, active)  -- configurable rule-adherence items
```

**Notes**
- All timestamps stored **UTC**; `session_date` is the **ET trading date** (`America/New_York`) so levels bucket correctly.
- Screenshots stored as files under `data/screenshots/`; DB holds paths only (no BLOBs).
- `r_multiple` computed only when `stop_price` is present (see DECISIONS #4).

---

## 3. API surface (contract v1 — frozen before parallel work)

All under `/api`. **Read-only w.r.t. the broker. No order-entry endpoints exist or will be added.**

**Health / config**
- `GET  /health`
- `GET  /config` · `PUT /config`

**Candles / market data**
- `GET /candles?symbol=&date=&timeframe=1m`            → full session candles
- `GET /candles/range?symbol=&start=&end=&timeframe=`

**Replay / indicators**
- `GET /replay/session?symbol=&date=`  → `{candles[], levels{}, indicators{ema9,ema21,ema50,ema200,vwap,rsi14}}`
  where every indicator/level array is causal (index `i` uses bars `≤ i`). Frontend reveals progressively.

**Simulated trades (replay)**
- `POST /sim/trades`  → body `{symbol, direction, entries[], exits[], session_date, notes?}` → persists as `simulated=true`

**Journal**
- `POST /import/fills`                      → pull fills from active provider, build round-trips
- `GET  /trades?from=&to=&symbol=&tag=&simulated=`
- `GET  /trades/{id}`
- `PATCH /trades/{id}`                      → notes, grade, tags, checklist, stop_price
- `GET  /tags` · `POST /tags` · `PATCH /tags/{id}`
- `GET  /analytics/summary?...filters`      → win rate, profit factor, avg R, expectancy, hold time
- `GET  /analytics/calendar?month=`         → per-day P&L
- `GET  /analytics/by?dimension=ticker|tod|tag|setup&...filters`
- `GET  /daily-review?date=`                → today's trades + journal prompts

**Provider interface (frozen v1)** — `backend/providers/base.py`
```python
class DataProvider(Protocol):
    def get_candles(self, symbol: str, date: date, timeframe: str = "1m") -> list[Candle]: ...
    def get_candles_range(self, symbol, start, end, timeframe) -> list[Candle]: ...
    def get_fills(self, since: datetime | None) -> list[Fill]: ...
    def get_positions(self) -> list[Position]: ...
    def get_account(self) -> Account: ...
    # Intentionally NO place_order / cancel_order / modify_order. Read-only by design.
```

---

## 4. Module boundaries & ownership (no shared files)

| Owner           | Owns (writes)                                   | Reads (does not edit) |
|-----------------|-------------------------------------------------|-----------------------|
| **PM (me)**     | `backend/api/`, `backend/models/`, `docs/`, `Makefile` | everything            |
| **data-engineer** | `backend/providers/`, `backend/ingest/`, `backend/db/` | `models/`             |
| **quant**       | `backend/indicators/`, `backend/analytics/`     | `models/`             |
| **frontend**    | `frontend/`                                     | `api/` (as HTTP)      |
| **qa-reviewer** | `tests/`                                         | all source            |

**Contract-first rule:** `backend/models/` + `backend/api/` schemas + `providers/base.py` are agreed and frozen (v1) **before** any parallel feature work. I own and publish them; teammates code against them and message me for changes.

---

## 5. Shared task list (seeded per milestone)

Each task: `[id] (owner) — description → acceptance`. Dependencies noted as `←`.

### M1 — Skeleton runs
- `[1.1] (data-eng)` DataProvider protocol + domain models (Candle/Fill/Position/Account) → importable, typed, no vendor imports.
- `[1.2] (data-eng)` MockProvider: deterministic synthetic 1-min candles for AAPL/RIVN/HOOD ← 1.1 → seeded, reproducible, realistic session shape.
- `[1.3] (PM+data-eng)` FastAPI app + `/health` + `/candles` ← 1.1 → returns mock candles as contract schema.
- `[1.4] (frontend)` Vite+React app; lightweight-charts renders 1-min candles + EMA9/21 ← 1.3 → AAPL chart visible.
- `[1.5] (PM)` `Makefile` `make dev` (backend+frontend), `.env.example`, `.gitignore` → one command boots both.
- **Demo (DEMO-M1):** `make dev` → open localhost:5173 → AAPL 1-min candles with EMAs.

### M2 — Replay
- `[2.1] (quant)` Indicators: EMA(9/21/50/200), VWAP(session), RSI(14) as **causal** series ← 1.1.
- `[2.2] (quant)` Levels engine: PDH/PDL, PMH/PML, day H/L, OR H/L; recompute per `session_date`.
- `[2.3] (quant)` Replay slicer + `/replay/session` — strict no-lookahead ← 2.1, 2.2.
- `[2.4] (frontend)` Replay controls (play/pause/step/speed) + progressive reveal + draw levels/indicators ← 2.3.
- `[2.5] (data-eng+frontend)` Sim-trade marking UI + `POST /sim/trades` persist ← 2.6.
- `[2.6] (data-eng)` DB schema + migrations (trades/fills/candles_cache/tags/enrichment).
- `[2.7] (qa)` **Lookahead-leak test** (indicator[N] ⟂ bars>N) + indicator correctness vs hand-computed fixtures ← 2.1–2.3.
- **Demo (DEMO-M2):** replay a session; levels recompute per date; mark a sim trade; see it in DB.

### M3 — Journal
- `[3.1] (data-eng)` Mock fills generator + `POST /import/fills` ← 2.6.
- `[3.2] (data-eng)` Round-trip trade builder (FIFO, longs+shorts, scale in/out) ← 3.1.
- `[3.3] (quant)` Journal stats: win rate, profit factor, avg R, expectancy, hold time; by tod/ticker/tag ← 3.2.
- `[3.4] (frontend)` Analytics dashboard: P&L calendar + stat cards + breakdowns ← 3.3.
- `[3.5] (frontend)` Trade detail: tags, notes, grade, rule checklist, screenshots ← 2.6.
- `[3.6] (frontend)` Daily review view + journal prompts ← 3.4.
- `[3.7] (data-eng)` Tags CRUD + seed course tags (`PDH-break`,`PMH-break`,`OR-breakout`,`VWAP-reclaim`,`EMA-pullback`).
- `[3.8] (qa)` Round-trip builder tests (FIFO edge cases) + stats tests ← 3.2, 3.3.
- **Demo (DEMO-M3):** import mock fills → round-trips → dashboard → tag/note/grade → daily review.

### M4 — Live Webull mode
- `[4.1] (data-eng)` WebullProvider real SDK adapter (read-only) behind interface.
- `[4.2] (data-eng)` `.env` toggle mock|live; graceful 403 for missing OpenAPI Quotes Subscription.
- `[4.3] (PM)` README: obtaining OpenAPI keys + OpenAPI Quotes Subscription + run steps.
- `[4.4] (qa)` Provider contract tests (mock) + live smoke behind flag.
- **Demo (DEMO-M4):** flip `.env` to live; graceful behavior with/without subscription.

### M5 — Webull adapter migration (landed 2026-07-09)
- Swapped deprecated `webull-python-sdk-{core,mdata,trade}` for `webull-openapi-python-sdk` 2.0.13 (Py 3.8–3.13 support; MCP uses same SDK).
- Added `backend/scripts/webull_auth.py` (create-token → SMS-verify → NORMAL poll); token persists at absolute path (`data/webull-token/`).
- Provider now raises `WebullTokenPending` (→ HTTP 428) when the token is missing/pending/expired, pointing operators at the auth script.
- Subscription-error wording updated to match what the API actually returns: "OpenAPI Quotes Subscription" (purchased at https://www.webullapp.com/quote; distinct from in-app quote subscriptions).
- Read-only guarantee preserved. 111 tests pass.

Commits: `27e8173` (A), `c40f257` (B), `1406ca0` (C), `f02d81a` (D backend), this commit (D docs).

See `docs/PLAN-M5.md` for the full migration plan.

### M6 — Training module (Module C: lessons/exercises) — planned 2026-07-09
Gamified drills on the replay engine + agent-guided trade debriefs. Four phases
(C0 foundations → C1 decision-point drills → C2 LLM debrief → C3 psychology/leaks
→ C4 gamification), new `LLMProvider` seam (`none|claude-cli|api`, default `none`),
process-scored (never P&L-scored). Frozen contracts, ownership table, and the full
seeded task list live in `docs/PLAN-M6-TRAINING.md` — start there.

### M7 — Trade Review 2.0 (PRD-M7)
TradeZella-parity per-trade review: calendar → day → trade → annotated chart →
one-click backtest. Full spec: `docs/prd/PRD-M7-TRADE-REVIEW.md`.

- **M7a — Trade Review page + calendar drill-in (landed, #35/#36).**
  `/trades/:id` cockpit (underlying chart + fill markers + hover-linked fill
  table + enrichment aside + prev/next) and `/journal/:date` day cards with
  sparklines. See `DEMO-M7a`.
- **M7b — Option premium seam (landed, #37, PR #51).** `get_option_bars`
  provider seam, Black-Scholes synthetic fallback anchored to real fills
  ("estimated" badge), premium chart + MFE/MAE. See `DEMO-M7b`.
- **M7c — Backtest handoff (pending, #38; prefactor #34).** Ghost markers +
  `origin_trade_id` via `/replay?from_trade=`.
- **M7.5 — Trade Review Funnel (epic #64, landed 2026-07-13).** QA-driven
  consolidation into one chart-anchored funnel: WS-0 enrichment survives
  fill re-import via a stable `trade_key` (#66, PR #70); WS-B debrief is now a
  tab in the cockpit's right column, `/debriefs/new/:id` redirects to
  `/trades/:id?debrief=1` (#63/#62, PR #72); WS-C day surfaces merged into
  `/journal/:date` with stat row + prompts + expandable mini card charts,
  retiring `/daily-review` + the TradeDetail drawer (#67, PR #73; date bugs
  #60/#61, PR #71). Follow-ups: prompt persistence (#68), stop==exit −1R UI
  flag, `_recompute_r_multiple` dedupe. Design decisions recorded on the epic.

### M8 — knowledge base & Lesson Reader (PRD-M8)
Ingest the user's own lesson transcripts (supplied locally, never committed) into TradeDesk-owned storage, make the
curriculum readable + searchable, and (M8c) generate LLM study sheets + a concept
index. Retrieval works with zero API keys (local embeddings, `Embedder` seam);
generation uses the `LLMProvider` seam and degrades cleanly under
`TRADEDESK_LLM=none`. Full spec: `docs/prd/PRD-M8-KNOWLEDGE.md`.

- **M8a — ingestion + search (landed, #47).** `make knowledge-ingest` → `knowledge_lessons`
  (SQLite source of truth) + rebuildable vector index under `data/knowledge_chroma/`;
  `GET /api/knowledge/{status,search}`; minimal Knowledge → Search UI. See `DEMO-M8a`.
- **M8b — Lesson Reader (landed, #48).** `GET /api/knowledge/modules` (curriculum
  tree + read status), `GET /api/knowledge/lessons/{id}` (metadata + raw transcript +
  chunk anchors + optional original-lesson link-out (`TRADEDESK_LESSON_URL_TEMPLATE`) + prev/next; `sheet`/`concepts` stubbed for
  M8c), `PUT /api/knowledge/lessons/{id}/progress` (`knowledge_lesson_progress` table,
  unread/read/starred). Knowledge → Modules tree + Lesson Reader UI (R9/R10-minus-
  sheets/R13). All endpoints degrade to a clean "not ingested" state. See `DEMO-M8b`.
- **M8c — LLM passes (code landed, #54 / PRs #55/#56).** Concept extraction +
  study-sheet generation (R6–R8), Concept pages (R11), ⌘K quick-search (R12).
  Concept/sheet passes over the full corpus are the only
  thing left; tracked as an execution run in #41.

### M9 — Coach Agents (PRD-M9)
Purpose-built coaching agents joining M7 trade context with M8 curriculum
knowledge, all through the `LLMProvider`/`generate_json` seam (degrades cleanly
under `TRADEDESK_LLM=none`). Read-only, process-scored (never P&L). Full spec:
`docs/prd/PRD-M9-COACH-AGENTS.md`.

- **M9a — shared agent infra + Trade Critic (landed 2026-07-12, #58).** Shared
  infra (R1–R5): `POST /api/knowledge/retrieve` (search + concept expansion),
  `agent_sessions`/`agent_messages` (schema v6), `GET /api/agents/status`.
  Trade Critic (R6–R11): `POST /api/agents/critic` → per-phase verdicts + cited
  `pass|fail|unclear` rule-checks + chart markers + prescription (drill +
  readings); uncited claims demoted to "unverified"; anti-gambling enforced at
  schema + prompt + regression-test layers. New `backend/app/agents/` package.
  See `DEMO-M9a`.
- **M9b — Curriculum Tutor (planned, R12–R15).** M9c — Leak→Lesson Mapper
  (planned, R16–R18). The remaining ~186 lessons' M8c passes finish before M9b.

---

## 6. Definition of Done (every milestone)
- All acceptance criteria checked; `pytest` green; **qa-reviewer sign-off posted**.
- No lookahead leakage (explicit qa test passes).
- `docs/DEMO-Mx.md` with exact click-path to verify.

## 7. Tech stack (LOCKED)
**Backend:** Python 3.11+, FastAPI, SQLite (SQLAlchemy Core + tiny migration runner), pandas + pandas-ta.
**Frontend:** React + Vite + TypeScript + **Tailwind CSS + shadcn/ui** + lightweight-charts.
Dark, minimal, fast. shadcn/ui (Radix under the hood) + Tailwind give the clean TradeZella/TradingView look;
TanStack Table/Query for the journal grid + data fetching. `make dev` orchestrates backend + frontend.

_Decision rationale (Next.js rejected, React over Svelte): see DECISIONS #11–#12._
