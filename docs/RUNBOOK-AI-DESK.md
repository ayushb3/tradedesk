# Runbook: running the desk and orchestrating a build

This is for a fresh Claude session that has to run TradeDesk locally or keep a multi-PR build going without asking the owner anything. Read it top to bottom once. The owner's private links and local specifics (the desk artifact, which real trade to browser-check, the design notes) live in the owner's vault runbook, not here.

## 1. Hard rules (public repo)

- **Read-only broker forever.**
  - No order entry and no broker client code.
  - The Alpaca client is market data only (`data.alpaca.markets`). Never use the trading API: `api.alpaca.markets`, `paper-api`, `/v2/orders`, `/v2/positions`, `/v2/account`, `alpaca_trade_api`, `alpaca.trading`.
- **The MCP server makes no network calls.** An AST test enforces this. In real mode, desk MCP reads use `bars_mode="cache"`; in mock mode they use `"mock"`.
- **Nothing real in the repo.** Code, tests, fixtures, comments, PR text and commit messages never contain:
  - real fills, P&L, trade ids or dates;
  - real traded contracts (OCC symbols, strikes, expiries);
  - personal rule values (cutoff time, daily stop, target, loss line);
  - account or order ids, or keys;
  - course content or names. Say "mentor", "lessons", "knowledge base".

  Fixtures are synthetic: made-up tickers, or neutral examples like `SPY 600C 2026-06-19`.
- `git grep -niE "kcu|somesh|thinkific"` must be empty.
- **No credentials.** Never handle Robinhood credentials; the owner logs in. Keep Alpaca keys only in the gitignored `.env`, and never read them into chat, logs or commits.
- **Display rules.**
  - Times shown to the user are in PT, labelled "PT".
  - P&L is shown loud but is never a score.
  - No "money left on the table" counters.
  - Don't edit `backend/tests/test_anti_gambling_audit.py`.
- **Replay never leaks the future** (the "decide first, then compare" rule). Nothing derived from the real trade's exit or from bars after the playhead may render, or be computable from a response, before the playhead reaches it:
  - fills are gated server-side;
  - `window_end_ts` is null until the playhead passes the exit;
  - the y-domain grows only from bars already played.

## 2. How the local app runs

| Piece | Where | How |
|---|---|---|
| Base clone | `C:\Users\ayush\Code\tradedesk-desk` (origin = this repo) | the running app's checkout; real `data/desk.db` + `data/rules.yaml` (gitignored) |
| Python | `C:/Users/ayush/Code/tradedesk-public/.venv/Scripts/python.exe` | shared venv, deps installed |
| Backend | `:9000` | from `backend/`: `<venv python> -m uvicorn app.main:app --port 9000` |
| Frontend | `:5173` | from `frontend/`: `npm run dev -- --host 127.0.0.1` |
| Browser | **`http://127.0.0.1:5173`**, not `localhost` | |
| Main checkout | `C:\Users\ayush\Code\TradeDesk` | the owner's own checkout with uncommitted work: **never touch it** |

**`.env` switches** (repo root, gitignored):
- `TRADEDESK_MODE=mock`: the Webull provider stays mocked. The real DB is still used.
- `TRADEDESK_MARKET_DATA=alpaca`: real bars, needs `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`. The default is `mock`.
- `TRADEDESK_LLM=none`.

**Restart the backend after any PR that adds a DB migration or changes a response model.** Migrations run at startup, and `uvicorn` is started without `--reload`. Vite hot-reloads the frontend on its own.

**Check out a PR for a browser test:**
```
git -C C:/Users/ayush/Code/tradedesk-desk fetch -q
git -C C:/Users/ayush/Code/tradedesk-desk checkout --detach origin/<branch>
```
When you're done, put it back on `main`: `git checkout main && git pull --ff-only`.

**Desk MCP server:** `make desk-mcp` (real) / `make desk-mcp-mock`, and `make desk-mcp-check` lists the tools. Registration in the Claude desktop app is in `backend/scripts/README.md`, under "Desk MCP server". Read tools have `readOnlyHint=True`; write tools need the owner's per-call approval.

## 3. The build loop (one slice = one issue = one PR)

1. **Issue.** Write a neutral issue body: no real values, labels `ai-desk` (+ `redesign` for the redesign epic).
2. **Worktree.** From the base clone:
   ```
   git worktree add ../td-<slug> -b feat/<slug> origin/main
   ```
   Link node_modules instead of running `npm ci`:
   ```powershell
   New-Item -ItemType Junction -Path ..\td-<slug>\frontend\node_modules -Target C:\Users\ayush\Code\tradedesk-desk\frontend\node_modules
   ```
3. **One Sonnet agent per slice.** Give it:
   - the slice brief: the issue, the worktree and branch, the files that already exist, exact scope and what's out of scope;
   - the hard rules;
   - **"ports 9000/5173 are the owner's running app; never kill or bind them; use 9100/5273 and stop only your own processes"**;
   - "do NOT merge".

   It opens a ready (not draft) PR.
4. **Review it yourself.** Read the diff: logic, the leak rule, one R definition. Then grep it:
   ```
   git diff origin/main | grep -nE "^\+.*(<real OCC roots/strikes/expiries>|account=)"
   git grep -niE "kcu|somesh|thinkific"
   ```
   Send it back to the agent if anything fails.
5. **Gate.** CI can't run (GitHub billing). Run locally:
   - backend: `TRADEDESK_MODE=mock <venv python> -m pytest -q -p no:cacheprovider` from `backend/`. It takes about 4 min, so run it in the background.
   - frontend: `npx tsc -b && npx vite build`, plus `npm run check:sim` once the sim exists.
6. **Browser check.** Check out the branch in the base clone, then restart the backend if needed (see section 2).
   - Open the real trade the owner uses for checks; the id is in the vault runbook.
   - Verify the slice's acceptance items. Step the replay with `ArrowRight` key events and read state from the DOM (see Gotchas).
7. **Merge** with a real merge commit, `gh pr merge <n> --merge`, but **only with the owner's OK**: either typed by them in this session, or quoted word for word by the session that started you. Close the issue, report to the parent session, and start the next slice from the new `main`.

Stack only when you have to: base the next branch on the open PR's branch, then retarget it to `main` with `gh pr edit <n> --base main` after the base merges.

## 4. Gotchas we hit (and the fix)

- **Real contracts leaked into a PR.** An agent used the owner's actual traded contracts as test examples.
  - The fix was to scrub them to neutral values *and squash the branch* before merging, so history is clean too.
  - Always grep the diff for real OCC roots, strikes and dates before merging.
- **Cache poisoning across providers.** Mock or Black-Scholes bars cached in `option_bars_cache` were served after switching to Alpaca, showing a bogus 100% MAE.
  - Caches are now keyed on `source`. Legacy rows (source NULL) are never served and get overwritten on the next fetch.
  - Any new cache must key on its source too.
- **Auto mode blocks self-merges** ("Merge Without Review", "Self-Approval"). Don't work around it: ask the owner, or use an explicit standing approval quoted by the parent session.
- **Agents killed the owner's servers** by freeing ports 9000/5173 during cleanup. Every brief now forbids it; if it happens, restart both (section 2).
- **`localhost` vs `127.0.0.1`:** use `127.0.0.1:5173` in the browser.
- **Fill timestamps carry fractional seconds.** A playhead at the exit's whole second still hides the exit, so test gating one second after.
- **Windows: the first numpy import inside an MCP tool's worker thread can hang.** The server imports `MockProvider` on the main thread to avoid it.
- **Browser-pane screenshots often time out.** Check with `javascript_tool` instead: read `main.innerText`, count small fonts and overflow, and step with `ArrowRight` events.
- **Closing a sim round trip logs a sim trade into the real DB** (`POST /api/sim/trades`). In browser checks, buy and add, but don't go flat unless you mean to.
- **One flaky backend test seen once:** `test_window_end_is_withheld_until_the_playhead_reaches_the_exit`. It passed on rerun; if it recurs, investigate.
- LF→CRLF warnings on commit are harmless.

## 5. Where the build stands

Keep this short and update it when a slice lands. Details live in the issues.

- **AI desk (epic #27):** done, including market data #48–#52 (PRs #53–#58), plus the source-keyed cache fix and the contract axis fit (#57, #59).
- **Redesign (epic #73):**
  - Slices 1–5 (#60–#64) are the Replay redesign: PRs #74, #75, #77, #78, #79. All are merged and browser-checked.
    - Replay's rule bar filters the day's earlier trades in the client. The full day response is still visible in the network tab. Move that cutoff server-side if it matters.
    - The trade picker shows R as "—" until #76 lands.
  - **Slice 6 (#65) is PR #76: ready but needs review.** It is the rule results API plus one R definition. The agent's open questions:
    1. A single trade that blows past the daily stop with nothing after it currently counts as "kept", while `stop_result` still reports the hit. Should it be broken?
    2. A manually set stop price no longer changes R.
  - **After #76 lands:**
    - switch the Replay real-trade card from its client-side gross R/$ to the API's `r` / `r_basis` and net $;
    - then build 7–8 (Trade review), 9–10 (Days) and 11 (nav).
    - 12–13 (Learn, Drills) are "later": filed, not built.
  - Slice 7 must add the excursion data-quality check: thin 0DTE 1-min wicks make MAE/MFE and "held past stop" look worse than the fills.
