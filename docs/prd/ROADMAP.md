# TradeDesk — Product Roadmap & Ecosystem Vision

Status: **Living document — last updated 2026-07-15.** This is the
session-to-session source of truth for milestone state (the former
`docs/HANDOFF-NEXT.md` is retired). §3 carries the current status tree;
GitHub issues carry the slice-level detail.

This document ties together four projects that grew independently and defines
what each becomes. Individual features are specced in `docs/prd/PRD-M*.md`.

---

## 1. The one-sentence vision

**TradeDesk becomes the single place where you journal, review, backtest, and
*learn* — with the course curriculum and your own trading history wired into
agents that teach you from text and visuals instead of hours of video.**

TradeZella is the reference point for the journal/review/backtest loop.
The differentiator is the education layer: TradeZella teaches you generic
trading stats; TradeDesk teaches you *your own course material* against *your* trades.

---

## 2. Ecosystem map — what each component becomes

| Component | Today | Target role |
|---|---|---|
| **tradedesk** | Journal + replay + training (M1–M4, M6 done; M5 pending) | **The product.** UI hub, database of record, agents, knowledge base consumer |
| **Lesson corpus** | Plain-text lesson transcripts you supply yourself (a lessons dir + per-module grouping files) | **Input artifact.** Read once by the M8 ingestion script; never committed. No new code |
| **Listener daemon** | Live audio → speech-to-text → LLM + retrieval → phone push (separate local project) | **The live daemon.** Stays a separate process (audio capture doesn't belong in a web app), gains one output: POST signals to TradeDesk (M10) |

**Integration principle: integrate via data, not via code.** The listener daemon
talks to TradeDesk over HTTP/JSON exactly like the frontend does. The lesson
transcripts are read once by an ingestion script. No shared Python packages,
no monorepo merge. This preserves TradeDesk's provider-seam architecture and
keeps each project independently runnable.

An earlier idea proposed merging the listener daemon with the lesson tooling.
This roadmap supersedes it: the daemon already *is* the live half, and TradeDesk
is a better home for the knowledge/review half because that's where your trades
live.

---

## 3. Milestones — sequence and rationale

```
M5  Webull SDK migration            (landed 2026-07-09 — docs/PLAN-M5.md)
M7  Trade Review 2.0                (PRD-M7)  ← daily-use value, no LLM needed
    └─ M7a review page + drill-in and M7b option premium landed
       (2026-07-11/12, #35–#37); M7c backtest handoff (#38) pending
M7.5 Trade Review Funnel            (epic #64 — landed 2026-07-13) one
    chart-anchored funnel: WS-0 enrichment durability (#66), WS-B debrief
    tab on the /trades/:id cockpit (#63/#62), WS-C day-surface merge into
    /journal/:date (#67). Landed ahead of M9b.
M8  knowledge base              (PRD-M8)  ← makes the curriculum queryable
    └─ M8a ingestion+search (#47), M8b Lesson Reader (#48), M8c concept/
       sheet code (#54) all landed; pass runs over ~186 lessons remain (#41)
M9  Coach Agents                    (PRD-M9)  ← joins M7's data + M8's knowledge
    └─ M9a Trade Critic + agent infra (landed 2026-07-12, #58 — docs/DEMO-M9a.md);
       M9b Curriculum Tutor + M9c Leak→Lesson Mapper next (after #41 pass runs)
M10 Live Signals (listener-daemon bridge)  (PRD-M10) ← last, depends on all of the above
```

Why this order:

1. **M7 before everything.** Per-trade review with visible entries/exits and
   one-click backtest is the feature you'd use every single trading day, it
   needs zero LLM/embedding infrastructure, and it creates the deep-link
   surface (`/trades/:id`, `/replay?from_trade=`) that agents in M9 will cite.
2. **M8 before M9.** Agents are only as good as what they can retrieve. Build
   and *validate* retrieval quality on the lesson transcripts first, with a plain
   search/reading UI as its own deliverable. The lesson reader alone solves
   the "I can't learn from video" problem before any agent exists.
3. **M9 joins the two.** The trade critic needs M7's trade context assembly
   and M8's retrieval. Building it earlier means rebuilding it later.
4. **M10 last.** Live signal capture already works today in the daemon
   standalone — you lose nothing by deferring the bridge. The valuable new
   piece (call-vs-execution adherence) needs M7's trade review to be useful.
5. **M5 already landed** (2026-07-09 — new SDK + real token flow). Live Webull
   fills work; market data still awaits the OpenAPI quotes subscription, which
   gates nothing in M7–M9 (mock-first like everything else).

### Cross-cutting: data-quality track (no milestone, chip away alongside)

The M9 agents' qualitative + quantitative advice is only as good as the fill
data underneath. Known gaps, roughly in order of analytic impact:

| Issue | Gap | Why it distorts analysis |
|---|---|---|
| #77 (follow-up) | Stored fill `ts` may disagree with the chart by >1h (not a tz offset) | Entry/exit timing patterns, "traded outside plan window" checks |
| #21 | Expired/assigned 0DTE options have no closing fill → `net_pnl=None`, excluded from calendar | Daily P&L wrong on 0DTE days; critic never sees those trades |
| #18 | Fees/commissions all zero from order history (~3% P&L gap) | Small-win trades may actually be losers |
| #19 / #14 | `Fill.account=None` on imports | No per-account attribution (MARGIN vs CASH) |
| #15 | Multi-leg/spread orders flattened per-leg | Wrong grouping the day a spread is traded |
| #79 | Option premium charts are synthetic BS estimates; index options (SPXW/NDX) get none | Premium-curve review is approximate; Databento wiring specced |
| #68 | Daily Review journal prompt answers don't persist | Qualitative self-review data silently lost |

---

## 4. Non-negotiable principles (inherited, extended)

These carry forward from `docs/PLAN.md` and `docs/PLAN-M6-TRAINING.md` and
apply to every PRD in this set:

1. **Read-only broker, forever.** No order entry in any milestone, including
   "backtest this trade" (replay sim only) and live signals (alerts inform,
   never execute — the daemon's own constraint).
2. **Mock-first.** Every feature demos with `TRADEDESK_MODE=mock` and
   `TRADEDESK_LLM=none`. LLM-dependent features degrade gracefully (M8's
   search works without generation; M9 is the only LLM-required milestone).
3. **Score process, never outcome.** Anti-gambling rules from M6 extend to
   agents: the trade critic evaluates rule adherence, not P&L. An agent must
   never celebrate profits or prescribe revenge trades.
4. **The LLM is a seam.** `backend/app/llm/` (base protocol, anthropic_api,
   claude_cli backends) is the only place model calls happen. M9 agents build
   on it; no new LLM client code elsewhere.
5. **One schema for all trades.** Simulated, training, and live trades share
   the `trades` table with context flags. New sources (M10 signals) get their
   own tables but join against the same trades.
6. **Contract-first, file-ownership boundaries** per `docs/PLAN.md §4` when
   delegating to subagents.

---

## 5. Key architecture decisions (proposed — confirm before M8)

### Vector DB vs. knowledge graph for course content

**Recommendation: ChromaDB (embedded, local) + a lightweight *concept index*
in SQLite. No graph database.**

- ChromaDB-style embedded retrieval is proven on corpora like this one.
  Embedded mode means no new service in `make dev`.
- A full knowledge graph (Neo4j etc.) is high setup cost and its main payoff —
  multi-hop relational queries — isn't how trading curriculum questions look.
  Real queries are "what does my course say about confirmation candles on the 5m?"
  (retrieval) and "which lessons cover my leak?" (tagging).
- The 20%-effort/80%-value substitute: an extraction pass that builds a
  **concept index** — `concepts(name, definition, aliases)` ×
  `lesson_concepts(lesson, concept, weight)` × `concept_links(a, b, relation)`
  in SQLite. That *is* a small knowledge graph, queryable with joins,
  browsable in the UI, and cheap to rebuild. Details in PRD-M8.

### Where embeddings come from

Local sentence-transformers model (no API key, works offline, corpus is small:
a few hundred lessons ≈ a few thousand chunks). The Anthropic API has no embeddings
endpoint; a paid embeddings API is unjustified at this scale. Seam it like
everything else so it's swappable.

### Option premium data

The journal already groups option trades per-contract
(symbol+type+expiry+strike). Charting the *option's own* price during a trade
needs option candles, which neither the mock provider nor the Webull adapter
supplies today.

**Verified against Webull docs 2026-07-10** (see `docs/DEMO-M4.md` § API drift
notes): options data via OpenAPI is (a) gated behind a **separate OPRA
Real-Time Non-display subscription** — distinct from the stock Level 1/Level 2
subs — and (b) **real-time only**: the Data API has no historical option bars
endpoint (asset categories are `US_STOCK`/`US_ETF` only). So historical
premium curves for trade review can never come from Webull, regardless of
what's purchased.

M7 therefore treats the synthetic-premium fallback (Black-Scholes derived
from underlying candles, anchored to recorded fill prices) as the **primary**
data path, behind a provider-seam method (`get_option_bars`) that a future
third-party provider (Polygon, ThetaData, …) could implement if estimated
curves ever prove insufficient. No new subscription purchase is part of this
roadmap. Details in PRD-M7.

---

## 6. What we are explicitly NOT building

- **Auto-trading / order routing** — prohibited by the read-only guarantee.
- **Multi-user / cloud deployment** — local-only, single user stays.
- **Redistribution of course content** — the knowledge base is a personal study
  tool over your own course notes; it never leaves localhost, and none of the content is committed to this repository.
- **A general-purpose chat app** — M9 agents are purpose-built (critic, tutor)
  with fixed context assembly, not an open chatbot with tool soup.
- **Video playback / hosting** — text and visuals are the point; optionally link to
  the original lesson (via `TRADEDESK_LESSON_URL_TEMPLATE`) when the user wants the original video.

---

## 7. Success criteria for the roadmap as a whole

After M7–M9, a typical evening looks like:

1. Open the calendar → click today → see each trade card with entry/exit
   arrows on the chart and the option premium curve.
2. Click "Debrief" on the worst trade → the critic agent has already pulled
   the trade context and the two lessons relevant to what went wrong,
   and cites them by module/lesson with excerpts.
3. It prescribes a drill (existing M6 engine) and a 5-minute reading
   (M8 lesson reader) instead of "rewatch Module 7" (45 minutes of video).
4. One click on "Backtest this setup" replays the same day/symbol with your
   actual entries overlaid, so you can practice the better exit.

If any step still requires leaving TradeDesk (except intentionally opening
the original lesson), the roadmap isn't done.
