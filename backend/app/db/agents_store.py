"""Agent-session persistence (M9a, issue #58).

Backs the agent conversation flows (the critic being the first `kind`). Mirrors
the store idioms used across ``app.db`` and ``app.knowledge.store``:

- ``engine = engine or get_engine(); init_db(engine)`` at the top of every fn.
- Timestamps are UTC. ``agent_sessions.started_at`` / ``agent_messages.ts`` are
  ``DateTime`` columns; SQLite round-trips them to naive UTC ``datetime`` on read,
  so we write ``datetime.now(timezone.utc)`` stripped of tzinfo (matching
  ``training_store._utcnow()``).
- JSON payloads live in TEXT columns, serialized via ``json.dumps`` on write and
  parsed via ``json.loads`` on read.

The message dicts returned everywhere use the keys ``"citations"`` (parsed list,
default ``[]``) and ``"payload"`` (parsed dict, default ``None``) — NOT the raw
``*_json`` column names — because the API layer's ``AgentMessageResponse`` expects
``citations`` and ``payload``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import agent_messages, agent_sessions


def _utcnow() -> datetime:
    """UTC now as a naive datetime (SQLite `DateTime` columns are tz-naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row_to_message(row) -> dict:
    """Map an agent_messages row to the API-facing dict (parsed citations/payload)."""
    m = row._mapping
    citations_raw = m["citations_json"]
    payload_raw = m["payload_json"]
    return {
        "id": m["id"],
        "session_id": m["session_id"],
        "role": m["role"],
        "content_md": m["content_md"],
        "citations": json.loads(citations_raw) if citations_raw else [],
        "payload": json.loads(payload_raw) if payload_raw else None,
        "ts": m["ts"],
    }


def create_session(
    kind: str,
    subject_ref: str,
    *,
    engine: Engine | None = None,
) -> dict:
    """Insert a new agent session (started_at=now UTC). Returns the session row."""
    engine = engine or get_engine()
    init_db(engine)

    now = _utcnow()
    with engine.begin() as conn:
        res = conn.execute(
            agent_sessions.insert().values(
                kind=kind,
                subject_ref=subject_ref,
                started_at=now,
            )
        )
        session_id = int(res.inserted_primary_key[0])

    return {
        "id": session_id,
        "kind": kind,
        "subject_ref": subject_ref,
        "started_at": now,
    }


def append_message(
    session_id: int,
    role: str,
    content_md: str,
    *,
    citations: list[dict] | None = None,
    payload: dict | None = None,
    engine: Engine | None = None,
) -> dict:
    """Append a message to a session (ts=now UTC).

    ``citations`` (a list of KnowledgeSearchHit dicts) and ``payload`` (a structured
    CriticResult dict) are both nullable and serialized into their ``*_json`` TEXT
    columns. Returns the persisted message dict with parsed ``citations`` (list,
    default ``[]``) and ``payload`` (dict|None).
    """
    engine = engine or get_engine()
    init_db(engine)

    now = _utcnow()
    citations_json = json.dumps(citations) if citations is not None else None
    payload_json = json.dumps(payload) if payload is not None else None

    with engine.begin() as conn:
        res = conn.execute(
            agent_messages.insert().values(
                session_id=session_id,
                role=role,
                content_md=content_md,
                citations_json=citations_json,
                payload_json=payload_json,
                ts=now,
            )
        )
        message_id = int(res.inserted_primary_key[0])

    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content_md": content_md,
        "citations": citations if citations is not None else [],
        "payload": payload,
        "ts": now,
    }


def get_session(session_id: int, *, engine: Engine | None = None) -> dict | None:
    """Return the session hydrated with its messages (ordered ts asc, then id).

    None when the session id is unknown.
    """
    engine = engine or get_engine()
    init_db(engine)

    with engine.connect() as conn:
        row = conn.execute(
            select(agent_sessions).where(agent_sessions.c.id == session_id)
        ).first()
        if row is None:
            return None

        msg_rows = conn.execute(
            select(agent_messages)
            .where(agent_messages.c.session_id == session_id)
            .order_by(agent_messages.c.ts.asc(), agent_messages.c.id.asc())
        ).fetchall()

    m = row._mapping
    return {
        "id": m["id"],
        "kind": m["kind"],
        "subject_ref": m["subject_ref"],
        "started_at": m["started_at"],
        "messages": [_row_to_message(r) for r in msg_rows],
    }


def list_sessions(
    subject_ref: str | None = None,
    kind: str | None = None,
    *,
    engine: Engine | None = None,
) -> list[dict]:
    """Session summaries newest-first: {id, kind, subject_ref, started_at,
    message_count}. Both filters optional (AND when both given)."""
    engine = engine or get_engine()
    init_db(engine)

    msg_count = (
        select(
            agent_messages.c.session_id,
            func.count(agent_messages.c.id).label("cnt"),
        )
        .group_by(agent_messages.c.session_id)
        .subquery()
    )

    stmt = (
        select(
            agent_sessions.c.id,
            agent_sessions.c.kind,
            agent_sessions.c.subject_ref,
            agent_sessions.c.started_at,
            func.coalesce(msg_count.c.cnt, 0),
        )
        .select_from(
            agent_sessions.outerjoin(
                msg_count, msg_count.c.session_id == agent_sessions.c.id
            )
        )
    )
    if subject_ref is not None:
        stmt = stmt.where(agent_sessions.c.subject_ref == subject_ref)
    if kind is not None:
        stmt = stmt.where(agent_sessions.c.kind == kind)
    stmt = stmt.order_by(agent_sessions.c.started_at.desc(), agent_sessions.c.id.desc())

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    return [
        {
            "id": r[0],
            "kind": r[1],
            "subject_ref": r[2],
            "started_at": r[3],
            "message_count": int(r[4]),
        }
        for r in rows
    ]


def get_latest_session_for_subject(
    subject_ref: str,
    kind: str | None = None,
    *,
    engine: Engine | None = None,
) -> dict | None:
    """Most recent session (started_at desc, id desc) for a subject_ref, optionally
    filtered by kind, hydrated like get_session. None when there is none."""
    engine = engine or get_engine()
    init_db(engine)

    stmt = select(agent_sessions.c.id).where(
        agent_sessions.c.subject_ref == subject_ref
    )
    if kind is not None:
        stmt = stmt.where(agent_sessions.c.kind == kind)
    stmt = stmt.order_by(
        agent_sessions.c.started_at.desc(), agent_sessions.c.id.desc()
    ).limit(1)

    with engine.connect() as conn:
        row = conn.execute(stmt).first()

    if row is None:
        return None
    return get_session(int(row[0]), engine=engine)
