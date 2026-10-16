# DEMO — M8c: course Concepts (extraction pass + concept index + cross-linking)

**Goal:** browse the course curriculum by *idea*, not just by module order. An
offline, resumable LLM pass reads each lesson transcript and maps it to the
concepts it teaches (drawn from a fixed course vocabulary), producing a browsable
**Concept index** — each concept with its definition, aliases, related concepts,
and the lessons that cover it. Lessons cross-link back to their concepts.

M8c ships in two PRs, both now landed:
- **PR-1 (Concepts, R6/R8/R11)** — concept extraction pass + concept index.
- **PR-2 (Study sheets, R7/R12)** — study-sheet pass + reader rendering + ⌘K.

Scope: PRD-M8 §6 — **R6** (concept extraction pass), **R7** (study-sheet pass),
**R8** (`TRADEDESK_LLM=none` degradation), **R11** (Concept pages), **R12** (⌘K
quick-search). Issue #54.

## The generation seam (why it's separate)

`app/llm/base.py` is the **frozen, interview-only** `LLMProvider` Protocol
("Do not add generation methods"). M8c does not touch it. Instead a thin
single-shot JSON-generation seam — `app/llm/generate.py::generate_json` — reuses
the existing `claude-cli` subprocess runner and the anthropic-API path, and
returns `None` on `TRADEDESK_LLM=none` or any failure. The concept pass calls it
per lesson; nothing about the debrief interview changes.

## Run
```bash
make dev                 # backend :9000 + frontend :5173, mock mode
TRADEDESK_LESSONS_DIR=/path/to/lessons make knowledge-ingest   # one-time (M8a; idempotent)

# Concept extraction pass (offline, resumable). Needs an LLM backend:
TRADEDESK_LLM=claude-cli make knowledge-concepts                 # full corpus (one call per lesson)
TRADEDESK_LLM=claude-cli make knowledge-concepts ARGS="--limit 3"  # smoke sample
make knowledge-concepts ARGS="--force"                           # rebuild all lessons

# Study-sheet pass (offline, resumable). Same seam, same flags:
TRADEDESK_LLM=claude-cli make knowledge-sheets                   # full corpus (one call per lesson)
TRADEDESK_LLM=claude-cli make knowledge-sheets ARGS="--limit 3"    # smoke sample
```
**Zero-credential guarantee holds.** With the default `TRADEDESK_LLM=none` the
pass seeds the concept *vocabulary* (so the index is browsable) but extracts
nothing from lessons, and exits 0 — search + raw reading are unaffected. The
seed vocabulary defaults to the small generic example checked in at
`backend/app/knowledge/seed_concepts.example.json` (8 concepts across Strategy /
Risk / Psychology / Process). Point `TRADEDESK_SEED_CONCEPTS` at your own JSON file
(same shape) to use a vocabulary built from your course material.

## Verify (click-path)

### 1. Knowledge → Concepts (`/knowledge/concepts`)

1. Open **http://localhost:5173** → sidebar **Knowledge → Concepts** (new nav
   entry, between Modules and Search).
2. Header reads **"8 concepts"** (with the bundled example vocabulary). Concepts are grouped by category
   (Strategy, Risk, Psychology, Process) as cards, each with its
   definition, an **"aka"** alias line, and a **lesson-count** badge.
3. The **filter box** narrows by name / definition / category / alias
   (e.g. type `risk`, `stop`, `journal`).


### 2. Concept card → Concept page (`/knowledge/concepts/:slug`)

1. **Click a concept** (e.g. *Support and Resistance*). You land on
   **`/knowledge/concepts/support-resistance`**.
2. **Header**: breadcrumb (`Concepts › Support and Resistance`), the de-underscored category
   badge, and the **"Also known as"** alias list.
3. **Related concepts** render as chips labelled with the relation
   (`PART OF`, `PREREQUISITE`, `CONTRAST`, `RELATED`), each linking to its
   sibling concept page — this is the lightweight knowledge graph.
4. **Lessons that cover this** lists the mapped lessons (sorted by salience
   weight, desc), each linking to the lesson reader. Concepts with no mapped
   lessons yet show a clean "No lessons are linked to this concept yet."


### 3. Lesson reader cross-links back to concepts

1. Open a lesson the pass mapped (e.g. **`/knowledge/lessons/2`**).
2. A **CONCEPTS** row sits above the transcript with a chip per mapped concept
   (e.g. *Journaling*, *Position Sizing*), each linking to its
   concept page. Lessons with no concepts render nothing there.


## API

```
GET /api/knowledge/concepts          → concept index: [{slug,name,category,definition,
                                  aliases,lesson_count}] (sorted category→name)
GET /api/knowledge/concepts/{slug}    → definition + aliases + lessons[] (weighted) +
                                  related[] (relation-labelled)
GET /api/knowledge/lessons/{id}       → now populates `concepts:[{slug,name,category,
                                  weight}]`; `sheet` stays null (PR-2)
```

**Graceful states, no 500s:**
- `GET /concepts` before the vocabulary is seeded → `available:false,
  status:"not_generated"` with guidance to run `make knowledge-concepts`.
- `GET /concepts/{slug}` for an unknown slug → **404**.
- `GET /lessons/{id}` before extraction → `concepts:[]` (non-breaking; the M8b
  contract still holds).

**Drift guard (PRD §7 risk #2):** the extraction prompt is given only the seed
slugs (with names + aliases) and told not to invent concepts; any returned slug
outside the seed set is discarded, so the LLM can't fragment the vocabulary.

**Resumability (R6):** a per-lesson `knowledge_concept_runs(lesson_id, content_hash,
model, generated_at)` marker records completion keyed by the transcript's content
hash. A re-run skips unchanged lessons; `--force` reprocesses. A lesson that
degrades (LLM unavailable) is not marked, so it's retried on the next run.

**Storage:** new `concepts`, `lesson_concepts`, `concept_links`,
`knowledge_concept_runs` tables — brand-new, picked up by `create_all` (no ALTER;
`SCHEMA_VERSION` bumped 3→4).

## Live-sample proof (this session)

`TRADEDESK_LLM=claude-cli make knowledge-concepts ARGS="--limit 2"` processed 2 lessons
via the real `claude` CLI, wrote 3 lesson-concepts, 0 errors. Lesson 2 mapped to a handful of
concepts — all seed-vocabulary slugs. A second run skipped both (`skipped_unchanged: 2`,
wrote nothing), confirming resumability. The full-corpus pass is left to
the operator (one one-time call per lesson); the seam is proven end-to-end on the
sample.

## Tests
- `backend/tests/test_knowledge_generate.py` — `generate_json` returns `None` on
  `llm=none`; parses a canned CLI envelope on `llm=claude-cli`; envelope
  error / non-JSON / timeout all → `None`; `_invoke_claude` still returns a valid
  `InterviewTurn` (refactor regression).
- `backend/tests/test_knowledge_concepts_pass.py` — idempotent seeding; drift guard
  discards non-seed slugs; resumability (unchanged → skip); `--force`;
  `llm=none` → `skipped_no_llm`, zero lesson-concept rows.
- `backend/tests/test_knowledge_endpoints_m8c.py` — `/concepts` empty→`available:false`
  and populated→grouped; `/concepts/{slug}` 404 + populated; enriched
  `/lessons/{id}` (real concepts present; still `[]` when absent — non-breaking).
- Full backend suite green (508 passed / 1 skipped / 1 xfailed).
- Frontend `tsc -b && vite build` green.

## Notes / known limitations
- Transcript input to the extraction prompt is capped at 6000 chars per lesson
  (front-loaded); a very long lesson is clipped. Acceptable — the salient concept
  vocabulary appears early, and raw text stays fully visible in the reader.

## Verify (click-path) — PR-2: study sheets + ⌘K

### 4. Study sheet on the lesson reader (`/knowledge/lessons/:id`)

1. Open a lesson the sheet pass generated (e.g. **`/knowledge/lessons/1`**).
2. Above the transcript sits a **STUDY SHEET** card (labelled
   *"AI-distilled · verify against the lesson"* — the raw transcript stays below
   so any error is auditable, PRD §7 risk #1):
   - **Summary** — one clean paragraph.
   - **Rules** — the key rules as bullets.
   - **Diagram** — the LLM's Mermaid source rendered **client-side as SVG**
     (a `<pre>` fallback shows the raw source if a diagram fails to parse; the
     field is nullable, so lessons without a good diagram simply omit it).
   - **Self-check** — a short quiz; picking a choice reveals correct/incorrect +
     the explanation. **Nothing is scored or persisted** — it's framed as a
     self-check ("Not graded — the reasoning matters more than the pick"),
     consistent with the anti-gambling stance.
3. A lesson with no generated sheet (`sheet: null`) renders **no** study-sheet
   section — the reader is exactly as it was in M8b.


### 5. ⌘K quick-search (R12) — from anywhere

1. Press **⌘K** (macOS) / **Ctrl+K** (Win/Linux) from **any** page (it's mounted
   globally in the app shell — the screenshot below is triggered from the Replay
   page).
2. Type a query (e.g. *position sizing*) — a debounced search runs against
   `GET /api/knowledge/search`; results list passages with a **Module · Lesson**
   breadcrumb and a match snippet.
3. Select a result → navigates to `/knowledge/lessons/:id` and closes the dialog.


## API (PR-2)

```
GET /api/knowledge/lessons/{id}       → `sheet` now populated (was null): {summary_md,
                                  rules_md, diagram_mermaid|null, quiz[], model,
                                  generated_at}. Stays null when no sheet exists.
GET /api/knowledge/search?q=&k=       → (existing M8a endpoint) powers ⌘K quick-search.
```

**Study-sheet pass (R7):** `make knowledge-sheets`, resumable via
`lesson_sheets.content_hash` (the sheet row itself is the completion marker — a
sheet always produces a row), `--force`/`--limit`. Degrades under
`TRADEDESK_LLM=none` (writes nothing, exits 0). Quiz output is sanitized:
questions with < 2 choices or an out-of-range `answer_idx` are dropped.
`diagram_mermaid` is nullable end-to-end. New `lesson_sheets` table
(`create_all`, `SCHEMA_VERSION` 4→5).

**Live-sample proof (this session):** `TRADEDESK_LLM=claude-cli make knowledge-sheets
ARGS="--limit 3"` generated a valid sheet for lesson 1 — one-paragraph summary,
bulleted rules, a **valid `flowchart TD` Mermaid diagram** (rendered as SVG, not
fallback), and 3 sanitized quiz questions — with the other lessons degrading
cleanly (a malformed CLI response was caught by the seam, counted, no crash).

## Out of scope (M8c complete)
This finishes M8c. Coach Agents (M9) consume this KB (concepts + retrieval); Live
Signals (M10) are last. The `POST /api/knowledge/retrieve` internal endpoint for agents
(PRD §4.2) is deferred to M9 where its consumer exists.
