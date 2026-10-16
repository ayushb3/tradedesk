"""knowledge base API schemas (M8a).

Router-owned models for the search endpoint. Kept separate from the ingestion
internals so the HTTP contract is stable while the pipeline evolves.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from app.models.utc import UtcDatetime


class KnowledgeSearchHit(BaseModel):
    """One ranked transcript passage with its lesson/module citation."""

    text: str
    score: float                # cosine similarity, higher = better
    lesson_id: int              # global lesson number (e.g. 001)
    lesson_title: str
    module: str                 # module label, e.g. "7"
    seq: int                    # lesson order within the module
    char_start: int
    char_end: int
    source_url: str | None = None
    citation: str               # e.g. "Module 1 · Lesson 003 — Example Lesson"


class KnowledgeSearchResponse(BaseModel):
    query: str
    k: int
    available: bool             # false when not ingested / embeddings unavailable
    status: str                 # human-readable state ("ok" | "not_ingested" | …)
    detail: str | None = None   # guidance when unavailable
    embedder: str | None = None
    results: list[KnowledgeSearchHit] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# M9a — Retrieval (issue #58) — the single agent-facing retrieval entry point
# --------------------------------------------------------------------------- #


class KnowledgeRetrieveRequest(BaseModel):
    """`POST /api/knowledge/retrieve` body. Composes semantic search (R5) with concept
    expansion (R2): the top-k passages plus the concepts they teach, so agents
    get one deterministic curriculum context per call — no tool-call loop."""

    query: str
    k: int = 8
    expand_concepts: bool = True


class KnowledgeRetrieveResponse(BaseModel):
    """Ranked passages + expanded concept slugs. Forwards search's
    `available`/`status` untouched so an un-ingested KB degrades to
    `available=False` with empty `hits` (never a 500)."""

    query: str
    k: int
    available: bool            # false when not ingested / embeddings unavailable
    status: str                # "ok" | "not_ingested" | "index_empty" | "empty_query"
    detail: str | None = None
    embedder: str | None = None
    hits: list[KnowledgeSearchHit] = Field(default_factory=list)
    # Concept slugs expanded from the retrieved lessons: deduped by slug, kept at
    # max weight across lessons, sorted by weight desc.
    concepts: list[KnowledgeLessonConceptRef] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# M8b — Lesson Reader (issue #48)
# --------------------------------------------------------------------------- #

# Reading progress for a lesson. Absence of a progress row is treated as "unread".
KnowledgeLessonStatus = Literal["unread", "read", "starred"]


class KnowledgeModuleLesson(BaseModel):
    """One lesson entry in the module tree (no transcript body)."""

    id: int
    seq: int
    title: str
    module: str
    status: KnowledgeLessonStatus
    source_url: str | None = None
    citation: str              # e.g. "Module 1 · Lesson 003 — Example Lesson"


class KnowledgeModule(BaseModel):
    """A module (curriculum grouping) with its lessons and read counts."""

    module: str                # module label, e.g. "7", "12.1", "0"
    label: str                 # display label, e.g. "Module 7"
    lesson_count: int
    read_count: int            # lessons whose status is read OR starred
    starred_count: int         # lessons whose status is starred
    lessons: list[KnowledgeModuleLesson] = Field(default_factory=list)


class KnowledgeModulesResponse(BaseModel):
    """Module tree for the Knowledge → Modules view. Degrades gracefully when
    the knowledge base has not been ingested (available=False, empty modules)."""

    available: bool            # false when not ingested
    status: str                # "ok" | "not_ingested"
    detail: str | None = None  # guidance when unavailable
    lesson_count: int
    module_count: int
    modules: list[KnowledgeModule] = Field(default_factory=list)


class KnowledgeChunkAnchor(BaseModel):
    """A chunk's char-offset span back into the raw transcript (reader anchor)."""

    seq: int
    char_start: int
    char_end: int


# --------------------------------------------------------------------------- #
# M8c — Concepts (issue #54, PR-1)
# --------------------------------------------------------------------------- #


class KnowledgeLessonConceptRef(BaseModel):
    """A concept tagged on a lesson (chip on the reader, sorted by weight desc)."""

    slug: str
    name: str
    category: str
    weight: float


class KnowledgeConceptSummary(BaseModel):
    """One concept in the index, with how many lessons teach it."""

    slug: str
    name: str
    category: str
    definition: str
    aliases: list[str] = Field(default_factory=list)
    lesson_count: int


class KnowledgeConceptLessonRef(BaseModel):
    """A lesson that teaches a concept, with salience weight + reader citation."""

    lesson_id: int
    module: str
    seq: int
    title: str
    weight: float
    citation: str


class KnowledgeRelatedConcept(BaseModel):
    """A concept linked to another via a typed relation."""

    slug: str
    name: str
    relation: str              # related|prerequisite|contrast|part_of


class KnowledgeConceptsResponse(BaseModel):
    """Concept index for the Knowledge → Concepts view. Degrades gracefully when
    the concept pass has not been run (available=False, empty concepts)."""

    available: bool            # false when concepts not generated
    status: str                # "ok" | "not_generated"
    detail: str | None = None  # guidance when unavailable
    concept_count: int
    concepts: list[KnowledgeConceptSummary] = Field(default_factory=list)


class KnowledgeConceptDetail(KnowledgeConceptSummary):
    """Full concept detail: summary fields + the lessons that teach it + related
    concepts (both link directions)."""

    lessons: list[KnowledgeConceptLessonRef] = Field(default_factory=list)
    related: list[KnowledgeRelatedConcept] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# M8c — Study sheets (issue #54, PR-2)
# --------------------------------------------------------------------------- #


class KnowledgeQuizQuestion(BaseModel):
    """One multiple-choice question from a lesson's study sheet."""

    q: str
    choices: list[str] = Field(default_factory=list)
    answer_idx: int
    explanation: str | None = None


class KnowledgeLessonSheet(BaseModel):
    """A generated study sheet for a lesson: summary, rules, an optional Mermaid
    diagram, and a short self-check quiz. Rides on the lesson response — it is
    ``null`` when no sheet has been generated for the lesson."""

    summary_md: str
    rules_md: str
    diagram_mermaid: str | None = None
    quiz: list[KnowledgeQuizQuestion] = Field(default_factory=list)
    generated_at: UtcDatetime | None = None
    model: str | None = None


class KnowledgeLessonResponse(BaseModel):
    """Full lesson: metadata + transcript + chunk anchors + progress + M8c stubs."""

    available: bool
    status: str
    detail: str | None = None
    id: int
    module: str
    seq: int
    title: str
    citation: str
    source_url: str | None = None
    raw_text: str
    chunk_count: int
    chunks: list[KnowledgeChunkAnchor] = Field(default_factory=list)
    progress: KnowledgeProgressResponse | None = None
    # M8c enrichment — study sheet (PR-2) + concepts (PR-1). `sheet` is null until
    # the study-sheet pass has run for this lesson; `concepts` is [] until tagged.
    sheet: KnowledgeLessonSheet | None = None
    concepts: list[KnowledgeLessonConceptRef] = Field(default_factory=list)
    prev_lesson_id: int | None = None
    next_lesson_id: int | None = None


class KnowledgeProgressUpdate(BaseModel):
    """PUT body for setting a lesson's reading progress (422 on invalid status)."""

    status: KnowledgeLessonStatus


class KnowledgeProgressResponse(BaseModel):
    """Current reading progress for a lesson."""

    lesson_id: int | None = None
    status: KnowledgeLessonStatus
    updated_at: UtcDatetime | None = None


# KnowledgeLessonResponse forward-references KnowledgeProgressResponse (defined below it).
KnowledgeLessonResponse.model_rebuild()
