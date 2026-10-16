# DEMO — M8b: Lesson Reader (module tree + transcript reader + reading progress)

**Goal:** make your own lesson corpus (course notes / transcripts you supply) *readable* instead of watchable.
Browse the lessons as a module tree, open any lesson to read the clean
transcript (segmented by chunk anchors, with an optional link out to the original
lesson), and mark lessons **read** or **starred** — progress that
feeds the M9 tutor ("you haven't read the lesson your leak maps to").

M8b ships the **reader** on top of M8a's ingest + search layer. Study sheets,
concept pages, and ⌘K quick-search are **M8c** — the lesson response carries
`sheet: null` / `concepts: []` as forward-compat stubs so M8c extends without a
breaking change.

Scope: PRD-M8 §6 — **R9** (Knowledge nav + Modules view), **R10** (lesson reader
minus study sheets), **R13** (reading progress). Issue #48.

## Run
```bash
make dev                 # backend :9000 + frontend :5173, mock mode
TRADEDESK_LESSONS_DIR=/path/to/lessons make knowledge-ingest   # one-time (idempotent)
# no-torch fallback (fast, deterministic embedder):
KNOWLEDGE_EMBEDDER=hashing python backend/scripts/ingest_knowledge.py
```
Everything works with zero credentials (`TRADEDESK_MODE=mock`,
`TRADEDESK_LLM=none`). **The app is fully usable even if ingestion never runs** —
the Modules view shows a "not ingested" banner instead of erroring.

## Verify (click-path)

### 1. Knowledge → Modules (`/knowledge/modules`)

1. Open **http://localhost:5173** → sidebar **Knowledge → Modules** (new nav
   entry, above Search).
2. Header reads **"N lessons · M modules"** (counts from your corpus). Each module is a collapsible row
   with a **read count** badge (`3/12 read`) and a **starred** badge when > 0.
   Module 0 is expanded by default; the rest collapsed.
3. Lessons list under each module in `seq` order, each with a status indicator:
   an **empty circle** (unread), a **green check** (read), or a **gold star**
   (starred). Modules order numeric-aware (`0 < 1 < 3.2 < 12.1`).
4. Copy is process-framed — "Work through the material module by module" — no
   streaks, points, or outcome language (anti-gambling audit).


### 2. Lesson row → Lesson Reader (`/knowledge/lessons/:id`)

1. **Click a lesson** (e.g. lesson 001). You land on
   **`/knowledge/lessons/1`**.
2. **Header**: breadcrumb (`Modules › Module 0 · Lesson 001 — …`), the
   **Mark read** / **Star** toggles (top-right), the title + citation, and an
   **Open original lesson** link-out, shown only when
   `TRADEDESK_LESSON_URL_TEMPLATE` is set (e.g. `https://example.com/lessons/{lesson_id}`;
   placeholders `{lesson_id}` and `{module}`). Empty by default = no link.
3. **Body**: the full transcript, segmented into `<section id="chunk-N">`
   anchors matching the ingest chunking — deep-linkable, and the same boundaries
   search cites. No study-sheet section (that's M8c).
4. **Prev/next** navigation moves across lessons in global lesson-id order
   (disabled at the ends).


### 3. Reading progress round-trip (R13)

1. On a lesson, click **Mark read** — the button fills (active variant) and the
   status persists via `PUT /api/knowledge/lessons/{id}/progress`.
2. Click an already-active toggle again to set it back to **unread** (the row is
   kept — `unread` is a valid state). **Star** works the same way.
3. Return to **Modules** — the tree reflects it immediately: the lesson shows a
   green check / gold star and the module's `read` count increments. (`read`
   count = lessons that are read **or** starred; starred implies engaged.)


## API

```
GET  /api/knowledge/modules              → module tree + per-lesson read status
GET  /api/knowledge/lessons/{id}         → metadata + raw_text + chunk anchors +
                                     progress + prev/next ids (sheet:null,
                                     concepts:[] in M8b)
PUT  /api/knowledge/lessons/{id}/progress → idempotent set-state {unread|read|starred}
```

**Graceful states, no 500s:**
- `GET /modules` on an un-ingested db → `available:false, status:"not_ingested"`
  with guidance to run `make knowledge-ingest`.
- `GET /lessons/{id}` for a missing/non-existent id → **404**
  `{"detail":"Lesson {id} not found."}` (the one deliberate raise — a bad id is
  a hard client error, not an availability state).
- `PUT …/progress` with a bad status → **422** (Pydantic `Literal`); on a
  non-existent lesson → **404** (no dangling progress rows).

**Storage:** new `knowledge_lesson_progress(lesson_id PK/FK → knowledge_lessons, status,
updated_at)` table — brand-new, picked up by `create_all` (no ALTER migration).

## Tests
- `backend/tests/test_knowledge_reader.py` — 12 tests: modules tree (ingested +
  not-ingested empty state), lesson fetch (chunk anchors, prev/next,
  sheet/concepts stubs), lesson 404, progress transitions
  (unread→read→starred→unread) + updated_at, progress 404, invalid-status 422.
- Full backend suite green (`pytest -q`, mock mode).
- Frontend `tsc -b && vite build` green.

## Out of scope (M8c)
LLM study sheets (summary/rules/Mermaid diagram/quiz), concept extraction +
Concept pages (R11), ⌘K quick-search (R12). The lesson response already carries
`sheet`/`concepts` stubs so M8c fills them without a contract break.
