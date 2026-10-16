# PRD — M9: Coach Agents

Status: **Draft.** Depends on: M7 (trade review context + deep links),
M8 (retrieval + concept index). Builds on M6's `backend/app/llm/` seam and
debrief/leak infrastructure.
Reference: `docs/prd/ROADMAP.md` §4 (principles), §5.

---

## 1. Problem

M6 shipped the pieces of a coaching loop — debrief interviews, leak detection,
drills, an LLM seam — but they don't yet *teach your course material*. The debrief
agent knows your trade; it doesn't know what your course says you should have done.
The leak detector finds "you hold losers too long"; it can't point at the two
lessons and one drill that fix it. And there's no way to ask a question about
your own trading and get an answer grounded in both your data and the
curriculum.

The user's stated learning constraint drives the design: **text and visuals
beat video**. Agents must answer with structured, citable, visual output —
never "go rewatch Module 7."

## 2. Goals

Three purpose-built agents (not an open chatbot):

1. **Trade Critic** — extends the M6 debrief: given a trade, evaluate it
   *against course rules*, citing specific lessons/concepts, and prescribe one
   drill + one reading.
2. **Curriculum Tutor** — conversational study over the M8 knowledge base:
   ask anything, get grounded answers with citations, diagrams, and
   check-your-understanding questions; can generate M6 drills from lesson
   content.
3. **Leak→Lesson Mapper** — batch job joining M6 leak detector output to M8
   concepts, so every detected leak carries "what course says about this" and a
   prescribed playlist (already a M6 primitive) with readings attached.

### Non-goals

- **No trade signals or "should I take this trade" answers.** Coaching is
  retrospective and educational. Live signals are M10 (and the separate listener daemon's job).
- **No P&L judgment.** Process adherence only — the M6 anti-gambling audit
  rules bind every prompt (score process, never outcome).
- No open-ended tool-using autonomous agent; each agent has a fixed context
  assembly and a bounded conversation shape.
- No personalized financial advice framing — the agent evaluates adherence to
  *the user's chosen curriculum*, it does not recommend investments.

## 3. User stories

- *After a red day*, I open my worst trade → "Critique". The critic already
  sees my fills, chart, plan (from the M6 debrief if done), and pulls the
  confirmation-candle lesson: "Entry at 10:02 was 2 bars before a confirmation candle
  formed — Module 7 · L083 says wait for the close above. Here's the passage.
  Drill: 10 reps of confirmation-candle timing on similar tape."
- *While studying*, I ask the tutor "when does my course say to size up?" and get the
  relevant sizing rule from your notes, the lesson citations, a Mermaid
  decision diagram, and a "quiz me" option.
- *Weekly*, the leak report now reads: leak → evidence trades → the concept
  it violates → 2 readings + 1 drill playlist, all deep-linked.
- *Anytime*, any citation clicks through to the M8 lesson reader anchored at
  the cited chunk, and any referenced trade clicks to its M7 review page.

## 4. Functional requirements

### 4.1 Shared agent infrastructure

| # | Requirement |
|---|---|
| R1 | All model calls go through the existing `backend/app/llm/` seam; agents work with both `anthropic_api` and `claude_cli` backends; `TRADEDESK_LLM=none` hides agent UI affordances cleanly (app fully usable without them) |
| R2 | **Context assemblers are code, not model tools** (v1): `TradeContext` (exists — `llm/context.py`), extended with `KnowledgeContext` (top-k retrieval via `POST /api/knowledge/retrieve` + concept expansion). Deterministic, testable, no tool-call loops |
| R3 | Every factual claim about curriculum must carry a citation `{lesson_id, chunk_ref}`; the response schema enforces it (structured output like M6's `form.py`), and the UI renders uncited curriculum claims as visually distinct ("unverified") |
| R4 | Conversations persisted: `agent_sessions(id, kind, subject_ref, started_at)` + `agent_messages(session_id, role, content_md, citations_json, ts)` — reviewable later, joinable to trades |
| R5 | Responses are markdown + optional Mermaid, rendered with the M8 reader's components (one visual language) |

### 4.2 Trade Critic

| # | Requirement |
|---|---|
| R6 | Entry points: Trade Review page (M7) button; debrief flow completion ("get critique"); leak report evidence trades |
| R7 | Context: trade + fills + candles around the trade + enrichment + debrief artifacts (if any) + retrieval keyed on trade features (setup tags, detected patterns, time-of-day) |
| R8 | Output schema: `{verdict_by_phase: {setup, entry, management, exit}, rule_checks: [{rule, source_citation, adherence: pass|fail|unclear, evidence_bar_ref}], prescription: {drill_id|drill_spec, readings: [lesson_id]}}` |
| R9 | Each `rule_check` pins to a bar/timestamp so the UI can annotate the M7 chart with pass/fail markers at the exact bars |
| R10 | Prescribed drills use the existing M6 drill engine (by id when a matching drill exists, else a scenario spec the M6 authoring path can create); prescribed readings deep-link to the M8 reader |
| R11 | Anti-gambling: prompt + schema forbid P&L-referencing praise/blame; a rule-following loser must critique better than a rule-breaking winner (regression-tested with fixture trades, extending `M6-ANTI-GAMBLING-AUDIT.md`) |

### 4.3 Curriculum Tutor

| # | Requirement |
|---|---|
| R12 | Chat UI under Knowledge section; every answer grounded in retrieval (R2/R3); "I don't find this in the curriculum" is a required honest fallback |
| R13 | Modes: **Explain** (default), **Quiz me** (generates questions from `lesson_sheets.quiz_json` or fresh), **Make it a drill** (emits an M6 scenario spec from a lesson's setup description) |
| R14 | Tutor sees reading progress (M8 R13) and the leak table, so "what should I study next?" is answerable and personal |
| R15 | Session subject can be pinned to a lesson/concept (opened from a lesson page → tutor pre-scoped to it) |

### 4.4 Leak→Lesson Mapper

| # | Requirement |
|---|---|
| R16 | Batch job (triggered on leak recompute): for each leak kind, map to concepts (static table for known leak kinds, LLM fallback for novel ones) → attach `{concept_id, readings, drill_playlist_id}` to the leak record |
| R17 | Leaks page renders the prescription; one click starts the playlist or opens the reading |
| R18 | Mapping table is data (`leak_concept_map`), editable/reviewable — not buried in a prompt |

## 5. Design notes

- **Why fixed context assembly instead of tool-use agents (v1):** determinism
  and testability. The corpus is small and the join keys (trade features ↔
  concepts) are known; a retrieval call + one model call covers the cases.
  If R7's keyed retrieval proves too rigid, v2 can grant the critic a single
  `search_knowledge` tool — the seam already supports tool loops via `claude_cli`.
- **Model choice** stays a backend config; recommend the API backend with a
  fast model for tutor chat and a stronger one for critic runs (config keys,
  not code).
- **Privacy:** trade data + curriculum excerpts leave the machine only when
  the API backend is configured, same trust boundary M6 already established;
  `claude_cli` backend keeps it inside the local Claude Code auth.

## 6. Phasing

| Phase | Scope | Demo |
|---|---|---|
| M9a | Shared infra (R1–R5) + **Trade Critic** (R6–R11) | Critique a mock option trade → cited rule checks annotated on the M7 chart, prescription links work |
| M9b | **Curriculum Tutor** (R12–R15) | Ask sizing question → cited answer + diagram; "make it a drill" produces a playable M6 drill |
| M9c | **Leak→Lesson Mapper** (R16–R18) + weekly report polish | Leaks page shows leak → lesson → drill loop end-to-end |

## 7. Risks & open questions

1. **Citation faithfulness** (model cites a chunk that doesn't support the
   claim). Mitigation: structured output with chunk refs + a cheap
   verification pass (string-overlap heuristic) demoting weak citations to
   "unverified"; eval set of 15 critique fixtures reviewed by hand.
2. **Critic wrongness is worse than absence** — a confident misread of the
   chart erodes trust. Mitigation: `unclear` is a first-class adherence value;
   prompt rewards abstention; verdicts phrased as rule-checks (falsifiable)
   not narratives.
3. **Curriculum ≠ ground truth.** The agent teaches *the user's course material*
   faithfully, which is the product goal — but UI copy should say "per course material
   Module X" not "the correct play", keeping the personalized-advice line
   bright.
4. **Open question:** do critic runs cost-gate (explicit button per trade) or
   auto-run on trade close? Recommend explicit button in M9a; revisit after
   observing usage.

## 8. Success metrics

- Critic: on the 15-trade eval set, ≥90% of rule_checks judged correctly
  grounded (manual review); zero anti-gambling violations on the M6 audit
  fixtures.
- Tutor: 20-question eval (from M8) answered with correct citations top-1
  ≥80%; honest fallback fires on 5 out-of-corpus decoy questions.
- Every leak on the Leaks page carries ≥1 reading + 1 drill.
- App remains fully functional with `TRADEDESK_LLM=none`.
