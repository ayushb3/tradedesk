"""Single agent-facing retrieval entry point (M9a, issue #58, R2).

Composes course semantic search (R5) with lesson-concept expansion so an agent
gets one deterministic curriculum context per call — the top-k passages plus
the concepts they teach — with NO tool-call loop. Degrades exactly as
`knowledge.search.search` does: an un-ingested KB yields ``available=False`` with an
empty ``hits`` list, never a 500.
"""
from __future__ import annotations

from sqlalchemy.engine import Engine

from app.knowledge import store
from app.knowledge.embedder import Embedder
from app.knowledge.search import search
from app.knowledge.vector_index import VectorIndex
from app.models.knowledge import KnowledgeLessonConceptRef, KnowledgeRetrieveResponse


def retrieve(
    query: str,
    k: int = 8,
    *,
    expand_concepts: bool = True,
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    index: VectorIndex | None = None,
) -> KnowledgeRetrieveResponse:
    """Retrieve the top-k passages for *query*, plus the concepts they teach.

    Forwards ``available``/``status``/``detail``/``embedder`` from the underlying
    search UNTOUCHED. When ``expand_concepts`` and the KB is available, each hit's
    lesson concepts are gathered, deduped by slug keeping the MAX weight across
    lessons, and sorted by weight descending (then name). When the KB is not
    ingested, degrades to the same ``available=False`` state search returns, with
    empty ``hits`` and ``concepts``.
    """
    resp = search(query, k, engine=engine, embedder=embedder, index=index)

    hits = list(resp.results)

    concepts: list[KnowledgeLessonConceptRef] = []
    if expand_concepts and resp.available:
        # slug -> best {slug, name, category, weight} seen across the hit lessons.
        best: dict[str, dict] = {}
        seen_lessons: set[int] = set()
        for hit in hits:
            if hit.lesson_id in seen_lessons:
                continue
            seen_lessons.add(hit.lesson_id)
            for c in store.get_lesson_concepts(hit.lesson_id, engine):
                slug = c["slug"]
                weight = float(c.get("weight", 0.0))
                current = best.get(slug)
                if current is None or weight > current["weight"]:
                    best[slug] = {
                        "slug": slug,
                        "name": c["name"],
                        "category": c["category"],
                        "weight": weight,
                    }
        ordered = sorted(
            best.values(), key=lambda c: (-c["weight"], c["name"])
        )
        concepts = [KnowledgeLessonConceptRef(**c) for c in ordered]

    return KnowledgeRetrieveResponse(
        query=resp.query,
        k=resp.k,
        available=resp.available,
        status=resp.status,
        detail=resp.detail,
        embedder=resp.embedder,
        hits=hits,
        concepts=concepts,
    )
