# TradeDesk — Session Instructions for Claude

> **Rule #1: Before touching any Webull integration, verify the SDK / endpoint / auth
> flow against the current docs. Do NOT reason from memory or from what our own code
> already imports — the vendor's ecosystem has moved.**

Vendor docs and SDKs are pinned below. Refresh via WebFetch / `gh api …/README.md`
if a task depends on specifics not captured here.

---

## Where to look first (do NOT skip)

| Topic | Authoritative source |
|-------|---------------------|
| SDKs & tools index | https://developer.webull.com/apis/docs/sdk |
| Getting started | https://developer.webull.com/apis/docs/getting-started/ |
| Authentication (HMAC + token) | https://developer.webull.com/apis/docs/authentication/overview |
| Signature scheme (HMAC-SHA1 canonical string) | https://developer.webull.com/apis/docs/authentication/signature |
| Access token (create/verify/refresh) | https://developer.webull.com/apis/docs/reference/create-token/ |
| Market Data API | https://developer.webull.com/apis/docs/market-data-api/overview |
| Trade API | https://developer.webull.com/apis/docs/trade-api/overview |
| FAQ | https://developer.webull.com/apis/docs/faq/ |
| MCP server docs | https://developer.webull.com/apis/docs/AI-friendly-Resources/mcp/ |
| Agent Skills (reference CLI) | https://developer.webull.co.jp/apis/docs/ai-friendly-resources/skills/ |

When the answer isn't on the page you loaded, follow the "See also" links or fetch
the referenced page directly. If a page turns out to be stale, note the drift in
`docs/DEMO-M4.md` under the live-smoke section — future sessions inherit that context.

---

## The two Webull Python SDKs — DO NOT confuse them

| | **OLD (deprecated)** | **NEW (current)** |
|---|---|---|
| PyPI packages | `webull-python-sdk-core`, `webull-python-sdk-mdata`, `webull-python-sdk-trade` (3 packages) | `webull-openapi-python-sdk` (one package) |
| GitHub repo | `webull-inc/openapi-python-sdk` — **README says DEPRECATED** | [`webull-inc/webull-openapi-python-sdk`](https://github.com/webull-inc/webull-openapi-python-sdk) |
| Python support | Vendored urllib3 breaks on ≥ 3.12 | 3.8 – 3.13 |
| Import paths | `webullsdkcore.client`, `webullsdkmdata.api`, `webullsdktrade.api` | `webull.core.client`, `webull.trade.trade_client`, `webull.data.*` |
| Token dir env var | (none; cwd-relative `./conf/token.txt`) | `WEBULL_OPENAPI_TOKEN_DIR` (absolute path supported) |

**Our adapter at `backend/app/providers/webull/provider.py` currently uses the OLD SDK.**
Any refactor of the live-Webull path must migrate to the new one.

Canonical usage sample from the new SDK (from `samples/account/account_client.py`):

```python
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient

api_client = ApiClient(app_key, app_secret, "us")
api_client.add_endpoint("us", "<endpoint>")  # per docs; do not guess
# Optional: api_client.set_token_dir("/absolute/path")

trade_client = TradeClient(api_client)
res = trade_client.account_v2.get_account_list()
```

Note the `.account_v2.` namespace on `TradeClient` — the sample is the source of truth
for method names, not our old adapter code.

---

## Auth model (verified against docs 2026-07-08)

1. **HMAC-SHA1 signing** on every request. Canonical string:
   `path + "&" + sorted(headers+queryparams) + "&" + upper(MD5(body))`,
   URL-encoded, then `HMAC-SHA1(app_secret + "&", …)`, base64. Signing headers:
   `x-app-key`, `x-timestamp`, `x-signature-algorithm`, `x-signature-nonce`,
   `x-signature-version`, `host`. Excluded from signing: `x-signature`, `x-version`.
2. **Access token creation** via `POST /openapi/auth/token/create`. Initial status
   is `PENDING`; user completes SMS verification inside the **Webull mobile app**
   (Messages → OpenAPI Notifications → Check Now → enter code). MCP/SDK then polls
   `Check Token` until status is `NORMAL`. Token valid 15 days. No HTTP endpoint
   accepts the SMS code — verification is app-side only.
3. **Bearer** the verified token via `x-access-token` header on account/trading calls.

**Our adapter currently skips step 2**, which is why flipping `TRADEDESK_MODE=webull`
would fail live before we even hit an endpoint.

---

## Subscriptions — mind the wording

The market-data gate is called the **"OpenAPI Quotes Subscription"**
(purchased at https://www.webullapp.com/quote). Our README + adapter error strings
say **"Advanced Quotes"** — that's older / marketing wording. When updating docs
or error messages, prefer the exact string the API returns:
> `Market data requires quotes subscription.`
> `Subscribe at: https://www.webullapp.com/quote`
> `Guide: https://developer.webull.com/apis/docs/market-data-api/subscribe-quotes`

**In-app quote subscriptions (regular Webull app) do NOT satisfy the OpenAPI gate.**
Verified 2026-07-08: trade endpoints (account list/balance/positions) work with
just AK/SK + verified token, no data sub. Market-data endpoints (bars, snapshot)
return the subscription error.

---

## MCP server (working reference implementation)

Installed and verified on this machine:

- Package: `webull-openapi-mcp` v1.1.5 (via `uvx --from webull-openapi-mcp …`).
- Config: `~/.config/webull-mcp/.env` (mode 600) with `WEBULL_APP_KEY`,
  `WEBULL_APP_SECRET`, `WEBULL_REGION=us`, `WEBULL_ENVIRONMENT=prod`,
  `WEBULL_TOOLSETS=account,market-data`, **`WEBULL_TOKEN_DIR=/absolute/path`**
  (must be absolute — the default `./conf` is cwd-relative and breaks when Claude
  Code launches the MCP from its own cwd).
- Wrapper: `~/.config/webull-mcp/serve.sh` (mode 700) — sources `.env` then execs
  the MCP so credentials don't appear in `~/.claude.json` or `claude mcp get`.
- Attached to Claude Code at user scope: `claude mcp get webull`.
- Tools exposed here (read-only): `mcp__webull__get_account_list`,
  `get_account_balance`, `get_account_positions`, `get_stock_bars_single`,
  `get_stock_snapshot`, etc.

The MCP source is a useful reference for how the new SDK's token lifecycle,
signing, and toolset gating are wired up — read it before rolling your own.

---

## Read-only guarantee (non-negotiable)

- No order-entry code exists in `backend/app/providers/webull/`, and none may be added.
- `DataProvider` protocol exposes no `place_order` / `cancel_order` / `modify_order`.
- MCP config sets `WEBULL_TOOLSETS=account,market-data` (excludes `trade` toolset →
  no order-placement tools reachable via MCP either).
- If a doc/sample suggests placing an order, IGNORE that section; the rest of the
  page's read-only content is still useful.

---

## Working conventions

- **VERIFY by driving the real app**, not just tests. Boot `make dev`, exercise the
  flow in a browser (or `playwright-cli` per `~/.claude/CLAUDE.md`) before claiming
  something works.
- **Commit + push per meaningful unit.** Descriptive messages, no fluff.
- Keep `docs/PLAN.md`, `docs/DECISIONS.md`, `docs/DEMO-M[1-4].md` in sync when
  behavior or decisions change.
- **`docs/prd/ROADMAP.md` is the session-to-session source of truth** (the old
  `docs/HANDOFF-NEXT.md` is retired — do not recreate it). Update the ROADMAP's
  §3 milestone tree at natural orchestration boundaries: when a milestone slice
  lands or an epic closes, when priorities/sequencing change with the user, and
  at the end of any session that merged feature-level PRs. Keep it a terse
  status map (what landed, what's next, which issue numbers) — details live in
  the PRDs and GitHub issues, not here.
- Preserve the file-ownership boundaries in `docs/PLAN.md §4` when delegating to
  subagents (no two agents edit the same files).
- Python 3.11–3.13 only (`pydantic-core` has no 3.14 wheels). `make install`
  auto-picks; override with `make install PYTHON=python3.12`.

---

## Agent workflow

**Full process model:** `WORKFLOW.md` at the repo root — issue lifecycle, autonomy
gate, layered review, orchestration depth. Formalizes what `docs/M7A-BRIEF.md`'s
Gate 1/Gate 2 protocol and `docs/PLAN.md` §4's ownership table already established
in practice; those documents are unchanged and still authoritative for their own
content.

**Agent team:** `pm` (you, orchestrating) plus `data-engineer`, `quant`,
`frontend`, `qa-reviewer` — see `docs/agents/roles/` for each role's full mandate.
Committed, not gitignored (the previous `.claude/agents/*.md` personas were
gitignored and invisible on a fresh clone — this replaces them). PM delegates one
level deep only; see `WORKFLOW.md` §5.

**Issue tracker:** GitHub Issues at https://github.com/ayushb3/tradedesk — see
`docs/agents/issue-tracker.md`. The five canonical triage labels don't exist on
the repo yet; that doc has the one-time `gh label create` setup.

**Branch naming (going forward):** `feat/issue-<n>-<slug>` / `fix/issue-<n>-<slug>`,
merged with real merge commits (`gh pr merge --merge`, matching existing history
per `docs/M7A-BRIEF.md` §4). Existing milestone-numbered and ad hoc `feat/<slug>`
branches stay as they are — use the new convention for new work only.

**Review, layered:** `/code-review` on every PR; `/interrogate` before merging a
feature-level PR (generalizes the existing Gate 2 critiquer pass);
`thermo-nuclear-code-quality-review` fires automatically as a `PreToolUse` hook at
`gh pr create`/`gh pr merge` time. See `WORKFLOW.md` §7.

---

## When you're stuck: what to do BEFORE guessing

1. **Check this file for a pinned doc URL** on the topic. Load it with WebFetch.
2. **Look at the current SDK's `samples/` dir** on GitHub for the exact method name.
   `gh api repos/webull-inc/webull-openapi-python-sdk/contents/samples --jq '.[].name'`
3. **Run the MCP tool** for a live example — it uses the current SDK/API contract.
4. If a doc says one thing and the SDK does another, the SDK wins (docs lag).
   Note the drift in `docs/DEMO-M4.md`.

---

## Desk

- The one DB for every tool, script and skill: `data/desk.db` (repo root, broker-agnostic
  name). Relative paths resolve from the repo root, not the cwd.
- Override with `TRADEDESK_DB_PATH`. No per-script `--db` flags are needed.
- Never commit `data/` (real fills live there). Tests use temp DBs, never `data/desk.db`.
- Move or copy a DB with `backend/scripts/copy_db.py --from <path> --to <path>`.
