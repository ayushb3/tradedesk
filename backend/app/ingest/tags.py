"""Tag catalog CRUD + default seeding (M3).

Backs the tag endpoints:
    list_tags() -> list[Tag]
    create_tag(TagCreate) -> Tag
    update_tag(tag_id, TagUpdate) -> Tag | None

`seed_default_tags()` inserts the strategy starter set idempotently. It runs
lazily on the first `list_tags()` call so a fresh DB always exposes the defaults
without needing an explicit bootstrap step.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import tags as tags_t
from app.models.trades import Tag, TagCreate, TagUpdate

DEFAULT_TAGS = ["PDH-break", "PMH-break", "OR-breakout", "VWAP-reclaim", "EMA-pullback"]


def _row_to_tag(m) -> Tag:
    return Tag(
        id=m["id"],
        name=m["name"],
        description=m["description"],
        active=bool(m["active"]),
    )


def seed_default_tags(engine: Engine | None = None) -> None:
    """Insert any missing default tags. Idempotent."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        existing = {
            r[0] for r in conn.execute(select(tags_t.c.name)).fetchall()
        }
        for name in DEFAULT_TAGS:
            if name not in existing:
                conn.execute(
                    tags_t.insert().values(name=name, description=None, active=True)
                )


def list_tags(engine: Engine | None = None) -> list[Tag]:
    """All tags (default set seeded lazily on first call), ordered by name."""
    engine = engine or get_engine()
    seed_default_tags(engine)
    with engine.connect() as conn:
        rows = conn.execute(select(tags_t).order_by(tags_t.c.name)).fetchall()
    return [_row_to_tag(r._mapping) for r in rows]


def create_tag(payload: TagCreate, engine: Engine | None = None) -> Tag:
    """Create a new tag and return it."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        res = conn.execute(
            tags_t.insert().values(
                name=payload.name, description=payload.description, active=True
            )
        )
        tag_id = int(res.inserted_primary_key[0])
        row = conn.execute(select(tags_t).where(tags_t.c.id == tag_id)).first()
    return _row_to_tag(row._mapping)


def update_tag(
    tag_id: int, payload: TagUpdate, engine: Engine | None = None
) -> Tag | None:
    """Patch a tag's provided fields; return the updated tag or None."""
    engine = engine or get_engine()
    init_db(engine)
    values: dict = {}
    if payload.name is not None:
        values["name"] = payload.name
    if payload.description is not None:
        values["description"] = payload.description
    if payload.active is not None:
        values["active"] = payload.active

    with engine.begin() as conn:
        row = conn.execute(select(tags_t).where(tags_t.c.id == tag_id)).first()
        if row is None:
            return None
        if values:
            conn.execute(
                tags_t.update().where(tags_t.c.id == tag_id).values(**values)
            )
            row = conn.execute(select(tags_t).where(tags_t.c.id == tag_id)).first()
    return _row_to_tag(row._mapping)
