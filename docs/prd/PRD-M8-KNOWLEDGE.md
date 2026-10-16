# PRD — M8: knowledge base & Lesson Reader

Status: **Draft.** Depends on: a corpus of lesson transcripts you supply yourself (plain `.txt` files).
Reference: `docs/prd/ROADMAP.md` §5 (vector DB vs. graph decision).

---

## 1. Problem

A trading course the user has taken may exist as a set of lesson transcripts
(plain-text files, one per lesson, plus per-module grouping files). Today that
knowledge is only accessible by (a) re-watching hours of video, or (b) grepping
raw transcript dumps. The user learns far better from **text and visuals** than video, and
no downstream feature (agents, leak→lesson mapping) can exist until the
curriculum is structured and queryable.

A prior prototype already showed that embedding-based retrieval works well on
this kind of corpus, but it lived outside TradeDesk and had no reading UI.

The corpus is **the user's own course notes / lesson files**. None of it ships
with this repository; point `TRADEDESK_LESSONS_DIR` at a local directory.

## 2. Goals

1. **Ingest** all lesson transcripts into TradeDesk-owned storage: chunked +
   embedded (semantic search) and concept-indexed (structured browse).
2. **Lesson Reader UI**: read any lesson as clean text with an LLM-generated
   study sheet (summary, key rules, visuals) — the "I can't learn from video"
   fix, valuable standalone before any agent exists.
3. **Search**: ask "what does my course say about confirmation candles?" and get
   ranked passages with module/lesson citations.
4. **Concept index**: browsable map of course concepts (support/resistance,
   confirmation candles, position sizing, …) → definitions → lessons that teach them → related
   concepts. This is the lightweight knowledge graph.
5. Retrieval works with **zero API keys** (local embeddings). Generation
   (study sheets) uses the existing `backend/app/llm/` seam and degrades
   gracefully when `TRADEDESK_LLM=none`.

### Non-goals

- No agents/chat (M9 consumes this; this milestone is corpus + reader + search).
- No graph database — the concept index lives in SQLite (ROADMAP §5).
- No re-scraping or video/audio handling; transcripts are input, as-is.
- No content redistribution — everything stays on localhost, per the roadmap.
- No spaced-repetition/flashcards yet (natural M9+ extension via M6 quests).

## 3. User stories

- *As a student*, I open Knowledge → Module 7 → a lesson, and read a clean
  study sheet: 1-paragraph summary, key rules as bullets, a diagram of the
  setup, and the full transcript below — 5 minutes instead of a 45-minute video.
- *As a trader mid-review*, I search "trimming into strength" and get the
  three passages where it's taught, with lesson links.
- *As a learner*, I open a concept page (e.g. "Support and Resistance") and see
  its definition, every lesson that covers it, and the related concepts (trend,
  stop loss) — so I can follow the curriculum by *idea*, not by module
  order.
- *As the operator*, I run `make knowledge-ingest` once and re-run it safely
  (idempotent) if transcripts or chunking change.

## 4. Functional requirements

### 4.1 Ingestion pipeline (offline command)

| # | Requirement |
|---|---|
| R1 | `backend/scripts/ingest_knowledge.py` (+ `make knowledge-ingest`): reads the lessons dir (per-lesson granularity; `Module_*.txt` files in the modules dir map lesson → module); source dirs configured via `TRADEDESK_LESSONS_DIR` / `TRADEDESK_MODULES_DIR` |
| R2 | Parses lesson number/title from filenames (`001_Welcome_to_the_Course…`), module membership from the organized set; stores `lessons(id, module, seq, title, source_path, raw_text, ingested_at)` |
| R3 | Chunks transcripts (~500 tokens, overlap) → embeds → vector index (collection `knowledge_playbook`), persisted under `data/knowledge_chroma/`; chunk metadata: lesson id, module, char offsets |
| R4 | Embeddings via local sentence-transformers model behind an `Embedder` seam (swappable, mirrors `DataProvider`); no network required |
| R5 | Idempotent re-runs (content-hash per lesson; only changed lessons re-processed) |
| R6 | **Concept extraction pass** (LLM, offline, resumable): per lesson, extract concepts taught → upsert `concepts(name, slug, definition, aliases_json)`, `lesson_concepts(lesson_id, concept_id, weight)`, `concept_links(a, b, relation)` — seeded/validated against a controlled vocabulary file (`TRADEDESK_SEED_CONCEPTS`; a small generic example ships as `seed_concepts.example.json`) |
| R7 | **Study-sheet generation pass** (LLM, offline, resumable): per lesson → `lesson_sheets(lesson_id, summary_md, rules_md, diagram_mermaid, quiz_json, generated_at, model)`; prompts pinned in `backend/app/llm/prompts.py` style |
| R8 | With `TRADEDESK_LLM=none`: R6/R7 skip cleanly; search + raw reading still fully work |

### 4.2 API

```
GET  /api/knowledge/modules                       → module tree with lesson list + sheet/coverage status
GET  /api/knowledge/lessons/{id}                  → lesson + sheet (if generated) + concepts
GET  /api/knowledge/search?q=&k=8                 → ranked chunks {text, lesson, module, score}
GET  /api/knowledge/concepts                      → concept list with lesson counts
GET  /api/knowledge/concepts/{slug}               → definition, lessons, related concepts
POST /api/knowledge/retrieve   (internal, M9)     → same as search + concept expansion; the single retrieval entry point agents use
```

### 4.3 Frontend — Knowledge section

| # | Requirement |
|---|---|
| R9 | New nav section **Knowledge** with three views: Modules (curriculum tree), Concepts (index), Search |
| R10 | **Lesson Reader**: study sheet on top (summary → rules → diagram rendered from Mermaid → mini-quiz), full transcript below with chunk anchors; optional link out to the original lesson via a configurable URL template (`TRADEDESK_LESSON_URL_TEMPLATE`, e.g. `https://example.com/lessons/{lesson_id}`; empty = no link) |
| R11 | Concept page per R6 data; concepts cross-link lessons and each other |
| R12 | Search page: query → passages with highlighted match + lesson breadcrumb; ⌘K quick-search from anywhere in the app |
| R13 | Reading progress: `lesson_progress(lesson_id, status, updated_at)` (unread/read/starred) — feeds M9 tutor ("you haven't read the lesson your leak maps to") |

## 5. Design notes

### 5.1 Storage layout

- **SQLite** (existing `tradedesk.db`): `lessons`, `lesson_sheets`, `concepts`,
  `lesson_concepts`, `concept_links`, `lesson_progress`. Relational, joins
  with trades/leaks later, backed up with the rest of the db.
- **ChromaDB** (embedded, `data/knowledge_chroma/`): vectors only, rebuildable
  artifact — never the source of truth. If Chroma versioning ever hurts,
  `sqlite-vec` is the fallback (decision recorded here for future sessions).

### 5.2 Why per-lesson granularity (not per-module)

`Module_*.txt` files concatenate lessons, losing lesson boundaries and
titles. Per-lesson files give citation granularity ("Module 1 · Lesson 003")
which M9 needs for trustworthy citations. Module files are only used to
derive the lesson→module mapping.

### 5.3 Diagrams

Study-sheet diagrams are **Mermaid source generated by the LLM** (flowcharts
for setups, sequence-ish timelines for trade management), rendered client-side.
Deterministic, diffable, no image storage. Quality varies; the sheet schema
keeps `diagram_mermaid` nullable so bad diagrams can be regenerated per-lesson.

### 5.4 Cost/scale envelope

N lessons × (1 concept pass + 1 sheet pass) ≈ 2N LLM calls one-time,
resumable; chunk count ≈ 3–6k → embedding locally in minutes. No runtime LLM
cost for search/reading.

## 6. Phasing

| Phase | Scope | Demo |
|---|---|---|
| M8a | Ingestion (R1–R5), search API, minimal Search UI | Ask a question, get cited passages |
| M8b | Lesson Reader with raw transcripts + module tree + progress (R9, R10 minus sheets, R13) | Browse curriculum, read a lesson |
| M8c | LLM passes: concepts + study sheets (R6–R8), Concept pages (R11), quick-search (R12) | Open a lesson → full study sheet; browse concept map |

## 7. Risks & open questions

1. **Transcript quality** (auto-captions: mis-heard tickers, no punctuation).
   Mitigation: study-sheet prompt instructed to normalize; keep raw text
   visible so errors are auditable. Accept imperfection — text+structure
   still beats video for review.
2. **Concept extraction drift** (LLM invents concepts / splits synonyms).
   Mitigation: seed vocabulary from the seed concepts file, alias table,
   and a small manual review UI moment — M8c demo includes eyeballing the
   concept list and merging duplicates via a script.
3. **sentence-transformers dependency weight** (torch). Mitigation: keep it
   an optional extra (`requirements-knowledge.txt`); the app boots without it,
   knowledge endpoints return a clear "not ingested" state.
4. **Open question:** should the separate local listener daemon (M10) read
   this KB instead of keeping its own index? Recommend yes eventually (one
   canonical knowledge base), via the `POST /api/knowledge/retrieve` endpoint —
   decide in M10, not here.

## 8. Success metrics

- All supplied lessons ingested; lesson→module mapping 100% resolved (spot-check).
- Search returns a relevant passage in top-3 for a 20-question eval list
  written from lesson titles (checked into `backend/tests/fixtures/`).
- A full module is readable as study sheets; user-judged "I'd study from
  this" on 5 sampled lessons.
- `make dev` unaffected; app fully functional with ingestion never run.
