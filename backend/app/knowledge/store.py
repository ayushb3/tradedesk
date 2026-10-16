"""SQLite persistence + paths for the knowledge base (PRD-M8 §5.1).

`knowledge_lessons` is the relational source of truth; vectors live in a rebuildable
index under ``data/knowledge_chroma/``. This module owns lesson read/writes and the
"is the knowledge base ingested / are embeddings available?" status that the API
returns when ingestion has never been run.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.db.connection import get_engine, init_db
from app.db.schema import (
    concept_links,
    concepts,
    knowledge_concept_runs,
    knowledge_lesson_progress,
    knowledge_lessons,
    lesson_concepts,
    lesson_sheets,
)

# config.py lives at backend/app/config.py; repo root is three parents up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

def lessons_dir() -> Path:
    """Directory of per-lesson ``NNN_Title.txt`` transcripts. Required: set
    ``TRADEDESK_LESSONS_DIR`` (there is deliberately no default location)."""
    env = os.getenv("TRADEDESK_LESSONS_DIR")
    return Path(env) if env else Path("lessons")


def modules_dir() -> Path:
    """Directory of ``Module_*.txt`` files that group lessons into modules.
    ``TRADEDESK_MODULES_DIR`` overrides; otherwise a ``modules`` dir that sits
    next to the lessons dir."""
    env = os.getenv("TRADEDESK_MODULES_DIR")
    if env:
        return Path(env)
    return lessons_dir().parent / "modules"


def lesson_source_url(lesson_id: int, module: str) -> str | None:
    """Link to the original lesson, built from the optional
    ``TRADEDESK_LESSON_URL_TEMPLATE`` setting (placeholders ``{lesson_id}`` and
    ``{module}``). None when no template is configured."""
    template = get_settings().lesson_url_template
    if not template:
        return None
    try:
        return template.format(lesson_id=lesson_id, module=module)
    except (KeyError, IndexError, ValueError):
        return None


def vector_dir() -> Path:
    """Persist dir for the vector index (PRD names ``data/knowledge_chroma/``)."""
    env = os.getenv("KNOWLEDGE_VECTOR_DIR")
    if env:
        return Path(env)
    return _REPO_ROOT / "data" / "knowledge_chroma"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def existing_hashes(engine: Engine | None = None) -> dict[int, str]:
    """{lesson_id: content_hash} for all ingested lessons (R5 idempotency)."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(knowledge_lessons.c.id, knowledge_lessons.c.content_hash)
        ).all()
    return {r[0]: r[1] for r in rows}


def upsert_lesson(
    *,
    lesson_id: int,
    module: str,
    seq: int,
    title: str,
    source_path: str,
    raw_text: str,
    chash: str,
    embedder: str,
    chunk_count: int,
    engine: Engine | None = None,
) -> None:
    engine = engine or get_engine()
    init_db(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(delete(knowledge_lessons).where(knowledge_lessons.c.id == lesson_id))
        conn.execute(
            knowledge_lessons.insert().values(
                id=lesson_id,
                module=module,
                seq=seq,
                title=title,
                source_path=source_path,
                raw_text=raw_text,
                content_hash=chash,
                embedder=embedder,
                chunk_count=chunk_count,
                ingested_at=now,
            )
        )


def lesson_count(engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        return int(conn.execute(select(func.count(knowledge_lessons.c.id))).scalar() or 0)


def module_counts(engine: Engine | None = None) -> dict[str, int]:
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(knowledge_lessons.c.module, func.count(knowledge_lessons.c.id)).group_by(
                knowledge_lessons.c.module
            )
        ).all()
    return {r[0]: int(r[1]) for r in rows}


def get_lesson_meta(lesson_id: int, engine: Engine | None = None) -> dict | None:
    """Lesson row without raw_text (used to hydrate search citations)."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        row = conn.execute(
            select(
                knowledge_lessons.c.id,
                knowledge_lessons.c.module,
                knowledge_lessons.c.seq,
                knowledge_lessons.c.title,
            ).where(knowledge_lessons.c.id == lesson_id)
        ).first()
    if row is None:
        return None
    return {
        "id": row[0],
        "module": row[1],
        "seq": row[2],
        "title": row[3],
    }


# --------------------------------------------------------------------------- #
# M8b — Lesson Reader helpers (issue #48)
# --------------------------------------------------------------------------- #


def list_lessons(engine: Engine | None = None) -> list[dict]:
    """All lesson metadata rows (no raw_text), for the module tree. Empty list
    when nothing is ingested — never raises."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                knowledge_lessons.c.id,
                knowledge_lessons.c.module,
                knowledge_lessons.c.seq,
                knowledge_lessons.c.title,
                knowledge_lessons.c.chunk_count,
            )
        ).all()
    return [
        {
            "id": r[0],
            "module": r[1],
            "seq": r[2],
            "title": r[3],
            "chunk_count": r[4],
        }
        for r in rows
    ]


def get_lesson_full(lesson_id: int, engine: Engine | None = None) -> dict | None:
    """Full lesson row *including* raw_text. None when the id doesn't exist."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        row = conn.execute(
            select(
                knowledge_lessons.c.id,
                knowledge_lessons.c.module,
                knowledge_lessons.c.seq,
                knowledge_lessons.c.title,
                knowledge_lessons.c.raw_text,
                knowledge_lessons.c.chunk_count,
            ).where(knowledge_lessons.c.id == lesson_id)
        ).first()
    if row is None:
        return None
    return {
        "id": row[0],
        "module": row[1],
        "seq": row[2],
        "title": row[3],
        "raw_text": row[4],
        "chunk_count": row[5],
    }


def lesson_exists(lesson_id: int, engine: Engine | None = None) -> bool:
    """True when a knowledge_lessons row exists for `lesson_id`."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        row = conn.execute(
            select(knowledge_lessons.c.id).where(knowledge_lessons.c.id == lesson_id)
        ).first()
    return row is not None


def get_progress(lesson_id: int, engine: Engine | None = None) -> dict | None:
    """{status, updated_at} for one lesson, or None when no progress row exists."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        row = conn.execute(
            select(
                knowledge_lesson_progress.c.status,
                knowledge_lesson_progress.c.updated_at,
            ).where(knowledge_lesson_progress.c.lesson_id == lesson_id)
        ).first()
    if row is None:
        return None
    return {"status": row[0], "updated_at": row[1]}


def get_progress_map(engine: Engine | None = None) -> dict[int, str]:
    """{lesson_id: status} for all lessons with a progress row. Missing lessons
    are absent (callers default them to 'unread')."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                knowledge_lesson_progress.c.lesson_id,
                knowledge_lesson_progress.c.status,
            )
        ).all()
    return {int(r[0]): r[1] for r in rows}


def upsert_progress(
    lesson_id: int, status: str, engine: Engine | None = None
) -> dict:
    """Idempotent upsert of a lesson's reading progress. Overwrites status +
    updated_at (UTC) when a row exists, inserts otherwise. Returns the new state.

    Callers must guarantee `lesson_id` references an existing knowledge_lessons row
    (the route enforces the 404); this does not create dangling progress checks."""
    engine = engine or get_engine()
    init_db(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            delete(knowledge_lesson_progress).where(
                knowledge_lesson_progress.c.lesson_id == lesson_id
            )
        )
        conn.execute(
            knowledge_lesson_progress.insert().values(
                lesson_id=lesson_id, status=status, updated_at=now
            )
        )
    return {"lesson_id": lesson_id, "status": status, "updated_at": now}


# --------------------------------------------------------------------------- #
# M8c — Concepts (issue #54, PR-1)
# --------------------------------------------------------------------------- #


def upsert_concept(
    *,
    slug: str,
    name: str,
    category: str,
    definition: str = "",
    aliases: list[str] | None = None,
    seeded: bool = False,
    engine: Engine | None = None,
) -> int:
    """Idempotent upsert of a concept by its unique slug. Returns the concept id.

    On an existing slug this overwrites name/category/definition/aliases/seeded
    and refreshes updated_at, preserving the original id and created_at. New rows
    stamp both created_at and updated_at.
    """
    engine = engine or get_engine()
    init_db(engine)
    now = datetime.now(timezone.utc)
    aliases_json = list(aliases) if aliases else []
    with engine.begin() as conn:
        existing = conn.execute(
            select(concepts.c.id).where(concepts.c.slug == slug)
        ).first()
        if existing is not None:
            cid = int(existing[0])
            conn.execute(
                concepts.update()
                .where(concepts.c.id == cid)
                .values(
                    name=name,
                    category=category,
                    definition=definition,
                    aliases_json=aliases_json,
                    seeded=seeded,
                    updated_at=now,
                )
            )
            return cid
        res = conn.execute(
            concepts.insert().values(
                slug=slug,
                name=name,
                category=category,
                definition=definition,
                aliases_json=aliases_json,
                seeded=seeded,
                created_at=now,
                updated_at=now,
            )
        )
        return int(res.inserted_primary_key[0])


def get_concept_id_by_slug(slug: str, engine: Engine | None = None) -> int | None:
    """Return the concept id for a slug, or None when it doesn't exist."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        row = conn.execute(
            select(concepts.c.id).where(concepts.c.slug == slug)
        ).first()
    return int(row[0]) if row is not None else None


def _resolve_concept_id(conn, slug: str) -> int | None:
    """In-transaction slug→id lookup. Reuses the caller's connection so writes
    stay in the same transaction (unlike the public own-connection
    ``get_concept_id_by_slug``)."""
    if not slug:
        return None
    row = conn.execute(
        select(concepts.c.id).where(concepts.c.slug == slug)
    ).first()
    return int(row[0]) if row is not None else None


def replace_lesson_concepts(
    lesson_id: int,
    items: list[dict],
    engine: Engine | None = None,
) -> int:
    """Delete-then-insert the lesson→concept edges for one lesson (idempotent).

    ``items`` is ``[{"slug": str, "weight": float}]``. Slugs that do not resolve
    to a concept row are silently skipped (drift guard belongs upstream, but this
    is defensive). Returns the number of edges written.
    """
    engine = engine or get_engine()
    init_db(engine)
    written = 0
    with engine.begin() as conn:
        conn.execute(
            delete(lesson_concepts).where(lesson_concepts.c.lesson_id == lesson_id)
        )
        for item in items:
            slug = item.get("slug")
            concept_id = _resolve_concept_id(conn, slug)
            if concept_id is None:
                continue
            conn.execute(
                lesson_concepts.insert().values(
                    lesson_id=lesson_id,
                    concept_id=concept_id,
                    weight=float(item.get("weight", 1.0)),
                )
            )
            written += 1
    return written


def upsert_concept_links(pairs: list[dict], engine: Engine | None = None) -> int:
    """Idempotent upsert of concept→concept relations.

    ``pairs`` is ``[{"a_slug": str, "b_slug": str, "relation": str}]``. Pairs
    whose slugs don't resolve, or that already exist for that relation, are
    skipped. Returns the number of newly inserted links.
    """
    engine = engine or get_engine()
    init_db(engine)
    written = 0
    with engine.begin() as conn:
        for pair in pairs:
            a_slug = pair.get("a_slug")
            b_slug = pair.get("b_slug")
            relation = pair.get("relation")
            if not a_slug or not b_slug or not relation or a_slug == b_slug:
                continue
            a_id = _resolve_concept_id(conn, a_slug)
            b_id = _resolve_concept_id(conn, b_slug)
            if a_id is None or b_id is None:
                continue
            exists = conn.execute(
                select(concept_links.c.a_id).where(
                    concept_links.c.a_id == a_id,
                    concept_links.c.b_id == b_id,
                    concept_links.c.relation == relation,
                )
            ).first()
            if exists is not None:
                continue
            conn.execute(
                concept_links.insert().values(
                    a_id=a_id, b_id=b_id, relation=relation
                )
            )
            written += 1
    return written


def mark_concept_run(
    lesson_id: int,
    content_hash: str,
    model: str,
    engine: Engine | None = None,
) -> None:
    """Record (or refresh) the resumability marker for a lesson's concept run."""
    engine = engine or get_engine()
    init_db(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            delete(knowledge_concept_runs).where(knowledge_concept_runs.c.lesson_id == lesson_id)
        )
        conn.execute(
            knowledge_concept_runs.insert().values(
                lesson_id=lesson_id,
                content_hash=content_hash,
                model=model,
                generated_at=now,
            )
        )


def concept_run_hashes(engine: Engine | None = None) -> dict[int, str]:
    """{lesson_id: content_hash} for every lesson whose concept pass has run."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(knowledge_concept_runs.c.lesson_id, knowledge_concept_runs.c.content_hash)
        ).all()
    return {int(r[0]): r[1] for r in rows}


def list_concepts(engine: Engine | None = None) -> list[dict]:
    """All concepts with their lesson_count (via join). Empty list when none.

    Each dict: slug, name, category, definition, aliases, lesson_count.
    """
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                concepts.c.id,
                concepts.c.slug,
                concepts.c.name,
                concepts.c.category,
                concepts.c.definition,
                concepts.c.aliases_json,
            )
        ).all()
        counts = dict(
            conn.execute(
                select(
                    lesson_concepts.c.concept_id,
                    func.count(lesson_concepts.c.lesson_id),
                ).group_by(lesson_concepts.c.concept_id)
            ).all()
        )
    return [
        {
            "slug": r[1],
            "name": r[2],
            "category": r[3],
            "definition": r[4] or "",
            "aliases": list(r[5]) if r[5] else [],
            "lesson_count": int(counts.get(r[0], 0)),
        }
        for r in rows
    ]


def get_concept_by_slug(slug: str, engine: Engine | None = None) -> dict | None:
    """Full concept detail: base fields + lessons (with weight) + related
    concepts (both link directions). None when the slug doesn't exist."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        base = conn.execute(
            select(
                concepts.c.id,
                concepts.c.slug,
                concepts.c.name,
                concepts.c.category,
                concepts.c.definition,
                concepts.c.aliases_json,
            ).where(concepts.c.slug == slug)
        ).first()
        if base is None:
            return None
        cid = int(base[0])

        lesson_rows = conn.execute(
            select(
                knowledge_lessons.c.id,
                knowledge_lessons.c.module,
                knowledge_lessons.c.seq,
                knowledge_lessons.c.title,
                lesson_concepts.c.weight,
            )
            .select_from(
                lesson_concepts.join(
                    knowledge_lessons, lesson_concepts.c.lesson_id == knowledge_lessons.c.id
                )
            )
            .where(lesson_concepts.c.concept_id == cid)
            .order_by(lesson_concepts.c.weight.desc(), knowledge_lessons.c.id)
        ).all()

        # Related concepts: this concept as `a` (outgoing) or `b` (incoming).
        out_rows = conn.execute(
            select(
                concepts.c.slug,
                concepts.c.name,
                concept_links.c.relation,
            )
            .select_from(
                concept_links.join(concepts, concept_links.c.b_id == concepts.c.id)
            )
            .where(concept_links.c.a_id == cid)
        ).all()
        in_rows = conn.execute(
            select(
                concepts.c.slug,
                concepts.c.name,
                concept_links.c.relation,
            )
            .select_from(
                concept_links.join(concepts, concept_links.c.a_id == concepts.c.id)
            )
            .where(concept_links.c.b_id == cid)
        ).all()

    lessons = [
        {
            "lesson_id": int(r[0]),
            "module": r[1],
            "seq": int(r[2]),
            "title": r[3],
            "weight": float(r[4]),
        }
        for r in lesson_rows
    ]

    related: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for r in list(out_rows) + list(in_rows):
        key = (r[0], r[2])
        if key in seen:
            continue
        seen.add(key)
        related.append({"slug": r[0], "name": r[1], "relation": r[2]})
    related.sort(key=lambda x: (x["relation"], x["name"]))

    return {
        "slug": base[1],
        "name": base[2],
        "category": base[3],
        "definition": base[4] or "",
        "aliases": list(base[5]) if base[5] else [],
        "lesson_count": len(lessons),
        "lessons": lessons,
        "related": related,
    }


def get_lesson_concepts(lesson_id: int, engine: Engine | None = None) -> list[dict]:
    """[{slug, name, category, weight}] for one lesson, ordered by weight desc.
    Empty list when the lesson has no concepts (or doesn't exist)."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(
                concepts.c.slug,
                concepts.c.name,
                concepts.c.category,
                lesson_concepts.c.weight,
            )
            .select_from(
                lesson_concepts.join(
                    concepts, lesson_concepts.c.concept_id == concepts.c.id
                )
            )
            .where(lesson_concepts.c.lesson_id == lesson_id)
            .order_by(lesson_concepts.c.weight.desc(), concepts.c.name)
        ).all()
    return [
        {"slug": r[0], "name": r[1], "category": r[2], "weight": float(r[3])}
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# M8c — Study sheets (issue #54, PR-2)
# --------------------------------------------------------------------------- #


def upsert_lesson_sheet(
    *,
    lesson_id: int,
    summary_md: str,
    rules_md: str,
    diagram_mermaid: str | None,
    quiz: list[dict] | None,
    content_hash: str,
    model: str,
    engine: Engine | None = None,
) -> None:
    """Delete-then-insert the study sheet for one lesson (idempotent).

    ``quiz`` is ``[{"q", "choices", "answer_idx", "explanation"}]``; it is
    serialized into the ``quiz_json`` JSON column. ``content_hash`` doubles as the
    resumability marker (a sheet row always exists once generated).
    """
    engine = engine or get_engine()
    init_db(engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            delete(lesson_sheets).where(lesson_sheets.c.lesson_id == lesson_id)
        )
        conn.execute(
            lesson_sheets.insert().values(
                lesson_id=lesson_id,
                summary_md=summary_md or "",
                rules_md=rules_md or "",
                diagram_mermaid=diagram_mermaid,
                quiz_json=list(quiz) if quiz else [],
                content_hash=content_hash,
                model=model,
                generated_at=now,
            )
        )


def sheet_hashes(engine: Engine | None = None) -> dict[int, str]:
    """{lesson_id: content_hash} for every lesson whose study sheet was generated.

    This is the resumability marker for the sheets pass — the presence of a row
    (and a matching hash) means the sheet is up to date."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        rows = conn.execute(
            select(lesson_sheets.c.lesson_id, lesson_sheets.c.content_hash)
        ).all()
    return {int(r[0]): r[1] for r in rows}


def get_lesson_sheet(lesson_id: int, engine: Engine | None = None) -> dict | None:
    """Study sheet for one lesson, or None when no sheet row exists.

    Returns {summary_md, rules_md, diagram_mermaid, quiz (deserialized list),
    generated_at, model}. ``quiz`` is always a list (empty when absent)."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        row = conn.execute(
            select(
                lesson_sheets.c.summary_md,
                lesson_sheets.c.rules_md,
                lesson_sheets.c.diagram_mermaid,
                lesson_sheets.c.quiz_json,
                lesson_sheets.c.generated_at,
                lesson_sheets.c.model,
            ).where(lesson_sheets.c.lesson_id == lesson_id)
        ).first()
    if row is None:
        return None
    return {
        "summary_md": row[0] or "",
        "rules_md": row[1] or "",
        "diagram_mermaid": row[2],
        "quiz": list(row[3]) if row[3] else [],
        "generated_at": row[4],
        "model": row[5],
    }
