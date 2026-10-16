# DEMO — M9a: Trade Critic (shared agent infra + cited rule-checks)

**Goal:** turn a closed trade into a *coaching artifact*. Click **Critique** on a
trade and the critic evaluates it **against the course curriculum** — a
per-phase verdict (setup / entry / management / exit), a list of falsifiable
rule-checks each marked `pass` / `fail` / `unclear` and cited to a specific
lesson, pass/fail markers pinned on the review chart, and a prescription (one
drill + readings). It scores **process, never P&L** (a rule-following loser
critiques better than a rule-breaking winner), and it **degrades to nothing**
under `TRADEDESK_LLM=none` — the button simply isn't there.

Scope: PRD-M9 §6 — **R1–R5** (shared agent infra) + **R6–R11** (Trade Critic).
Curriculum Tutor (M9b) and Leak→Lesson Mapper (M9c) are out of scope. Issue #58.

## What lands

- **Shared agent infra**
  - `POST /api/knowledge/retrieve` — the single agent-facing retrieval entry point:
    top-k semantic search + concept expansion (deduped by slug, max weight,
    desc). Forwards search's `available`/`status` untouched (R2).
  - `agent_sessions` + `agent_messages` tables (schema **v6**); reviewable,
    joinable to trades. `agent_messages.payload_json` persists the structured
    `CriticResult` (R4).
  - `GET /api/agents/status` `{available, llm}` — gates the UI (R1).
- **Trade Critic**
  - `POST /api/agents/critic` `{trade_id}` → `CriticResult` (R6–R10). Reuses the
    M6 `build_debrief_context` verbatim for the trade half; `build_knowledge_context`
    assembles the curriculum half from setup-tag names + detected patterns
    (= tag names) + an ET time-of-day bucket — deterministic, no tool loop (R7).
  - `CritiquePanel` renders the four phase verdicts, rule-checks with adherence
    badges + citation deep-links, and the prescription; **uncited checks render
    as visually-distinct "unverified"** (R3). One visual language — plain
    markdown + Mermaid, no markdown lib (R5).
  - Chart markers: each `rule_check.evidence_bar_ref` (ISO ts) maps to a bar and
    draws a pass/fail/unclear marker distinct from fills (R9).
- **Anti-gambling (R11)** enforced at three layers (schema has no P&L field,
  prompt has the anti-gambling clause + rewarded `unclear`, behavioral
  regression tests). See `docs/M6-ANTI-GAMBLING-AUDIT.md` § M9a.

## Run

```bash
# .env: TRADEDESK_MODE=mock, TRADEDESK_LLM=claude-cli  (=api or =none also valid)
make dev                 # backend :9000 + frontend :5173
```

The knowledge base must be ingested (`make knowledge-ingest`; already done in dev).
Retrieval works over all ingested lessons; concept-expansion + study sheets
are populated for a **limited sample** of lessons this milestone — the M8c
passes are resumable and a later session finishes the rest (`make knowledge-concepts`,
`make knowledge-sheets`). This does not gate the critic: it retrieves and cites across
the full lesson set.

## Verify (click-path)

### 1. Set up a trade to critique

1. Sidebar **Journal** → **Import fills** (or `POST /api/import/fills`) → mock
   provider builds ~10 round-trips.
2. Open a closed option/equity trade, e.g. **`/trades/5`** (HOOD long). Add a
   couple of setup tags (**EMA-pullback**, **VWAP-reclaim**) in the Journal
   panel so retrieval keys on those concepts. (Tags drive `detected_patterns`.)

### 2. Critique (`/trades/{id}`)

1. In the header, click **Critique** (present only when
   `GET /api/agents/status` → `available:true`). The critic runs one LLM call
   (~15–30 s under `claude-cli`).
2. The **Critique** panel appears below the chart: "*per course material — process, not
   P&L*".
   - **Phase verdicts** — four cards (Setup / Entry / Management / Exit), each a
     short falsifiable verdict. No dollars, no P&L.
   - **Rule checks** — each row shows the rule, an adherence badge
     (**Pass** green / **Fail** red / **Unclear** amber), a note, and a
     citation link (e.g. *Module 2 · Lesson 012 — Example lesson title*). A check
     with no valid/faithful citation renders as **"unverified — no curriculum
     citation"**, visually distinct (R3).
   - **Prescription** — Readings deep-link to the M8 reader; a drill links to an
     existing scenario by id, or offers **"Create this drill"** (POST
     `/api/training/scenarios` → navigate) when the critic returns a spec (R10).
3. On the review chart, **pass/fail/unclear markers** appear at the bars pinned
   by each `evidence_bar_ref`, distinct from the fill arrows (R9). Markers whose
   timestamp falls outside the visible window are skipped.
4. Click a citation → the M8 lesson reader opens at that lesson
   (`/knowledge/lessons/109`), anchored at the cited chunk (R5). Any referenced
   trade clicks back to its M7 review page.
5. Re-open the trade → the header shows **"Re-run critique"** and the session is
   retrievable via `GET /api/agents/sessions?subject_ref=trade:5` and
   `GET /api/agents/sessions/{id}` (R4).

Secondary entry points (R6): **"Get critique"** on the debrief completion
screen, and per-trade **Critique** links on the Leaks page evidence rows.

### 3. Degrade cleanly (R1)

1. Set `TRADEDESK_LLM=none`, restart the backend, reload `/trades/5`.
2. The **Critique** button is **gone**; no chart markers; the trade page is
   otherwise fully functional (chart, fills, journal). `POST /api/agents/critic`
   returns **HTTP 200** `{available:false, status:"llm_unavailable"}` — never a
   500. `POST /api/knowledge/retrieve` still works (retrieval is credential-free).

## Live-run evidence (this session)

Critiqued mock trade #5 (HOOD long, EMA-pullback + VWAP-reclaim) under
`claude-cli`: 5 rule-checks (2 `fail`, 3 `unclear` — abstention working),
citations to three in-context lessons (all faithful),
`evidence_bar_ref` timestamps pinning to real bars, readings for the cited lessons,
one citation correctly demoted to **unverified**. Session persisted
(`session_id=1`, 5 citations + structured payload). Verdicts contain no P&L
language.

## Tests

`backend/tests/`: `test_knowledge_retrieve.py`, `test_agents_store.py`,
`test_agents_critic.py` (llm=none degradation + canned-`run_fn` happy path +
out-of-context citation demotion + 404 unknown trade),
`test_agents_anti_gambling.py` (disciplined-loser-vs-lucky-winner regression +
no-P&L-token + prompt-clause + no-P&L-field). **548 backend tests pass**
(524 pre-existing + 24 new); frontend `tsc -b && vite build` green.

## Manual test plan (for reviewer, real env)

```bash
git checkout feat/issue-58-m9a-critic
cd backend && . .venv/bin/activate && python -m pytest -q      # 548 pass
cd ../frontend && npx tsc -b && npx vite build                 # green
# .env: TRADEDESK_MODE=mock, TRADEDESK_LLM=claude-cli
make dev
#   → import fills, open /trades/5, tag EMA-pullback + VWAP-reclaim, click Critique
#   → confirm cited rule_checks deep-link to the lesson; unverified styling on uncited;
#     pass/fail markers on the chart at evidence bars; readings/drill links work
# flip TRADEDESK_LLM=none, restart backend, reload → Critique button gone, no 500
```
