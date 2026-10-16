"""knowledge base endpoints (M8a + M8b).

    GET /api/knowledge/status                 -> ingestion + embeddings availability
    GET /api/knowledge/search?q=&k=           -> ranked transcript passages (M8a)
    GET /api/knowledge/modules                -> curriculum tree + read/star counts (M8b)
    GET /api/knowledge/lessons/{id}           -> full lesson + chunk anchors + progress (M8b)
    PUT /api/knowledge/lessons/{id}/progress  -> set reading progress (idempotent) (M8b)

Read-only and mock-safe: all resolve with no credentials. Availability-style
endpoints (status/search/modules) never raise when ingestion has not been run —
they report a clear "not ingested" state. Lesson fetch/progress raise 404 for a
missing lesson id (a hard client error, not an availability state).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.knowledge import search as knowledge_search
from app.knowledge import store
from app.knowledge.chunking import chunk_text
from app.models.knowledge import (
    KnowledgeChunkAnchor,
    KnowledgeConceptDetail,
    KnowledgeConceptLessonRef,
    KnowledgeConceptsResponse,
    KnowledgeConceptSummary,
    KnowledgeLessonConceptRef,
    KnowledgeLessonResponse,
    KnowledgeLessonSheet,
    KnowledgeModule,
    KnowledgeModuleLesson,
    KnowledgeModulesResponse,
    KnowledgeProgressResponse,
    KnowledgeProgressUpdate,
    KnowledgeRelatedConcept,
    KnowledgeRetrieveRequest,
    KnowledgeRetrieveResponse,
    KnowledgeSearchResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_NOT_INGESTED = (
    "knowledge base is not ingested yet. Run `make knowledge-ingest` "
    "(or `python -m scripts.ingest_knowledge`) to build the search index."
)

_NOT_GENERATED = "Concepts not generated yet. Run `make knowledge-concepts`."


def _citation(module: str, lesson_id: int, title: str) -> str:
    return f"Module {module} · Lesson {lesson_id:03d} — {title}"


def _module_sort_key(module: str) -> list[float]:
    """Numeric-aware sort key for module labels: "0" < "1" < "3.2" < "12.1".

    Split on "." and compare numerically per segment; non-numeric segments fall
    back to +inf so they sort after numeric labels deterministically."""
    parts: list[float] = []
    for seg in str(module).split("."):
        try:
            parts.append(float(seg))
        except ValueError:
            parts.append(float("inf"))
    return parts


@router.get("/status")
def knowledge_status() -> dict:
    """Lightweight health/availability for the Knowledge section. Safe to call
    before ingestion — returns counts of zero rather than erroring."""
    from app.knowledge.embedder import get_embedder, sentence_transformers_available
    from app.knowledge.vector_index import chromadb_available

    count = store.lesson_count()
    return {
        "ingested": count > 0,
        "lesson_count": count,
        "module_counts": store.module_counts() if count else {},
        "embedder": get_embedder().name,
        "local_embeddings_available": sentence_transformers_available(),
        "chromadb_available": chromadb_available(),
    }


@router.get("/search", response_model=KnowledgeSearchResponse)
def knowledge_search_endpoint(
    q: str = Query("", description="search query"),
    k: int = Query(8, ge=1, le=50, description="number of passages to return"),
) -> KnowledgeSearchResponse:
    return knowledge_search.search(q, k)


# --------------------------------------------------------------------------- #
# M9a — Retrieval (issue #58): the single agent-facing retrieval entry point.
#
# Contract seam (PM-owned route; dev1 owns the module and codes against this
# exact signature):
#
#   backend dev / dev1 — `app.agents.retrieve` (M9a):
#       retrieve(query: str, k: int = 8, *, expand_concepts: bool = True,
#                engine=None, embedder=None, index=None) -> KnowledgeRetrieveResponse
#           # composes knowledge.search.search(...) (forwarding its available/status
#           # untouched) + concept expansion via store.get_lesson_concepts
#           # (dedupe by slug, max weight, sort desc). Degrades to
#           # available=False with empty hits when the KB is not ingested.
#
# Until app.agents.retrieve lands, this stub degrades to available=False (the
# same idiom as search's not_ingested state), so the endpoint never 500s.
# --------------------------------------------------------------------------- #


@router.post("/retrieve", response_model=KnowledgeRetrieveResponse)
def knowledge_retrieve(body: KnowledgeRetrieveRequest) -> KnowledgeRetrieveResponse:
    try:
        from app.agents.retrieve import retrieve
    except ImportError:
        return KnowledgeRetrieveResponse(
            query=body.query,
            k=body.k,
            available=False,
            status="not_implemented",
            detail="Retrieval composer not implemented yet (dev1 task, M9a).",
        )
    try:
        return retrieve(body.query, body.k, expand_concepts=body.expand_concepts)
    except Exception:
        # Embedder/vector-index errors (model load failure, corrupt chroma dir)
        # can raise at call time — degrade to available=False, never a 500.
        return KnowledgeRetrieveResponse(
            query=body.query,
            k=body.k,
            available=False,
            status="unavailable",
            detail="Retrieval is temporarily unavailable.",
        )


# --------------------------------------------------------------------------- #
# M8b — Lesson Reader (issue #48)
# --------------------------------------------------------------------------- #


@router.get("/modules", response_model=KnowledgeModulesResponse)
def knowledge_modules() -> KnowledgeModulesResponse:
    """Curriculum tree: modules (numeric-aware order) → lessons (by seq) with
    per-lesson read status and per-module read/star counts. Degrades gracefully
    to available=False when nothing is ingested (never a 500)."""
    lessons = store.list_lessons()
    if not lessons:
        return KnowledgeModulesResponse(
            available=False,
            status="not_ingested",
            detail=_NOT_INGESTED,
            lesson_count=0,
            module_count=0,
            modules=[],
        )

    progress = store.get_progress_map()

    # Group lessons by module label.
    grouped: dict[str, list[dict]] = {}
    for row in lessons:
        grouped.setdefault(row["module"], []).append(row)

    modules: list[KnowledgeModule] = []
    for module in sorted(grouped, key=_module_sort_key):
        rows = sorted(grouped[module], key=lambda r: r["seq"])
        module_lessons: list[KnowledgeModuleLesson] = []
        read_count = 0
        starred_count = 0
        for row in rows:
            status = progress.get(row["id"], "unread")
            if status in ("read", "starred"):
                read_count += 1
            if status == "starred":
                starred_count += 1
            module_lessons.append(
                KnowledgeModuleLesson(
                    id=row["id"],
                    seq=row["seq"],
                    title=row["title"],
                    module=module,
                    status=status,
                    source_url=store.lesson_source_url(row["id"], module),
                    citation=_citation(module, row["id"], row["title"]),
                )
            )
        modules.append(
            KnowledgeModule(
                module=module,
                label=f"Module {module}",
                lesson_count=len(rows),
                read_count=read_count,
                starred_count=starred_count,
                lessons=module_lessons,
            )
        )

    return KnowledgeModulesResponse(
        available=True,
        status="ok",
        detail=None,
        lesson_count=len(lessons),
        module_count=len(modules),
        modules=modules,
    )


@router.get("/lessons/{lesson_id}", response_model=KnowledgeLessonResponse)
def knowledge_lesson(lesson_id: int) -> KnowledgeLessonResponse:
    """Full lesson: metadata, transcript, chunk anchors, progress, and the M8c
    forward-compat stubs (sheet=null, concepts=[]). 404 when the id is unknown
    (including on a not-yet-ingested db — the lesson genuinely doesn't exist)."""
    lesson = store.get_lesson_full(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail=f"Lesson {lesson_id} not found.")

    # Chunk anchors: recompute deterministically from raw_text with the ingest
    # chunker so offsets are stable and mirror knowledge_lessons.chunk_count.
    chunks = [
        KnowledgeChunkAnchor(seq=c.index, char_start=c.char_start, char_end=c.char_end)
        for c in chunk_text(lesson["raw_text"])
    ]

    prog = store.get_progress(lesson_id)
    if prog is None:
        progress = KnowledgeProgressResponse(lesson_id=lesson_id, status="unread", updated_at=None)
    else:
        progress = KnowledgeProgressResponse(
            lesson_id=lesson_id, status=prog["status"], updated_at=prog["updated_at"]
        )

    # prev/next in global lesson-id order (nullable at the ends).
    all_ids = sorted(r["id"] for r in store.list_lessons())
    pos = all_ids.index(lesson_id)
    prev_id = all_ids[pos - 1] if pos > 0 else None
    next_id = all_ids[pos + 1] if pos < len(all_ids) - 1 else None

    # Study sheet rides on the lesson response — null when no sheet row exists.
    sheet_row = store.get_lesson_sheet(lesson_id)
    sheet = KnowledgeLessonSheet(**sheet_row) if sheet_row is not None else None

    return KnowledgeLessonResponse(
        available=True,
        status="ok",
        detail=None,
        id=lesson["id"],
        module=lesson["module"],
        seq=lesson["seq"],
        title=lesson["title"],
        citation=_citation(lesson["module"], lesson["id"], lesson["title"]),
        source_url=store.lesson_source_url(lesson["id"], lesson["module"]),
        raw_text=lesson["raw_text"],
        chunk_count=lesson["chunk_count"],
        chunks=chunks,
        progress=progress,
        sheet=sheet,
        concepts=[
            KnowledgeLessonConceptRef(**c) for c in store.get_lesson_concepts(lesson_id)
        ],
        prev_lesson_id=prev_id,
        next_lesson_id=next_id,
    )


# --------------------------------------------------------------------------- #
# M8c — Concepts (issue #54, PR-1)
# --------------------------------------------------------------------------- #


@router.get("/concepts", response_model=KnowledgeConceptsResponse)
def knowledge_concepts() -> KnowledgeConceptsResponse:
    """Concept index grouped/sorted by category then name. Degrades gracefully
    to available=False when the concept pass has not been run (never a 500)."""
    rows = store.list_concepts()
    if not rows:
        return KnowledgeConceptsResponse(
            available=False,
            status="not_generated",
            detail=_NOT_GENERATED,
            concept_count=0,
            concepts=[],
        )

    rows.sort(key=lambda c: (c["category"], c["name"]))
    concepts = [
        KnowledgeConceptSummary(
            slug=c["slug"],
            name=c["name"],
            category=c["category"],
            definition=c["definition"],
            aliases=c["aliases"],
            lesson_count=c["lesson_count"],
        )
        for c in rows
    ]
    return KnowledgeConceptsResponse(
        available=True,
        status="ok",
        detail=None,
        concept_count=len(concepts),
        concepts=concepts,
    )


@router.get("/concepts/{slug}", response_model=KnowledgeConceptDetail)
def knowledge_concept(slug: str) -> KnowledgeConceptDetail:
    """Full concept detail: definition, aliases, related concepts, and the
    lessons that teach it (sorted by weight desc). 404 when the slug is unknown."""
    detail = store.get_concept_by_slug(slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Concept {slug!r} not found.")

    lessons = [
        KnowledgeConceptLessonRef(
            lesson_id=lsn["lesson_id"],
            module=lsn["module"],
            seq=lsn["seq"],
            title=lsn["title"],
            weight=lsn["weight"],
            citation=_citation(lsn["module"], lsn["lesson_id"], lsn["title"]),
        )
        for lsn in detail["lessons"]
    ]
    related = [KnowledgeRelatedConcept(**r) for r in detail["related"]]

    return KnowledgeConceptDetail(
        slug=detail["slug"],
        name=detail["name"],
        category=detail["category"],
        definition=detail["definition"],
        aliases=detail["aliases"],
        lesson_count=detail["lesson_count"],
        lessons=lessons,
        related=related,
    )


@router.put("/lessons/{lesson_id}/progress", response_model=KnowledgeProgressResponse)
def knowledge_set_progress(lesson_id: int, body: KnowledgeProgressUpdate) -> KnowledgeProgressResponse:
    """Idempotent upsert of a lesson's reading progress. 404 when the lesson id
    doesn't exist (no dangling progress rows for phantom lessons)."""
    if not store.lesson_exists(lesson_id):
        raise HTTPException(status_code=404, detail=f"Lesson {lesson_id} not found.")

    result = store.upsert_progress(lesson_id, body.status)
    return KnowledgeProgressResponse(
        lesson_id=result["lesson_id"],
        status=result["status"],
        updated_at=result["updated_at"],
    )
