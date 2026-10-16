"""Store for the per-day self-report (`day_fields`, issue #7).

`upsert_day_fields` accepts only whitelisted fields, merges into any existing
row (unspecified fields are left alone; an explicit None clears a nullable
field) and stamps ``updated_at``.
"""
from __future__ import annotations

import json
from datetime import date as date_type
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import day_fields as day_fields_t
from app.models.desk import DayFields

ALLOWED_FIELDS = frozenset(
    {
        "why", "callers", "size_written_first", "max_loss_declared",
        "closed_by_window_end", "stop_hit", "target_hit", "how_felt", "notes",
    }
)
_TEXT = {"why", "how_felt", "notes"}
_BOOL = {"size_written_first", "closed_by_window_end", "stop_hit", "target_hit"}


def _key(d: date_type | str) -> str:
    if isinstance(d, date_type):
        return d.isoformat()
    return date_type.fromisoformat(d).isoformat()


def _to_model(row) -> DayFields:
    m = row._mapping
    return DayFields(
        date=m["date"],
        why=m["why"],
        callers=json.loads(m["callers_json"] or "[]"),
        size_written_first=m["size_written_first"],
        max_loss_declared=m["max_loss_declared"],
        closed_by_window_end=m["closed_by_window_end"],
        stop_hit=m["stop_hit"],
        target_hit=m["target_hit"],
        how_felt=m["how_felt"],
        notes=m["notes"],
        updated_at=m["updated_at"].replace(tzinfo=timezone.utc),
    )


def _validate(name: str, value):
    if name in _TEXT:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value
    if name == "callers":
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise ValueError("callers must be a list of strings")
        return json.dumps(list(value))
    if name in _BOOL:
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{name} must be a bool or None")
        return value
    # max_loss_declared
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("max_loss_declared must be a number or None")
    return float(value)


def get_day_fields(date: date_type | str, engine: Engine | None = None) -> DayFields | None:
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        row = conn.execute(
            select(day_fields_t).where(day_fields_t.c.date == _key(date))
        ).first()
    return _to_model(row) if row else None


def upsert_day_fields(
    date: date_type | str, /, engine: Engine | None = None, **fields
) -> DayFields:
    unknown = sorted(set(fields) - ALLOWED_FIELDS)
    if unknown:
        raise ValueError(
            f"Unknown day_fields field(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(ALLOWED_FIELDS))}"
        )
    key = _key(date)
    values = {
        ("callers_json" if n == "callers" else n): _validate(n, v) for n, v in fields.items()
    }
    values["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

    engine = engine or get_engine()
    init_db(engine)
    with engine.begin() as conn:
        exists = conn.execute(
            select(day_fields_t.c.date).where(day_fields_t.c.date == key)
        ).first()
        if exists:
            conn.execute(
                day_fields_t.update().where(day_fields_t.c.date == key).values(**values)
            )
        else:
            conn.execute(day_fields_t.insert().values(date=key, **values))
    result = get_day_fields(key, engine)
    assert result is not None
    return result
