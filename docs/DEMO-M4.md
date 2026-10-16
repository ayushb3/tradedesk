# DEMO — M4: Live Webull mode

**Goal:** a real read-only Webull OpenAPI adapter behind the provider interface, toggled by `.env`, with graceful errors for missing SDK / credentials / token / OpenAPI Quotes Subscription.

> The **live** path needs your Webull OpenAPI `app_key`/`app_secret`, a one-time SMS-verified token (see step 4 below), and — for market-data calls only — the **OpenAPI Quotes Subscription**. Everything up to that credential wall is built and verified; the live smoke test is the only step that requires your keys.

## Verify without credentials (what's fully testable now)
```bash
make test        # 111 tests incl. backend/tests/test_webull.py (mappers, error paths, token-lifecycle)
```
Mode toggle + graceful degradation:
```bash
cd backend && . .venv/bin/activate && python -c "
from app.providers.base import get_provider
from app.config import Settings
print(type(get_provider(Settings(mode='mock'))).__name__)                       # MockProvider
try: get_provider(Settings(mode='webull'))
except Exception as e: print(type(e).__name__)                                   # WebullConfigError
"
```

## Go live (requires your keys)
1. Install the SDK (already in `requirements.txt`; `make install` handles this):
   ```bash
   pip install webull-openapi-python-sdk
   ```
2. `cp .env.example .env` and set:
   ```
   TRADEDESK_MODE=webull
   WEBULL_APP_KEY=...
   WEBULL_APP_SECRET=...
   WEBULL_REGION=us
   # WEBULL_OPENAPI_TOKEN_DIR=/absolute/path   # optional override
   ```
3. Run the one-time token bootstrap:
   ```bash
   python backend/scripts/webull_auth.py
   ```
   Open the **Webull mobile app → Messages → OpenAPI Notifications → Check Now → enter the SMS code**. The script polls until `NORMAL` and writes the token to `data/webull-token/token.txt`. Token is valid ~15 days. See [`backend/scripts/README.md`](../backend/scripts/README.md) for details.
4. Boot the app:
   ```bash
   TRADEDESK_MODE=webull make dev
   ```
   The Journal's **Import fills** and the Replay candles now come from your real account/market data through the identical interface. Analytics + replay behave the same as mock.

## Graceful failure modes (verified)
| Situation | HTTP | Result |
|-----------|------|--------|
| `mode=webull`, no `app_key`/`app_secret` | — | `WebullConfigError` naming the missing vars, pointing at `.env` |
| Live call, SDK not installed | — | `WebullSDKNotInstalled` with `pip install webull-openapi-python-sdk` instructions |
| Token missing / PENDING / EXPIRED | 428 | `WebullTokenPending` — actionable message: run `python backend/scripts/webull_auth.py` |
| Market-data call, no OpenAPI Quotes Subscription (403) | 403 | `WebullSubscriptionError` — "Market data requires the OpenAPI Quotes Subscription (buy at https://www.webullapp.com/quote). This is separate from any in-app Webull quote subscription." |
| Bad/expired credentials (401) | 401 | `WebullAuthError` |

## Read-only guarantee
The `DataProvider` interface exposes **no** order-entry methods, and none exist in `app/providers/webull/`. The Webull integration can only read account / orders / positions / market data.

## What's NOT done (the credential wall)
- A real end-to-end market-data call against live Webull — needs your keys + OpenAPI Quotes Subscription. The adapter, mappers, mode toggle, token lifecycle, error handling, tests, and setup docs are all in place; completing the one-time bootstrap is the only remaining step for account/journal-import. Market data additionally requires the data subscription (portal purchase, not a code step).

---

## Live smoke test — 2026-07-08 findings

Ran the Webull OpenAPI directly (via `webull-openapi-mcp` as a probe, not the TradeDesk backend — see "Adapter path forward" below). Verified account credentials + tokens work; identified the exact subscription gate.

**Setup used for the probe:**
- Installed `webull-openapi-mcp` (v1.1.5) via `uvx` — the official pip SDK (`webull-python-sdk-core/mdata/trade`) fails on Python 3.12 due to a vendored `urllib3` that expects the old `six.moves` layout, but `uvx`'s isolated build env resolves it cleanly.
- One-time `webull-openapi-mcp auth` completes the token creation + Webull-app SMS verification (`POST /openapi/auth/token/create` returns a PENDING token; verification happens inside the mobile app, MCP polls for NORMAL).
- Token persisted at `WEBULL_TOKEN_DIR=/Users/…/.config/webull-mcp/conf/token.txt` (default is `./conf/`, cwd-relative — pin it in `.env` or `serve` won't find the token).

**Results:**

| Call | Result |
|------|--------|
| `get_account_list` | ✅ Returns both linked accounts (INDIVIDUAL_CASH + INDIVIDUAL_MARGIN) with account_id + user_id |
| `get_account_balance` (margin) | ✅ Full breakdown: cash balance, net liquidation, day P&L, day-trading buying power, option BP. No fields blank. |
| `get_account_positions` | ✅ Returns `No data available` (no open positions today — endpoint works, just an empty portfolio) |
| `get_stock_bars_single AAPL M1 count=5` | ❌ `Market data requires quotes subscription.` → https://www.webullapp.com/quote |
| `get_stock_snapshot AAPL` | ❌ Same subscription error |

**Interpretation:**
- **Trade toolset works with just AK/SK + verified token.** Account, balance, positions all succeed. That means TradeDesk's journal-import path (which maps orders → fills → round-trips) is unblocked once we wire the adapter properly.
- **Market data is gated on the OpenAPI Quotes Subscription** — a separate purchase from in-app quotes. The error message and link are what our `WebullSubscriptionError` handles (the message wording was updated in M5 to match what the API actually returns).
- **Read-only enforcement is doing its job:** the MCP was launched with `WEBULL_TOOLSETS=account,market-data`, so order-placement tools (`place_order`, `cancel_order`) aren't even exposed. Belt-and-suspenders with the `DataProvider` seam not having any order methods.

## Adapter path forward

The TradeDesk backend adapter at `backend/app/providers/webull/provider.py` (at the time of this smoke test) still imported the broken pip SDK, so **flipping `TRADEDESK_MODE=webull` from `make dev` would fail with `WebullSDKNotInstalled`** on Python 3.12 (SDK installs but its vendored urllib3 errors on import). A phased migration plan was written up in `docs/PLAN-M5.md`.

**Resolution (M5 — landed 2026-07-09):** All four phases of `docs/PLAN-M5.md` are complete:
- `27e8173` (Phase A) — swapped deprecated `webull-python-sdk-{core,mdata,trade}` for `webull-openapi-python-sdk`; all tests still green.
- `c40f257` (Phase B) — added `backend/scripts/webull_auth.py` (create-token → SMS-verify → NORMAL poll); token persists at `data/webull-token/`.
- `1406ca0` (Phase C) — wired `WebullProvider` to read the token from disk; added `WebullTokenPending → HTTP 428` for missing/PENDING/EXPIRED token.
- `f02d81a` (Phase D backend) — updated `WebullSubscriptionError` wording to match exact API response ("OpenAPI Quotes Subscription").
- This commit (Phase D docs) — README, DEMO-M4, PLAN updated to reflect current adapter shape.

See `docs/PLAN-M5.md` for the full migration plan and acceptance criteria.

---

## API drift notes

**Subscription gate returns HTTP 401, not 403 (observed 2026-07-09).**
The live US OpenAPI returns `HTTP 401` with body `"Insufficient permission, please subscribe to stock quotes."` when market-data endpoints are called without the OpenAPI Quotes Subscription. Docs imply 403, but the live API uses 401. The adapter now classifies by response body (not HTTP status): a 401 containing a subscription signal maps to `WebullSubscriptionError`; a 401 with no such signal (e.g. bad/expired token) still maps to `WebullAuthError`. 403 continues to map to `WebullSubscriptionError` unconditionally.

**SDK drift note (2026-07-09):** `get_history_bar` requires `start_time`/`end_time`
(epoch ms) to retrieve historical bars; passing only `count` returns the most-recent
~1200-1650 bars, so any requested date older than that window silently returns empty.

**Options market data (verified 2026-07-10, docs check for PRD-M7).**
Per the Market Data API Overview "Market Data Permissions" table:
- US options real-time quotes/trades require the **OPRA Real-Time Non-display
  subscription** — a *separate* product from the stock subscriptions
  (Level 1 = Nasdaq Basic Non-Display, Level 2 = Nasdaq TotalView Non-Display).
  As with stocks, in-app subscriptions do not apply to OpenAPI.
- **No historical option bars exist in the Data API at all.** The Data API's
  asset categories are only `US_STOCK` and `US_ETF` (futures/crypto have
  dedicated paths); the endpoint list has Stock/Futures/Crypto sections and no
  Options section. Options data via OpenAPI appears to be real-time only.
  → Historical option premium charts (PRD-M7) cannot come from Webull under
  any subscription; use the synthetic fallback or a third-party provider.

  **CORRECTION (verified 2026-07-13, PRD-M7 live testing):** the above is now
  stale — Webull's changelog dated **2026-05-16** added option market-data
  endpoints under the "Non-Display Solution": **Option Tick, Option Snapshot,
  and Option Historical Bars** (`GET /openapi/market-data/option/bars`, batch
  query, M1/M5 granularities — the option contract's OWN OHLC, so it works for
  index roots like SPXW directly). The endpoint is documented and live:
  https://developer.webull.com/apis/docs/reference/option-historical-bars/
  **BUT** access needs a *paid option market-data subscription* that Webull's
  Option Market Data page still describes as "under active development and will
  be released soon" — so calling it today likely returns an entitlement error
  even though the route exists. This is an example of exactly the drift
  CLAUDE.md Rule #1 warns about (we missed a changelog that predated our own
  "verified 2026-07-10" note). When the option-data subscription becomes
  purchasable, the `get_option_bars` provider seam can call `option/bars`
  directly and retire the synthetic fallback to a degraded path.

  **Third-party alternatives (researched 2026-07-13)** if we want real option
  bars before Webull's sub ships, or for index options specifically:
  ThetaData (options-native, $40/mo Value = real-time + 4yr 1-min history),
  Polygon (simplest REST, $29/mo Starter 15-min-delayed 2yr / $79 Developer),
  Databento (usage-based, ~tens of $ total for on-demand backfills + $125 free
  credits — best fit for our fetch-on-trade-open access pattern). All three
  cover index options (SPXW/NDX/RUT) with the contract's real premium, unlike
  the BS synthetic which needs a fetchable equity underlying (index roots have
  none — see PRs #75/#76). Dominant architecture in comparable apps: Webull for
  trading/fills, a dedicated data vendor for historical option bars.
- Wording drift: the subscription page is again titled "Subscribe Advanced
  Quotes" / "OpenAPI Advanced Market Data Subscription Service" (CLAUDE.md
  previously flagged "Advanced Quotes" as older wording — the docs have moved
  back to it). Subscription is purchased via the Webull Technology site →
  avatar → Advanced Quotes → OpenAPI Advanced Quotes; no prices listed on the
  docs page itself.
Fixed in `fix/webull-bars-time-window`.
