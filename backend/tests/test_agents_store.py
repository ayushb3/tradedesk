"""Agent-session persistence tests (M9a, issue #58).

Round-trips sessions + messages through `app.db.agents_store` on a throwaway
temp-file engine, exercising the citations/payload JSON columns, the newest-first
list filters, `get_latest_session_for_subject`, and the v6 schema landing the
`agent_sessions` / `agent_messages` tables.
"""
from __future__ import annotations

from app.db import agents_store as store
from app.db.connection import SCHEMA_VERSION, init_db
from app.db.schema import agent_messages, agent_sessions


# --------------------------------------------------------------------------- #
# Round-trip: create → append → get
# --------------------------------------------------------------------------- #

def test_session_message_round_trip(temp_engine):
    session = store.create_session("critic", "trade:42", engine=temp_engine)
    assert session["kind"] == "critic"
    assert session["subject_ref"] == "trade:42"
    assert session["started_at"] is not None

    citations = [
        {"lesson_id": 7, "citation": "Module 7 · Lesson 007 — Support Levels"},
    ]
    payload = {
        "verdict_by_phase": {"setup": "ok", "entry": "", "management": "", "exit": ""},
        "rule_checks": [],
        "prescription": {"drill_id": None, "readings": []},
    }
    store.append_message(
        session["id"], "agent", "**Setup** — ok",
        citations=citations, payload=payload, engine=temp_engine,
    )

    got = store.get_session(session["id"], engine=temp_engine)
    assert got is not None
    assert len(got["messages"]) == 1
    msg = got["messages"][0]
    assert msg["role"] == "agent"
    assert msg["content_md"] == "**Setup** — ok"
    # Parsed back into a real list / dict (not the raw *_json TEXT).
    assert isinstance(msg["citations"], list)
    assert msg["citations"] == citations
    assert isinstance(msg["payload"], dict)
    assert msg["payload"]["verdict_by_phase"]["setup"] == "ok"


def test_null_citations_and_payload_default(temp_engine):
    """A user message with no citations/payload reads back [] / None (not raw NULL)."""
    session = store.create_session("critic", "trade:1", engine=temp_engine)
    store.append_message(session["id"], "user", "why?", engine=temp_engine)

    got = store.get_session(session["id"], engine=temp_engine)
    msg = got["messages"][0]
    assert msg["citations"] == []
    assert msg["payload"] is None


def test_messages_ordered_by_ts_then_id(temp_engine):
    session = store.create_session("critic", "trade:5", engine=temp_engine)
    for i in range(3):
        store.append_message(session["id"], "agent", f"msg {i}", engine=temp_engine)

    got = store.get_session(session["id"], engine=temp_engine)
    contents = [m["content_md"] for m in got["messages"]]
    assert contents == ["msg 0", "msg 1", "msg 2"]


def test_get_session_unknown_id_is_none(temp_engine):
    init_db(temp_engine)
    assert store.get_session(99999, engine=temp_engine) is None


# --------------------------------------------------------------------------- #
# list_sessions — filters, message_count, newest-first
# --------------------------------------------------------------------------- #

def test_list_sessions_filters_and_counts(temp_engine):
    critic_a = store.create_session("critic", "trade:1", engine=temp_engine)
    critic_b = store.create_session("critic", "trade:2", engine=temp_engine)
    tutor = store.create_session("tutor", "trade:1", engine=temp_engine)

    store.append_message(critic_a["id"], "agent", "a1", engine=temp_engine)
    store.append_message(critic_a["id"], "user", "a2", engine=temp_engine)
    store.append_message(critic_b["id"], "agent", "b1", engine=temp_engine)

    # Filter by subject_ref.
    by_subject = store.list_sessions(subject_ref="trade:1", engine=temp_engine)
    assert {s["id"] for s in by_subject} == {critic_a["id"], tutor["id"]}

    # Filter by kind.
    by_kind = store.list_sessions(kind="critic", engine=temp_engine)
    assert {s["id"] for s in by_kind} == {critic_a["id"], critic_b["id"]}

    # Both filters (AND).
    both = store.list_sessions(subject_ref="trade:1", kind="critic", engine=temp_engine)
    assert [s["id"] for s in both] == [critic_a["id"]]

    # message_count is correct (0 for the tutor session, 2 for critic_a).
    counts = {s["id"]: s["message_count"] for s in store.list_sessions(engine=temp_engine)}
    assert counts[critic_a["id"]] == 2
    assert counts[critic_b["id"]] == 1
    assert counts[tutor["id"]] == 0


def test_list_sessions_newest_first(temp_engine):
    first = store.create_session("critic", "trade:1", engine=temp_engine)
    second = store.create_session("critic", "trade:2", engine=temp_engine)
    third = store.create_session("critic", "trade:3", engine=temp_engine)

    ids = [s["id"] for s in store.list_sessions(kind="critic", engine=temp_engine)]
    # started_at ties collapse to the same timestamp in-test, so the id-desc
    # tiebreak governs: newest (highest id) first.
    assert ids == [third["id"], second["id"], first["id"]]


# --------------------------------------------------------------------------- #
# get_latest_session_for_subject
# --------------------------------------------------------------------------- #

def test_get_latest_session_for_subject(temp_engine):
    old = store.create_session("critic", "trade:9", engine=temp_engine)
    new = store.create_session("critic", "trade:9", engine=temp_engine)
    store.append_message(new["id"], "agent", "newest", engine=temp_engine)

    latest = store.get_latest_session_for_subject("trade:9", engine=temp_engine)
    assert latest is not None
    assert latest["id"] == new["id"]
    assert latest["messages"][0]["content_md"] == "newest"

    # kind filter that matches nothing → None.
    assert store.get_latest_session_for_subject(
        "trade:9", kind="tutor", engine=temp_engine
    ) is None

    # unknown subject → None.
    assert store.get_latest_session_for_subject(
        "trade:404", engine=temp_engine
    ) is None
    # `old` still exists but is not the latest.
    assert old["id"] != new["id"]


# --------------------------------------------------------------------------- #
# Schema v6 — tables exist on a fresh isolated_db
# --------------------------------------------------------------------------- #

def test_schema_version_is_9():
    assert SCHEMA_VERSION == 9


def test_agent_tables_exist_on_fresh_db(temp_engine):
    from sqlalchemy import inspect

    init_db(temp_engine)
    tables = set(inspect(temp_engine).get_table_names())
    assert agent_sessions.name in tables
    assert agent_messages.name in tables
