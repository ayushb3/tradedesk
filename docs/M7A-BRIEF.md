# M7a Execution Brief — Trade Review page (share trades) + calendar drill-in

> **How to run this:** open Claude Code CLI at the repo root and say:
> *"Read docs/M7A-BRIEF.md and execute it as PM."*
> Recommended: run the session on Opus. The project agents in `.claude/agents/`
> (pm, dev1, dev2) register automatically — dev1/dev2 are pinned to Sonnet.
>
> This brief is written by the lead orchestrator (the Fable desktop session,
> "the manager"). The manager reviews your work at two gates via files you
> commit and push — you never need to contact it directly.

---

## 0. Your role

You are the **PM** for this phase. Read `.claude/agents/pm.md` — it is your
full mandate (conventions, non-negotiables, team, workflow, merge policy).
This brief adds the phase-specific mission and adapts the gate protocol for
CLI execution. Where they conflict, this brief wins.

You delegate implementation to `dev1` (backend, Sonnet) and `dev2` (frontend,
Sonnet) subagents and do integration/glue yourself. Don't implement whole
features in the PM session — decompose and delegate.

## 1. Required reading (in order, before any code)

1. `CLAUDE.md` — vendor rules, read-only guarantee, working conventions
2. `docs/PLAN.md` — architecture, data model, §4 file-ownership discipline, DoD
3. `docs/prd/ROADMAP.md` §4 — principles binding every milestone
4. `docs/prd/PRD-M7-TRADE-REVIEW.md` — the spec; M7a is §6 phase table row 1
5. Existing code to ground the contract: `frontend/src/pages/JournalPage.tsx`,
   the trades/enrichment routers under `backend/app/api/`, and
   `backend/app/analytics/` (how journal stats are served today)

## 2. Mission — exactly what M7a ships

**A. Trade Review page** — route `/trades/:id`, for **share** trades
(PRD R1–R3, R6–R8):
- Header: symbol, direction, qty, open/close timestamps, hold time, net P&L,
  P&L %, R-multiple (when stop recorded), grade.
- Underlying candle chart (existing candles path + lightweight-charts wrapper),
  timeframe selector (1m/5m), existing server-side indicator overlays available.
- Entry/exit markers: one per fill (▲ buy / ▼ sell) with qty+price labels;
  entry vs. exit visually distinct; partial fills all shown.
- Fill table: ts, side, qty, price, fees — hover-linked to chart markers
  both ways.
- Enrichment panel: notes, tags, grade, checklist — inline editing via the
  existing `trade_enrichment` API. Reuse existing enrichment components if
  the journal already has them.
- Prev/next trade navigation within the same session day.

**B. Calendar day drill-in** (PRD R10–R11):
- Day view as a **route**: `/journal/:date` (decision final — route, not
  expanding panel).
- Trade cards per trade that day: contract/symbol descriptor, direction,
  net P&L, R, hold time, grade, and a **sparkline** of the underlying during
  the hold window. Card click → `/trades/:id`.
- Calendar day click navigates to `/journal/:date`.

**C. Backend to support A+B:**
- `GET /api/trades/{id}/review` — trade + fills + enrichment + underlying
  descriptor. PRD §5.1 is the starting shape; you freeze the exact JSON.
  **Include option fields in the schema now, stubbed null** (e.g.
  `option: null`, `premium_bars: null`) so M7b extends without breaking it.
- Whatever the day view needs (extend existing journal endpoints or add
  `GET /api/journal/days/{date}/trades`) — your call, keep it consistent
  with existing router patterns.
- Sparkline data comes from underlying candles over the hold window
  (downsampled server-side; don't ship thousands of points per card).

## 3. Scope fence — will be checked at review

- ❌ NO option premium charts, `get_option_bars` seam, or synthetic
  Black-Scholes anything (M7b).
- ❌ NO backtest handoff, replay URL params, ghost markers, or
  `origin_trade_id` (M7c).
- ❌ NO MFE/MAE (R9 — options-scoped, skip in M7a).
- ❌ NO new indicators, no provider changes, no order-entry code (forever).
- Options trades may render the review page with the underlying chart only —
  that's fine; don't special-case beyond "premium chart absent".

## 4. Decisions already made — do not re-litigate

1. Day view is a route (`/journal/:date`).
2. Sparklines = underlying candles during hold window.
3. Review endpoint schema carries nullable option fields from day one.
4. Merge style: merge commits (`gh pr merge --merge`), matching repo history.

## 5. Gate protocol (CLI-adapted)

The manager (Fable desktop session) reviews asynchronously through git.
At each gate: commit, push, **stop and tell the user** to get the manager's
review. Resume only on explicit go-ahead relayed by the user.

**Gate 1 — contract freeze (before spawning dev1/dev2):**
- Write `.agents/m7a/CONTRACT.md`: exact routes + full request/response JSON
  shapes (real field names/types, example payloads), the file-ownership table
  (which agent owns which files — no overlaps; you own
  `backend/app/api/` contract files + route registration), and the task
  breakdown.
- Commit + push on branch `feat/m7a-trade-review`, then STOP:
  *"Gate 1 ready — have the manager review .agents/m7a/CONTRACT.md on branch
  feat/m7a-trade-review."*

**Gate 2 — PR open (before merging):**
- All tests green (`cd backend && python -m pytest`; `cd frontend && npm run
  build`), live verification done (`make dev` in mock mode, drive the flow
  with playwright-cli), screenshots committed under `docs/media/m7a/`,
  `docs/DEMO-M7a.md` written in the style of existing DEMO docs, critiquer
  pass done (findings fixed or explained in the PR body).
- Open the PR (sections: Summary / What lands / Tests / Linked / Out of
  scope; screenshots embedded via repo paths so they render). Then STOP:
  *"Gate 2 ready — have the manager review PR #N."*
- After the user relays approval: `gh pr merge --merge`.

## 6. Working rules (summary — full versions in the persona files)

- Everyone works in the **primary checkout** on `feat/m7a-trade-review`.
  **No worktrees** (they have no venv/node_modules here).
- Spawn dev1/dev2 with a context packet: the frozen contract, their exact
  file list, acceptance criteria, and how to verify. Their persona files
  (`.claude/agents/dev1.md`, `.claude/agents/dev2.md`) carry the codebase
  maps and ground rules.
- Devs don't commit; they report changes and you commit per meaningful unit
  with descriptive messages.
- Mock-first: everything must work with `TRADEDESK_MODE=mock`,
  `TRADEDESK_LLM=none`, zero credentials. Tests force mock mode.
- Anti-gambling UI rules (`docs/M6-ANTI-GAMBLING-AUDIT.md`): process-focused
  copy, no P&L celebration.
- Keep `docs/PLAN.md` untouched; M7a docs live in `docs/DEMO-M7a.md`.

## 7. Definition of done

- [ ] `/trades/:id` renders for every closed share trade in mock data
- [ ] Entry/exit markers correct against fills (spot-check a multi-fill trade)
- [ ] `/journal/:date` lists that day's trades with sparklines; calendar links to it
- [ ] Enrichment edits persist and survive reload
- [ ] Prev/next navigation works within a day
- [ ] Backend suite green; frontend build green
- [ ] Playwright-driven walkthrough screenshots in `docs/media/m7a/`
- [ ] `docs/DEMO-M7a.md` complete; critiquer pass done
- [ ] PR merged (after Gate 2 approval); branch deleted
