# TradeDesk: the AI-driven desk (plan)

> **Tracking:** epic [#112](https://github.com/ayushb3/tradedesk/issues/112) slices this plan into issues #89–#111 and #114 (label `ai-desk`), in build order.
>
> **Source:** copied from the WorkNotes vault note `Personal/TradeDesk/2026-10-15-ai-driven-desk-plan.md` (2026-10-15, reviewed). Names in *italics* are vault notes, not repo files. The checkboxes below are not the status; the epic is.

Part of *TradeDesk*. Folds the Fill Ledger, the coach and the Robinhood export into TradeDesk, with Claude as the main way in. Builds on *2026-10-15-robinhood-fill-import-wrap* and *2026-10-15-option-contract-fill-chart-plan*. Rules it measures: *the trading reset plan (vault)*.

## The idea

TradeDesk becomes the engine: one local database of every fill, every plan and every note, and one analysis library. Claude drives it through a local tool server (read and journal-write only, never the broker). An artifact page is the screen Ayush scans and taps on, on desktop or phone. The work happens in conversation: Claude imports, analyses, flags, asks, and writes the answers back.

## Decisions made

- **P&L is shown, loud.** Numbers and data first: net, per-position and per-fill P&L, R, R:R, account size, the loss line. This replaces the "P&L out of sight" direction in *2026-07-29-tradedesk-rule-adherence-north-star*. Rule-adherence numbers sit beside P&L, not instead of it.
- **Read-only broker forever.** Fills come from the guarded `rh-export` (Ayush logs in himself; writes blocked at the network layer). TradeDesk never talks to Robinhood. Claude never sees a password, an MFA code or an account number.
- **Times in PT** everywhere, labelled.
- **The trading window is 6:30–8:30 PT.** One value in the rules config; every screen reads it.
- **The Claude desktop app is the main place to work.** The desk page runs live against TradeDesk there (path B), and the review conversation happens in the same window.
- **The phone is for learning.** It gets a lighter view: the day's review, patterns and the matching lessons, synced as snapshots (path A). No trading tools on the phone.
- **TradeDesk's database is the truth** for fills, plans, notes and day fields. The vault's *trading log* becomes a generated view of it (see Phase 7).
- **Claude drives.** Import, analysis, flags, questions and write-backs happen in conversation. The page is for scanning and tapping, not for typing much.

## What the desk must show (from the Fill Ledger)

Per day: net, entries, positions, win %, avg win / avg loss, **R:R and the win rate it needs**, biggest position as × the daily stop and as % of account, last fill vs the 8:30 PT close, stop orders used / trailed / pulled, rule chips (caller, size written first, max loss declared).

Per position: contract, contracts, $ in, avg in → avg out, P&L $ and %, **R** (P&L ÷ written max loss, or 30% of premium when none), tags (added lower, held loser down, gave back, runner paid, × stop at risk, overnight), and the fill table: side, order type, qty, price, cash, per-fill P&L on average cost, avg in after, time PT.

Across days: the same columns per day, plus totals (green/red days, all-days R:R vs actual win rate).

Always on top: **the loss line** (P&L since 2026-09-29, every trade counted, $ left), and today's state: before the window, in the window, closed, stop hit, target hit, line hit.

## The day, end to end

This is the experience the phases build toward. Everything below happens in the Claude desktop app with the desk page open beside the chat.

1. **Before the open (by 6:15 PT).** Ayush opens the desk and says "start trading". Claude reads `line_status` and yesterday's day fields from TradeDesk, prints the line and runs the why-gate from `/trade-coach`. The page shows yesterday's review and the line.
2. **Plan cards (rule 4).** Before each entry he says the plan ("mentor's call, SPY 580P, $400, max −$100"; example numbers). Claude writes it with `add_plan`; the card appears on the page with its time. No card, no trade.
3. **During the window.** No broker reads happen while he trades (the export needs a login, and that is an after-close task). He reports each close in one line; Claude keeps the running tally and calls done at the daily target / the daily stop / 8:30. The stop moment runs the instant he says his stop phrase.
4. **Close (8:30 PT).** He logs out of Robinhood. Claude writes the day fields (`day_fields`: why, size written first, max loss, closed by 8:30, how he felt).
5. **Export.** He runs `rh-export/export.py` in his own terminal (login, MFA). It writes the raw orders JSON and the account snapshot to `rh-export/out/`.
6. **Import and review.** He says "import". Claude calls `import_robinhood`, then `get_day`, and opens the review: rule breaks first, then the numbers, then one or two patterns, then the matching lesson. The page refreshes itself. He annotates by talking ("that TSLA was another caller's call, grade B"); Claude writes it back.
7. **Phone, later.** Claude pushes the review snapshot to the page's database. On the phone he reads the review, the patterns and the lesson links. Nothing can be traded or imported from there.
8. **Weekly (Friday after close or Sunday).** "Weekly review" runs `patterns(range)` and a lesson-linked summary; the vault log is regenerated.

## Phases

v1 is phases 0–5 and 7. Phases 6, 8 and the "Later" list come after v1 is used for two trading weeks.

### 0. Clean base
- [ ] Merge [ayushb3/tradedesk#88](https://github.com/ayushb3/tradedesk/pull/88) once CI is green. Rebase Ayush's uncommitted `feat/m7a-trade-review` work onto it afterwards; the two must not fight over `imports.py`.
- [ ] Fix the naive-UTC timestamp bug: the API sends aware UTC, the frontend and the page format in PT. One `display_tz` setting, default `America/Los_Angeles`, labelled "PT" on every time.
- [ ] Stable trade ids across re-imports (keyed on the opening fill id), so notes, plans and AI annotations never orphan. Seam from *2026-10-13-tradedesk-reset-investigation*. Re-key `debriefs`, `agent_sessions` and `scenarios.source_trade_id` in the same change.
- [ ] The DB is `data/desk.db` (decided 2026-10-15: the name stays broker-agnostic, so a second broker never means a rename). The current `data/robinhood.db` data moves into it. Write the path in the repo's `CLAUDE.md`. Every tool, script and skill reads the same file.
- **Done when:** a re-import of the same export adds 0 fills, keeps every note and plan on the same trade, and the 10-14 fills show at 6:3x PT in the app, not 13:3x.

### 1. Analysis library and rules config in the backend
- [ ] Port the Fill Ledger analysis to pure Python with tests: average-cost per-fill P&L, add-lower / held-loser / gave-back / runner-paid, R and R:R, stop multiple, late fills, stop chains. Test fixtures are the 09-29..10-14 export (account ids stripped) and the Fill Ledger's numbers, so parity is a test, not a check by eye.
- [ ] One rules config, `data/rules.yaml` (gitignored, next to the DB; `backend/rules.example.yaml` with placeholder numbers is what gets committed, so the repo can go public): the 9 rules by number and one-line text, window start 6:30 PT, platform closed 8:30 PT (rule 5, changed from 8:00 on 2026-10-15; fills after it count as late), the daily stop, the daily target, the loss line and its start date 2026-09-29, default risk 30% of premium when no max loss is written, account-size source (`rh-export/out/account.json`), and the pattern → lesson map (Phase 5). Every screen, tool and the coach read it; nothing hard-codes a number.
- [ ] **Rules are written in conversation, not by hand** (decided 2026-10-15). A `/trade-rules` session grills Ayush on his rules, one question at a time, in the spirit of a grilling session but about trading: it starts from the rules he already has (his current rules note), checks each new rule against them, pushes on vague ones ("what number?", "what happens when you break it?"), and proposes a `rules.yaml` diff. Nothing is written until he says yes; the write goes through a validated tool and lands in the audit table.
- [ ] `day_fields` table (one row per date): why, caller(s), size written first, max loss declared, closed by 8:30, stop hit, target hit, how he felt, notes. This is the home for the vault's `days.jsonl` fields and for what `/trade-coach` writes at the close.
- [ ] API: `/days`, `/days/{date}`, `/patterns?from&to`, `/line`, `/rules`. Every response carries `tz: "America/Los_Angeles"` and `as_of` (last import time).
- **Done when:** `GET /days/2026-10-12` returns the Fill Ledger's numbers for that day to the cent, `GET /line` matches `/trade-coach` step 0, and changing the stop in `rules.yaml` changes every stop-multiple column with no code change.

### 2. Pre-trade card (rule 4 made real)
- [ ] A `plans` table: time PT, caller, contract, size $, max loss $, target, note, status (open / matched / unmatched). Entered by chat ("mentor's call, SPY 580P, $400, max −$100"; example numbers) or on the page.
- [ ] The importer matches each trade to its plan: same contract, plan time before the opening fill, same day; nearest wins. R uses the written max loss. Off-plan trades (no card) and blown max losses become loud flags on the day and the position.
- [ ] A plan with no trade stays visible as "planned, not taken". That is a good outcome and is counted as one.
- [ ] Plans can be entered after the fact, but are marked `late: true` and do not count as "size written first".
- **Done when:** a day with two plans and three trades shows two matched trades with R on the written max loss, one off-plan flag, and the match survives a re-import.

### 3. TradeDesk tool server for Claude (local MCP)
- [ ] One MCP server, `tradedesk`, over stdio (no port, no listener), launched by the Claude app. Registered in the Claude desktop app's MCP config **and** in the vault's Claude Code config so the skills and the page see the same name.
- [ ] Tools: `import_robinhood(path)`, `list_days`, `get_day(date)`, `get_trade(id)`, `patterns(from, to)`, `line_status`, `rules`, `add_plan(...)`, `annotate_trade(id, caller|notes|grade|tags)`, `day_fields(date, ...)`, `push_phone_snapshot(date)` (Phase 4A), `status` (DB path, last import, mode).
- [ ] Writes are journal-only and whitelisted field by field. `import_robinhood` accepts only files under `rh-export/out/` and never reads `account.json` beyond the four numbers. No tool returns an account id or an order id.
- [ ] No broker code, no network calls, no shell-out; a test enforces it like the importer's (AST scan for `requests`, `httpx`, `socket`, `subprocess`, `robin_stocks`).
- [ ] `TRADEDESK_MODE=mock` runs the server against a fixture DB (the stripped 09-29..10-14 export) so the page and the skills can be tested with no login and no real money on screen.
- [ ] Every write is logged to an `audit` table (tool, args, time), so "who changed this" is always answerable.
- [ ] `/trade-coach` and `/trade-log` use these tools instead of hand-built JSON and the `trades.jsonl` one-liner. Step 0 of `/trade-coach` becomes one `line_status` call.
- **Done when:** in a fresh Claude desktop session, "what was my line yesterday" is answered from `line_status` alone, and `annotate_trade` on a mock trade shows up in the app and the audit table while a `requests` import in the server fails the suite.

### 4. The artifact page (Fill Ledger v2)
- [ ] Same design as the Fill Ledger, fed by TradeDesk. The current page keeps the layout; only the data source changes.
- [ ] **Path B first (desktop app, the main screen):** the page declares `mcp: {servers: [{server: "host:tradedesk", tools: [...]}]}` and reads TradeDesk live with `watchTool` on `list_days` / `get_day` / `line_status`, so it is always current with no sync step. A host server only answers inside the Claude desktop app with that server configured; everywhere else `use("mcp")` is `null` or the call fails `server_not_connected`, and the page falls back to path A.
- [ ] **Path A (phone, learning):** `push_phone_snapshot(date)` writes the day's review, the patterns and lesson links into the page's `db`. The page renders that when the host server is absent, with a "snapshot from <time PT>" badge. The existing Fill Ledger `db` doc shape is the starting point.
- [ ] **Retire the old Fill Ledger** once this page and the phone snapshot are live (decided 2026-10-15). Ayush confirms the delete; nothing points at the old URL afterwards.
- [ ] Freshness is always visible: "live · imported 8:41 PT" on desktop, "snapshot · 8:52 PT" on phone, and a grey banner when neither source answers.
- [ ] Writes from the page go straight to TradeDesk through `callTool` (`annotate_trade`, `add_plan`) on desktop. On the phone the page is read-only in v1; any edits there are "Later". No page-side copy of the truth, so there is nothing to merge.
- [ ] "Ask Claude about this trade / day" on each position and day. In v1 this copies a ready prompt ("Review trade rh-… on 2026-10-12 with `get_trade`") for the chat beside the page; a page-side `sample` call is "Later".
- [ ] Empty and error states, each tested in mock mode: no server, no days yet, a day with open positions, export older than today, line hit, stop hit, no plans today.
- [ ] The page stays private (the `mcp` grant already bars public sharing). It holds no keys, no account ids, no order ids.
- **Done when:** the same artifact URL shows live numbers in the Claude desktop app with the server on, the last snapshot on the phone, and a clear banner when the server is off, and a caller set from the page shows up in `get_trade` without a sync step.

### 5. The coach loop
- [ ] **Before the open:** the why-gate (`/trade-coach` step 1) with the line and yesterday's day fields read from TradeDesk, then plan cards for the session.
- [ ] **During:** plan card before each entry; the running tally from what he reports; done calls at the daily target / the daily stop / window close; the stop moment the instant he says his stop phrase or wants "one more". No analysis of the trade during the stop moment. Wording follows his rules.
- [ ] **Line hit:** `line_status` left ≤ $0 means no session, no import review of "what went wrong", and the page shows only the line banner and the reset list. Not negotiable, in code and in the skill.
- [ ] **After:** he runs the export, then Claude imports, analyses, pushes the phone snapshot and opens the review: rule breaks first, then the numbers, then one or two patterns, then a link to the lesson that matches. Order fixed so the lesson always comes after the honest numbers.
- [ ] **Lesson map:** a `lessons` section in `rules.yaml`: pattern → lesson ids and one-line reason (runner paid, gave back, added lower, late fills, ...; ids from his local lesson map, kept in the gitignored `data/rules.yaml`). Links go to TradeDesk `/knowledge/lessons/:id`; the lesson corpus is ingested on this PC (decided 2026-10-15). Needs the lesson files copied here first (*2026-10-15-windows-pc-handoff*).
- [ ] **Course content never leaves this PC** (decided 2026-10-15). No lesson text, excerpt or summary goes into the artifact page, the phone snapshot, a fixture, a PR or an issue. The page and the snapshot carry lesson ids and Ayush's own one-line reasons from `rules.yaml` only; the lesson opens in local TradeDesk.
- [ ] **Weekly:** `patterns(range)` across days (R:R trend, added lower, gave back, late fills, biggest risk), with the lessons that recur, written as one note in `Personal/TradeDesk/summaries/`.
- **Done when:** one full mock day runs start to finish in chat (gate → two plans → tally → close → export fixture → import → review with a lesson link → phone snapshot) with no hand-edited JSON, and his stop phrase mid-tally stops the flow with the stop moment and nothing else.

### 6. Option-contract chart (after v1)
- [ ] Build *2026-10-15-option-contract-fill-chart-plan*: 1-min contract bars with fills marked, plus "left on the table" (best price after exit) per position. The chart lives in TradeDesk Trade Review; the page links to it. Needs the Alpaca decision from that note.
- **Done when:** a real 10-14 trade shows the contract's own bars with every fill on them, the plan's stop line, and the best-after-exit number.

### 7. Migrate the vault trading log
- [ ] Trades: re-import from the raw exports (09-29 onward already exist in `rh-export/out/`). The 09-29 recap-only rows and any excluded plays stay in the vault file as history; nothing is lost.
- [ ] Days: a one-off script copies `days.jsonl` (highest `rev` per date: why, the caller flag, size_written_first, max_loss_declared, platform_closed_by_8, stop/target, notes) into `day_fields`. Check the two line totals match before cutting over.
- [ ] After cutover: `trades.jsonl` and `days.jsonl` are frozen (read-only, kept for history). `scripts/trade_log.py render` reads from `/days` and keeps writing the generated `index.md` and the plan note's table, so the vault still sees every day. `/trade-log` becomes a thin skill over the MCP tools.
- [ ] `/trade-coach` step 0 switches to `line_status` only once the two totals have matched for a week.
- **Done when:** `line_status` equals the old `trades.jsonl` one-liner to the dollar, the vault `index.md` regenerates from TradeDesk, and no skill writes to the jsonl files anymore.

### 8. Onboarding the next session
- [ ] Write a short runbook in `Personal/TradeDesk/desk-runbook.md` and link it from the TradeDesk index and `Personal/Trading/CLAUDE.md`: how to start the server (one command), the DB path, the export command, the artifact URL, mock mode, and "what to do when the page says no server".
- [ ] The repo's `CLAUDE.md` gets a "desk" section: rules.yaml is the source of truth, the MCP tool list, the no-network test, and the DB path.
- [ ] This plan's checkboxes are the status. Each session ticks what landed and adds one line to the matching wrap note in `summaries/`.
- **Done when:** a fresh Claude desktop session, given only the runbook, gets the live page and `line_status` working in under five minutes with no questions asked of Ayush.

## Later (not v1)
- Phone edits (notes on the go) with a queue Claude applies at the next review.
- "Ask Claude" as a page-side `sample` call instead of a copied prompt.
- Drills on the phone built from his own trades (M6 scenarios from real days).
- Called-trade log from mentor session recordings (M10, *2026-07-29-tradedesk-rule-adherence-north-star*); manual plan cards are the v1 version.
- M9c leak → lesson mapper replacing the hand-kept lesson map.

## Open decisions
- **Window end: decided 8:30 PT** (2026-10-15). Rule 5 in *the trading reset plan (vault)* still says 8:00 until the vault edit lands; `rules.yaml` uses 8:30.
- **Where the truth lives.** Decided above: TradeDesk's database; the vault log becomes generated (Phase 7). Confirm the cutover order: a week of both totals matching before the skills switch.
- **DB file: decided `data/desk.db`** (2026-10-15), so the name stays broker-agnostic.
- **Account size source.** The export's `account.json` equity snapshot after the close, or a hand-set number in `rules.yaml`? Proposed: the snapshot, with the config value as the fallback.
- **What the phone learning view holds.** Proposed: the last review, the week's patterns, and the lessons they point to. Possibly drills later.
- **Lesson corpus: decided, ingest on this PC** (2026-10-15). No course lessons or data can be made public.
- **Fill Ledger: decided, retire it** (2026-10-15) once the v2 page and the phone snapshot are live. Ayush confirms the delete.
- **Rules: decided, written in conversation** (2026-10-15) through `/trade-rules`, never hand-edited as the normal path.

## Related
- *2026-07-29-tradedesk-rule-adherence-north-star* — the earlier direction; its rule metrics carry over, its hidden P&L does not
- *Trading desk* — coach and course sessions
- *2026-10-15-windows-pc-handoff* — getting the lessons onto this PC
- *trade-log-skill-spec* — the vault log this plan migrates
- Fill Ledger page: link in the vault
