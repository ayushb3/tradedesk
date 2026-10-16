"""Course semantic search (PRD-M8 §4.2, R5).

Embeds the query with the same `Embedder` seam used at ingest, runs a top-k
similarity query against the vector index, and hydrates each hit with its lesson
citation from SQLite. Degrades gracefully: when nothing is ingested (or the
index is empty), returns ``available=False`` with a clear status rather than
raising, so the app boots and the endpoint responds even if ingestion never ran.
"""
from __future__ import annotations

from sqlalchemy.engine import Engine

from app.knowledge import store
from app.knowledge.embedder import Embedder, get_embedder
from app.knowledge.vector_index import VectorIndex, get_vector_index
from app.models.knowledge import KnowledgeSearchHit, KnowledgeSearchResponse

_NOT_INGESTED = (
    "knowledge base is not ingested yet. Run `make knowledge-ingest` "
    "(or `python -m scripts.ingest_knowledge`) to build the search index."
)


def _citation(module: str, lesson_id: int, title: str) -> str:
    return f"Module {module} · Lesson {lesson_id:03d} — {title}"


def search(
    q: str,
    k: int = 8,
    *,
    engine: Engine | None = None,
    embedder: Embedder | None = None,
    index: VectorIndex | None = None,
) -> KnowledgeSearchResponse:
    q = (q or "").strip()
    if not q:
        return KnowledgeSearchResponse(
            query=q, k=k, available=False, status="empty_query",
            detail="Provide a non-empty query string.",
        )

    # Nothing ingested → clear "not ingested" state (never a 500).
    if store.lesson_count(engine) == 0:
        return KnowledgeSearchResponse(
            query=q, k=k, available=False, status="not_ingested", detail=_NOT_INGESTED,
        )

    embedder = embedder or get_embedder()
    index = index or get_vector_index(store.vector_dir())

    if index.is_empty():
        return KnowledgeSearchResponse(
            query=q, k=k, available=False, status="index_empty", detail=_NOT_INGESTED,
            embedder=embedder.name,
        )

    qvec = embedder.embed([q])[0]
    hits = index.query(qvec, k)

    results: list[KnowledgeSearchHit] = []
    for h in hits:
        m = h.metadata
        lesson_id = int(m.get("lesson_id"))
        module = str(m.get("module", "?"))
        title = str(m.get("title", ""))
        results.append(
            KnowledgeSearchHit(
                text=h.text,
                score=round(h.score, 4),
                lesson_id=lesson_id,
                lesson_title=title,
                module=module,
                seq=int(m.get("seq", 0)),
                char_start=int(m.get("char_start", 0)),
                char_end=int(m.get("char_end", 0)),
                source_url=store.lesson_source_url(lesson_id, module),
                citation=_citation(module, lesson_id, title),
            )
        )

    return KnowledgeSearchResponse(
        query=q, k=k, available=True, status="ok", embedder=embedder.name,
        results=results,
    )
