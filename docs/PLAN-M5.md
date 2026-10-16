# TradeDesk — M5 Plan: Migrate Webull adapter to the current SDK + real token flow

**Status:** Ready to implement in a fresh context. Not started.
**Owner:** whoever picks this up next (a fresh Claude session, or the `dev1` subagent).
**Depends on:** M4 landing (done), `CLAUDE.md` (read this first — it pins the correct docs and SDK).
**Rollback:** every phase leaves M1–M3 mock-mode flows untouched. Reverting the last phase's commits removes the migration without touching journal / replay / analytics.

---

## 1. Why this exists

The M4 live smoke test on 2026-07-08 revealed two facts that invalidate the current live path:

1. **Wrong SDK.** `backend/app/providers/webull/provider.py` imports the deprecated `webull-python-sdk-{core,mdata,trade}` (v0.1.18). The current SDK is `webull-openapi-python-sdk` (v1.0.x, one package, Python 3.8–3.13 supported). The old SDK's vendored `urllib3` crashes on Python 3.12+.
2. **Missing auth step.** Our adapter assumes `AK/SK -> ApiClient` is enough. It isn't. The real flow is `AK/SK -> POST /openapi/auth/token/create -> PENDING token -> user completes SMS verification in the Webull mobile app -> poll for NORMAL -> bearer via x-access-token header`. Verified via `webull-openapi-mcp`.

We also documented that the market-data subscription is called **"OpenAPI Quotes Subscription"** (not "Advanced Quotes" as our code says), purchased at https://www.webullapp.com/quote — separate from in-app quotes.

See `docs/DEMO-M4.md § "Live smoke test — 2026-07-08 findings"` for raw evidence.

## 2. Non-goals

- **No order-entry code, ever.** M4's constraint stands: read-only. `DataProvider` protocol stays free of `place_order` / `cancel_order` / `modify_order`. The MCP `WEBULL_TOOLSETS=account,market-data` gate is the reference boundary.
- **No purchase of the OpenAPI Quotes Subscription** as part of this milestone. Market-data endpoints stay stubbed with a clear typed error until the user buys the sub separately. Everything ELSE (accounts, positions, orders → fills → round-trips → journal) must work end-to-end after this milestone.
- **No changes to mappers.py.** They're already SDK-agnostic (accept camelCase/snake_case, str/num, fixture-tested). If a mapper needs to change, treat it as a scope check — surface to PM.
- **No changes to mock mode.** `make dev` with `TRADEDESK_MODE=mock` behaves identically before and after.
- **No new frontend work.** UI already handles both mock and live via the same API surface.

## 3. Scope — the exact surface that changes

### Files that will change

| File | Change |
|------|--------|
| `backend/app/providers/webull/provider.py` | Rewrite: new SDK imports, add token bootstrap, keep public methods signature-identical |
| `backend/app/providers/webull/errors.py` | Add `WebullTokenPending` error type; update `WebullSubscriptionError` docstring wording |
| `backend/app/providers/webull/__init__.py` | Re-export the new error type |
| `backend/app/config.py` | Add `webull_token_dir: str = ""` setting, plumb `WEBULL_OPENAPI_TOKEN_DIR` env var |
| `backend/requirements.txt` | Remove `webull-python-sdk-*`; add `webull-openapi-python-sdk` |
| `backend/tests/test_webull.py` | Update fake-SDK monkeypatches to the new SDK's import paths; add token-pending fixture |
| `README.md` | Update install line + wording ("OpenAPI Quotes Subscription", not "Advanced Quotes") + document the one-time `webull-auth` step |
| `docs/DEMO-M4.md` | Update to reflect the new adapter path; keep the smoke-test findings section |
| `docs/PLAN.md` | Mark M5 as landed; short retro note |
| `.env.example` | Add `WEBULL_OPENAPI_TOKEN_DIR=` line with a comment pointing at an absolute path |

### Files that must NOT change

- `backend/app/providers/webull/mappers.py` — the SDK-agnostic seam
- `backend/app/models/*.py` — domain models are frozen contract v1
- `backend/app/providers/base.py` — `DataProvider` protocol is frozen
- Anything under `backend/app/{indicators,analytics,ingest,db,api}/` — unrelated modules
- `frontend/**` — no UI change

### Public API (behavior) that must NOT change

- `MockProvider` behavior — every existing test must still pass unchanged.
- `WebullProvider` method signatures: `get_candles`, `get_candles_range`, `get_fills`, `get_positions`, `get_account`, all matching `DataProvider`.
- Error-to-HTTP mapping in the API layer.

## 4. Contract for the new adapter

### 4.1 SDK import surface (from `webull-inc/webull-openapi-python-sdk` samples/)

```python
from webull.core.client import ApiClient           # (was: webullsdkcore.client.ApiClient)
from webull.trade.trade_client import TradeClient  # (was: webullsdktrade.api.API)
from webull.data.mdata_client import MDataClient   # (was: webullsdkmdata.api.API; confirm exact name)

api_client = ApiClient(app_key, app_secret, region_id)  # region "us" | "hk" | "jp"
# Optional but recommended (see § 4.3):
api_client.set_token_dir("/absolute/path")               # or set WEBULL_OPENAPI_TOKEN_DIR

trade = TradeClient(api_client)
mdata = MDataClient(api_client)

# Method surface as seen in samples/account/account_client.py:
res = trade.account_v2.get_account_list()                # returns response w/ .status_code + .json()
```

**Before writing code, confirm the exact class/module for market-data (`webull.data.*`)**: read `samples/data/*` in the repo (`gh api repos/webull-inc/webull-openapi-python-sdk/contents/samples --jq '.[].name'`, then inspect the market-data-shaped sample). Do NOT guess — the old SDK used `webullsdkmdata`, the new one may use `webull.data` or `webull.market_data`. This is the single most likely place to guess wrong.

### 4.2 Endpoint mapping (verified against the docs and the MCP live smoke)

| Our method | New-SDK call (best current guess — confirm in samples/) | Notes |
|-----------|---------------------------------------------------------|-------|
| `get_account()` | `trade.account_v2.get_account_balance(account_id)` + `get_account_positions` | Needs account_id from list first |
| `get_positions()` | `trade.account_v2.get_account_positions(account_id)` | Verified live — returns "No data available" for empty |
| `get_fills(since)` | `trade.order_v2.get_order_history(account_id, filters)` | Filter status=FILLED; TODO confirm exact filter names in sample |
| `get_candles(symbol, day, tf)` | `mdata.stock.get_bars(symbol, US_STOCK, timeframe, count)` | 403s without OpenAPI Quotes Sub — raise `WebullSubscriptionError` |
| `get_candles_range(...)` | Same, with a wider count and post-filter | Same subscription gate |

For each row: verify in the SDK's `samples/` before writing the call. If a sample doesn't exist for the exact endpoint, fall back to the MCP source (`webull-openapi-mcp` uses this SDK — inspect via `uvx --from webull-openapi-mcp python -c "import webull_openapi_mcp; …"`, or `pip show -f webull-openapi-mcp`).

### 4.3 Token lifecycle (this is the piece we're actually adding)

The SDK handles HMAC signing per request; **token creation is a separate one-time step** the SDK does NOT automate for us. Options in priority order:

**Option A — Delegate to the SDK's CLI (`webull-openapi-mcp auth`-style flow).** Ship a tiny script `backend/scripts/webull_auth.py` that:
1. Reads AK/SK/region/token_dir from `Settings`.
2. Calls the SDK's token-create helper (name TBD; probably `api_client.create_token()` or `api_client.auth.create_token()` — inspect samples).
3. Prints "PENDING → please open the Webull mobile app → Messages → OpenAPI Notifications → verify → we'll poll for NORMAL".
4. Polls the SDK's status endpoint until NORMAL (or timeout after 5 min, exit with a clear error).
5. Persists the token via `api_client.set_token_dir(...)` semantics (writes `<token_dir>/token.txt`).
6. Exits 0. `WebullProvider` never runs this flow — it assumes a valid token is on disk.

The provider's job at runtime: **detect a missing/PENDING/EXPIRED token and raise `WebullTokenPending` with an actionable message** pointing at `python backend/scripts/webull_auth.py`.

**Option B — Programmatic in-provider bootstrap.** Provider automatically runs token-create on first call, blocks up to 5 min waiting for user verification. Rejected: blocks the FastAPI worker; user has no way to see the SMS prompt from within `make dev`.

Pick A. Simple, testable, matches the MCP's separate `auth`/`serve` model.

### 4.4 Error types (updated)

| Error | Raised when | Actionable message |
|-------|-------------|--------------------|
| `WebullConfigError` | AK/SK missing | (unchanged) |
| `WebullSDKNotInstalled` | `import webull.core...` fails | Update install line: `pip install webull-openapi-python-sdk` |
| **NEW `WebullTokenPending`** | Token file missing or status is PENDING/EXPIRED/INVALID | "Run `python backend/scripts/webull_auth.py` and verify via the Webull mobile app (Messages → OpenAPI Notifications → Check Now → enter SMS code). Token dir: {token_dir}." |
| `WebullAuthError` | 401 from a signed call, or SDK reports auth failure | (unchanged wording) |
| `WebullSubscriptionError` | 403 on a market-data call | Update wording: "Market data requires the OpenAPI Quotes Subscription (buy at https://www.webullapp.com/quote). This is separate from any in-app Webull quote subscription." |

## 5. Phased delivery (each phase is a single commit; each is independently reversible)

### Phase A — SDK swap + tests still green (mock-only, non-behavioral)
Goal: no behavior change; all tests still pass; import path is the new SDK.

- Rewrite `provider.py` imports to `from webull.core.client import ApiClient`, etc.
- Update the ImportError catch in `_clients()` to key on the new SDK name; new `WebullSDKNotInstalled` message.
- Update `test_webull.py` fake-SDK monkeypatch targets to the new module paths.
- `requirements.txt`: swap the package.
- `make install && make test` → 103 tests still pass.

**Definition of done:** every existing test still green; `pip show webull-openapi-python-sdk` succeeds in the venv; no `webullsdk*` string remains anywhere in `backend/`.

**Rollback:** `git revert <phase-A>`.

### Phase B — Token lifecycle + auth script
Goal: `python backend/scripts/webull_auth.py` runs the create-token → SMS-verify → NORMAL flow and persists the token.

- New file `backend/scripts/webull_auth.py` (CLI, prints instructions, blocks with a timeout, uses the SDK's token helper).
- New file `backend/scripts/README.md` (one-pager: what to run, when, expected wall-clock time).
- New setting `Settings.webull_token_dir`; `WEBULL_OPENAPI_TOKEN_DIR` env var; default to `<repo>/data/webull-token/` (absolute path derived at runtime, gitignored).
- `.gitignore`: add `data/webull-token/`.
- `.env.example`: add the new var with a comment.
- New unit test with a faked SDK token-create/check pair (PENDING → NORMAL transition) to exercise the polling loop.

**Definition of done:** running the script against the real Webull backend produces a valid token file and prints "Token status: NORMAL". Unit test for the fake-token-happy-path passes.

**Rollback:** `git revert <phase-B>` → script file gone; provider still works in Phase A state.

### Phase C — Wire provider to actually use the token
Goal: `WebullProvider.get_account_list()` returns real data end-to-end.

- Provider constructor: read `webull_token_dir`, call `api_client.set_token_dir(...)`.
- New method `_ensure_token()`: check status via the SDK; if not NORMAL, raise `WebullTokenPending` with the run-this-script message.
- `_clients()` calls `_ensure_token()` before returning cached clients.
- API layer: map `WebullTokenPending` to HTTP 428 Precondition Required with the actionable message body.

**Live verification:** boot `make dev` with `TRADEDESK_MODE=webull`; hit `POST /api/import/fills`; expect either (a) real fills from your Webull account, or (b) a 428 with the auth-script pointer if the token expired.

**Definition of done:** journal-import path pulls real fills (or an empty list, if your account had no orders in the last 7 days). Tests for the token-pending path pass.

**Rollback:** `git revert <phase-C>` → provider back to Phase B state (SDK swapped, script exists, but wiring is incomplete → same failure mode as before this milestone).

### Phase D — Market-data subscription gate wording + docs
Goal: cleanup pass; no runtime behavior change beyond error message wording.

- `errors.py`: `WebullSubscriptionError` message updated (new wording + link).
- `provider.py`: candle methods now raise `WebullSubscriptionError` with the new message; keep the 403 → `WebullSubscriptionError` translator.
- `README.md`: rewrite "Live Webull mode" section end-to-end. Include the one-time `webull_auth.py` step, correct subscription wording, correct install line.
- `docs/DEMO-M4.md`: revise to describe the current adapter shape; keep the "smoke test — 2026-07-08 findings" section as historical record.
- `docs/PLAN.md`: mark M5 as landed.

**Definition of done:** README + DEMO-M4 accurately describe what the user must do to run live mode. No "Advanced Quotes" string remains in `backend/` or `docs/` (grep-verified).

**Rollback:** `git revert <phase-D>` → docs go back to slightly-wrong wording but code is fine.

## 6. Subagent split (if you use them)

Phase A is safe to do solo — it's a mechanical rename + test-fixture update.

For Phases B–D, if delegating:

| Agent | Owns (writes) | Reads (does not edit) |
|-------|---------------|-----------------------|
| **dev1** (backend) | `backend/app/providers/webull/*.py`, `backend/app/config.py`, `backend/scripts/*.py`, `backend/requirements.txt`, `backend/tests/test_webull.py` | `backend/app/models/*`, `backend/app/providers/base.py`, `backend/app/api/*` |
| **dev2** (docs/infra) | `README.md`, `docs/DEMO-M4.md`, `docs/PLAN.md`, `.env.example`, `.gitignore` | `backend/**` |
| **pm** (verify) | `docs/PLAN-M5.md` progress ticks | everything read-only |

**File-ownership rule (from PLAN.md §4): two agents never edit the same file.** If a phase needs both a code change and a docs change in the same PR, run them sequentially, not in parallel.

## 7. Acceptance criteria (whole milestone)

- [ ] `make install && make test` → all backend tests green, including new token-lifecycle tests.
- [ ] `python backend/scripts/webull_auth.py` completes successfully against a live Webull account (one-time; token persists).
- [ ] `TRADEDESK_MODE=webull make dev` boots without errors.
- [ ] `POST /api/import/fills` returns a valid response (real fills, or an empty list if no orders in the last N days). No stack traces.
- [ ] Hitting `/api/candles?symbol=AAPL&…` returns a well-formed 403-ish error (`WebullSubscriptionError` → HTTP 4xx) with the new "OpenAPI Quotes Subscription" wording, **not** a stack trace.
- [ ] `grep -r "Advanced Quotes" backend/ docs/` returns nothing.
- [ ] `grep -r "webullsdk" backend/` returns nothing.
- [ ] `git status` clean; `main` protected; PR is optional and separate from this milestone.

## 8. Risks & things a fresh context is likely to trip on

1. **New-SDK module path guessed wrong.** Mitigation: read `samples/` FIRST, before writing any import. Specifically confirm the market-data client name — the old SDK's `webullsdkmdata.api.API` may be `webull.data.mdata_client.MDataClient` in the new one, or something else entirely.
2. **Token-check API name unknown.** Mitigation: MCP source is the reference (`webull-openapi-mcp`'s `auth`/`serve` subcommands demonstrably work). If needed, `pip show -f webull-openapi-mcp` and inspect its Python entrypoint to see what it calls.
3. **Region routing.** The MCP defaulted to `Environment: uat` when we forgot to set `WEBULL_ENVIRONMENT=prod`. The SDK likely has an equivalent — likely `api_client.set_env("prod")` or an env var. Confirm before shipping.
4. **`set_token_dir` semantics.** Absolute path is safest; the SDK's default is cwd-relative `./conf/token.txt`, which broke us in the MCP wiring. Always resolve to absolute at load time.
5. **The `Response.status_code` / `Response.json()` pattern.** Every sample method returns a response object with `status_code` and `.json()`, not raw data. Adapters need to check status BEFORE calling `.json()` — a 401/403 body may not be JSON. Our current `_translate_status` handles this correctly; keep it.
6. **Tests that mock the SDK.** The current tests monkeypatch `webullsdkcore.*` etc. — those import paths won't exist after Phase A. Update monkeypatch targets in the same commit as the rename, or the test suite blows up loudly (which is fine; that's the signal you moved forward correctly).

## 9. What to read before starting (in order)

1. **`CLAUDE.md`** (repo root) — pins the correct SDK and doc URLs. Non-negotiable pre-read.
2. **`docs/DEMO-M4.md`** — smoke-test findings that motivated this plan.
3. **`docs/PLAN.md`** — file-ownership rules, non-negotiable constraints.
4. **`webull-inc/webull-openapi-python-sdk/samples/`** on GitHub — the SDK's canonical usage examples. `gh api repos/webull-inc/webull-openapi-python-sdk/contents/samples --jq '.[].name'` to list, then `gh api …/samples/<file>` and base64-decode `.content` to read.
5. **`~/.config/webull-mcp/serve.sh` + `.env`** — the working MCP setup (a reference for env-var names and token-dir handling).

Do NOT read the old SDK (`webull-inc/openapi-python-sdk`) — its README explicitly says DEPRECATED at the top.

## 10. Out of scope, but adjacent (candidates for a later milestone)

- **Real-time quote streaming (MQTT / gRPC).** The new SDK supports it; useful for the replay module's future "live" mode. Distinct feature, not required for M5.
- **Multi-account support.** We resolve to the first account today; a follow-up could let the user pick. Not blocking.
- **Rate limiting / caching.** Data API is 300 req / 60s. Once market data is unlocked, we'll want a per-symbol / per-day cache. Distinct from the migration.
- **Cross-region support.** Adapter is US-only. HK/JP/etc. work identically but need endpoint routing + region-specific settings. Deferred.

---

## Estimate

- Phase A: 60–90 min (mostly re-running tests after each import change)
- Phase B: 90–120 min (writing the auth script, blocking-poll loop, unit tests)
- Phase C: 45–75 min (wiring + live verification against a real account)
- Phase D: 30 min (docs pass)

**Total: ~4–5 hours of focused work.** Well-suited to a single fresh-context session.
