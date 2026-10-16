"""Legacy-prefix table rename migration (TRADEDESK_LEGACY_TABLE_PREFIX)."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.db.connection import init_db


def _engine_with_legacy_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE old_lessons (id INTEGER PRIMARY KEY, title TEXT)"))
        conn.execute(text("INSERT INTO old_lessons (id, title) VALUES (1, 'kept')"))
        conn.execute(
            text("CREATE TABLE old_lesson_progress (lesson_id INTEGER PRIMARY KEY, status TEXT)")
        )
    return engine


def _tables(engine):
    return set(inspect(engine).get_table_names())


def test_noop_when_prefix_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADEDESK_LEGACY_TABLE_PREFIX", raising=False)
    engine = _engine_with_legacy_tables(tmp_path)
    init_db(engine)
    tables = _tables(engine)
    assert "old_lessons" in tables
    # The new table is created empty alongside; legacy data is untouched.
    with engine.begin() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM old_lessons")).scalar() == 1


def test_renames_legacy_tables_and_keeps_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEDESK_LEGACY_TABLE_PREFIX", "old_")
    engine = _engine_with_legacy_tables(tmp_path)
    init_db(engine)
    tables = _tables(engine)
    assert "knowledge_lessons" in tables
    assert "knowledge_lesson_progress" in tables
    assert "old_lessons" not in tables
    assert "old_lesson_progress" not in tables
    with engine.begin() as conn:
        assert conn.execute(text("SELECT title FROM knowledge_lessons")).scalar() == "kept"


def test_idempotent_and_never_overwrites_existing_new_table(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEDESK_LEGACY_TABLE_PREFIX", "old_")
    engine = _engine_with_legacy_tables(tmp_path)
    init_db(engine)
    init_db(engine)  # second run: nothing left to rename, no error

    # Re-create a legacy table while the new one exists: it must be left alone.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE old_lessons (id INTEGER PRIMARY KEY, title TEXT)"))
    init_db(engine)
    tables = _tables(engine)
    assert "old_lessons" in tables and "knowledge_lessons" in tables
    with engine.begin() as conn:
        assert conn.execute(text("SELECT title FROM knowledge_lessons")).scalar() == "kept"
