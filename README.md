# TradeDesk

A local-only desktop web app with two modules that share one SQLite database and analytics engine:

- **Trade Journal** (TradeZella-style) — auto-imports filled orders, groups fills into round-trip trades, and gives you a P&L calendar, win rate, profit factor, avg R, hold time, and breakdowns by ticker / time-of-day / setup tag. Enrich each trade with tags, notes, a rule checklist, screenshots, and an A/B/C grade.
- **Bar-Replay Backtester** (TradingView-style) — replay 1-minute candles bar-by-bar with play/pause/step/speed, all key indicators and levels drawn with **no lookahead leakage**, and mark simulated trades that flow into the same journal analytics.

**Principles**
- 🔒 **Local-only by default.** Runs on localhost, single user. Data never leaves your machine unless you explicitly opt in to an LLM backend for the M6 debrief interview (see [Debrief LLM](#debrief-llm-m6-opt-in--default-off) below).
- 👁️ **Read-only broker access.** The Webull integration reads account / orders / positions / market data only. **No live order placement — ever.** There is no order-entry code anywhere in the app, by design.
- 🧪 **Mock-first.** Runs end-to-end against deterministic synthetic data with zero credentials. Live Webull is a `.env` toggle.

---

## Quick start (mock mode — no credentials needed)

**Prerequisites:** Python **3.11–3.13** (not 3.14 — `pydantic-core` has no 3.14 wheels yet) and Node 18+. `make install` auto-selects a compatible Python; override with `make install PYTHON=python3.12`.

```bash
make install     # backend venv + frontend node_modules (one time)
make dev         # backend :9000 + frontend :5173
```
Open **http://localhost:5173**, go to **Journal → Import fills**, or **Replay** to backtest. That's it — everything works against synthetic data out of the box.

```bash
make test        # backend pytest suite
```

### Access from your phone (Cloudflare tunnel)

To open the app on a phone (or any device off your machine) — handy for verifying
changes while iterating — expose the frontend with a Cloudflare tunnel. No account,
router, or firewall config needed.

```bash
brew install cloudflared   # one time
make dev                   # terminal 1 — backend + frontend as usual
make tunnel                # terminal 2 — prints https://<sub>.trycloudflare.com
```

Open the printed URL on the phone. The `/api` calls proxy through Vite, so the backend
stays same-origin — nothing else to configure. Hot reload still works: Claude's edits to
frontend/backend code appear live (uvicorn `--reload` + Vite HMR).

Notes:
- **Live HMR on the phone** needs Vite to know the public host. Restart dev as
  `TUNNEL_HOST=<sub>.trycloudflare.com make dev` (keep the tunnel running). Without it the
  app still works — you just refresh manually after edits.
- The `trycloudflare.com` URL **rotates each `make tunnel` run** — fine for testing, not a
  stable address. For a permanent URL, deploy the built frontend + backend properly.
- The tunnel is **unauthenticated** while running: anyone with the link can view your
  journal data. URLs are unguessable; don't leave it running unattended.

## Tech stack
- **Backend:** Python 3.11+, FastAPI, SQLite (SQLAlchemy Core), pandas/numpy. Indicators + levels are hand-rolled for exact causal (no-lookahead) control.
- **Frontend:** React + Vite + TypeScript + Tailwind + shadcn/ui + lightweight-charts + TanStack Query/Table.

## Architecture
```
frontend/ (React SPA)  ──HTTP/JSON──▶  backend/ (FastAPI)  ──▶  SQLite + data/screenshots/
                                        │
     providers/ (DataProvider seam) ────┤  mock (synthetic) | webull (read-only, live)
     indicators/ · analytics/ · ingest/ · db/
```
The `DataProvider` interface (`backend/app/providers/base.py`) is the only seam that touches a data source — swap mock ↔ Webull ↔ any future provider (Polygon, yfinance) without changing the rest of the app. See `docs/PLAN.md` for the full design and `docs/DECISIONS.md` for the decision log.

---

## Live Webull mode (M5)

> Requires a Webull brokerage account enrolled in the Webull **OpenAPI** program. Market data for US stocks additionally needs the **OpenAPI Quotes Subscription** — a separate purchase from any in-app Webull quote subscription. Trade and account endpoints (fills, positions, balances) do **not** require the data subscription.

**Read-only guarantee:** no order-entry code exists anywhere in the app. The `DataProvider` protocol exposes no `place_order`/`cancel_order`/`modify_order` methods, and the backend has none. The live integration reads account data only.

### 1. Get OpenAPI credentials
1. Sign in to the [Webull OpenAPI developer portal](https://developer.webull.com/) with your Webull account.
2. Apply for OpenAPI access and create an application to obtain an **`app_key`** and **`app_secret`**.
3. For market data (candles, snapshots): purchase the **OpenAPI Quotes Subscription** at https://www.webullapp.com/quote. This is separate from any in-app Webull quote subscription — existing in-app subscriptions do not satisfy the OpenAPI gate. Without it, market-data calls return a graceful error (see below); account/journal-import calls work regardless.

### 2. Install the Webull SDK
The SDK is included in `requirements.txt` and installed by `make install`. To install manually:
```bash
cd backend && . .venv/bin/activate
pip install webull-openapi-python-sdk
```
If the SDK isn't installed, mock mode is unaffected; live mode raises a clear "SDK not installed" error only when a live call is attempted.

### 3. Configure `.env`
```bash
cp .env.example .env
```
Edit `.env`:
```
TRADEDESK_MODE=webull
WEBULL_APP_KEY=your_app_key
WEBULL_APP_SECRET=your_app_secret
WEBULL_REGION=us
# Optional — override the token storage directory (must be an absolute path):
# WEBULL_OPENAPI_TOKEN_DIR=/absolute/path/to/token-dir
```
`.env` is gitignored — secrets never get committed or logged.

### 4. One-time token bootstrap (required before first run)
The Webull OpenAPI requires a verified access token in addition to AK/SK signing. Run the auth script once to obtain it:
```bash
python backend/scripts/webull_auth.py
```
The script calls `POST /openapi/auth/token/create`, then prompts you to open the **Webull mobile app → Messages → OpenAPI Notifications → Check Now → enter the SMS code**. It polls until the token status is `NORMAL` and writes the token to `data/webull-token/token.txt` (gitignored). The token is valid for ~15 days; re-run this script when it expires.

See [`backend/scripts/README.md`](backend/scripts/README.md) for full options (`--token-dir`, `--timeout`, exit codes).

### 5. Run
```bash
TRADEDESK_MODE=webull make dev
```
With a valid NORMAL token on disk, the app reads your real account fills, positions, and balances through the same interface. Journal analytics and replay work identically to mock mode.

### Graceful errors
- **Missing `app_key`/`app_secret`** → clear config error pointing at `.env`.
- **Token missing / PENDING / EXPIRED (HTTP 428)** → actionable message pointing at `python backend/scripts/webull_auth.py`.
- **No OpenAPI Quotes Subscription (HTTP 403)** → `"Market data requires the OpenAPI Quotes Subscription (buy at https://www.webullapp.com/quote). This is separate from any in-app Webull quote subscription."` — not a crash; account/journal-import calls are unaffected.
- **SDK not installed** → install instructions, only when a live call is made.

To switch back to synthetic data at any time, set `TRADEDESK_MODE=mock`.

---

## Knowledge base (M8, optional)

A local knowledge base built from **your own course notes / lesson files** (plain `.txt` transcripts that you supply; none ship with this repo). It gives you a module tree, a lesson reader, semantic search, a concept index and optional LLM study sheets, all on localhost.

```bash
export TRADEDESK_LESSONS_DIR=/path/to/lessons     # NNN_Title.txt per lesson
export TRADEDESK_MODULES_DIR=/path/to/modules     # optional; Module_*.txt grouping files
make knowledge-ingest                             # idempotent; add ARGS="--force" to rebuild
make knowledge-concepts && make knowledge-sheets  # optional LLM passes (need TRADEDESK_LLM)
```

| Variable | Purpose |
|---|---|
| `TRADEDESK_LESSONS_DIR` | Directory of per-lesson transcripts (no default) |
| `TRADEDESK_MODULES_DIR` | Module grouping files (default: `modules/` next to the lessons dir) |
| `TRADEDESK_SEED_CONCEPTS` | Path to your own concept-vocabulary JSON (default: the small generic `seed_concepts.example.json`) |
| `TRADEDESK_LESSON_URL_TEMPLATE` | Optional "open original lesson" link, e.g. `https://example.com/l/{lesson_id}` (empty = no link) |
| `TRADEDESK_LEGACY_TABLE_PREFIX` | One-time upgrade aid for old local DBs, see `backend/scripts/README.md` |

The app is fully usable if you never ingest anything.

---

## Debrief LLM (M6, opt-in — default off)

The M6 trade-debrief interview (a structured setup → plan → feelings → verdict reflection on a completed trade) runs entirely offline by default. If you want Claude to conduct the interview conversationally instead of walking a fixed form, opt in via `TRADEDESK_LLM`:

| Mode | Data leaves machine? | Credentials | Billing |
|---|---|---|---|
| `none` (default) | No | None | Free |
| `claude-cli` | Trade + candle summary + your replies (see below) | Existing Claude Pro/Max OAuth in `~/.config/claude` | Draws from your plan's monthly Agent SDK credit pool |
| `api` | Same | `ANTHROPIC_API_KEY` in `.env` | Pay-per-token, developer billing |

**When you opt in, this leaves your machine:**
- The trade being debriefed: symbol, direction, entry/exit prices, stop, R, fees, net P&L, session date, tags, any enrichment notes you've written.
- ~40 candles around the trade (OHLCV bars, one symbol).
- Your typed replies in the interview.

**This never leaves your machine:**
- Any secret (`WEBULL_APP_KEY`, `ANTHROPIC_API_KEY`, OAuth tokens).
- Any trade other than the one you're actively debriefing.
- Your full journal, P&L calendar, or aggregate stats.

The same 4-step interview runs on all three backends — swapping `TRADEDESK_LLM` only changes the conversational feel, not the artifacts. If you enable `claude-cli` and later change your mind, flip back to `none` and the offline form runs the same arc.

See `docs/PLAN-M6-TRAINING.md` §2 for the interview protocol and the `LLMProvider` seam design.

---

## Project docs
- `docs/PLAN.md` — architecture, data model, API surface, module boundaries, milestone task list.
- `docs/DECISIONS.md` — open questions + resolved decisions.
- `docs/DEMO-M1.md … DEMO-M4.md` — click-path to verify each milestone.
