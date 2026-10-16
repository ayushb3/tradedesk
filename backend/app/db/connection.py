"""Engine factory + schema bootstrap for the local SQLite store.

Migration strategy (documented choice):
This is a single-user, local-only app (see PLAN §1). We do not carry the weight
of Alembic. Instead `init_db()` runs `metadata.create_all()` — which is a no-op
for tables that already exist — and records the current schema version in a
`schema_migrations` table. For a single-user local DB, create-all plus a version
stamp is sufficient and reversible (delete the file to reset). See
`migrations/README.md` for how to evolve the schema if that ever changes.

M6 column migration (task 6.0.2):
`trades.context TEXT NOT NULL DEFAULT 'real'` cannot be added by `create_all`
because the table already exists on real DBs. `_apply_column_migrations` runs
an idempotent `ALTER TABLE trades ADD COLUMN context …` on every `init_db` call,
guarded by a `PRAGMA table_info(trades)` check so it is a no-op after the first run.
"""
from __future__ import annotations

import os
import re

from sqlalchemy import Column, Integer, MetaData, Table, Text, create_engine, event, select, text
from sqlalchemy.engine import Engine

from app.config import get_settings

from .schema import metadata

# Bump when the schema in schema.py changes in a breaking way.
# v3 (M7b): added `option_bars_cache` table (new table, created by create_all).
# v4 (M8c): concepts, lesson_concepts, concept_links, knowledge_concept_runs (new tables).
# v5 (M8c PR-2): lesson_sheets (new table, created by create_all).
# v6 (M9a): agent_sessions, agent_messages (new tables, created by create_all).
SCHEMA_VERSION = 6

# Standalone metadata so the bookkeeping table is created alongside the app
# tables but is not part of the domain `metadata` exported to callers.
_migrations_metadata = MetaData()
schema_migrations = Table(
    "schema_migrations",
    _migrations_metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", Text, nullable=False),
)

# Cache engines by url so repeated get_engine() calls reuse the same pool.
_engines: dict[str, Engine] = {}


def _db_url(db_path: str | None = None) -> str:
    path = db_path if db_path is not None else get_settings().db_path
    return f"sqlite:///{path}"


def get_engine(db_path: str | None = None) -> Engine:
    """Return a (cached) SQLAlchemy engine for the configured SQLite file."""
    url = _db_url(db_path)
    engine = _engines.get(url)
    if engine is None:
        engine = create_engine(url, future=True)
        # WAL mode + a busy timeout so readers never block behind a writer and a
        # brief write-lock contention waits rather than erroring. This keeps the
        # Journal's trade/analytics reads responsive even while a slow live fill
        # import is committing. Applied per-connection via a connect hook.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        _engines[url] = engine
    return engine


def _apply_column_migrations(conn) -> None:
    """Idempotent column-level migrations for existing tables.

    SQLAlchemy's `create_all` only creates missing *tables*; it never alters
    existing ones. For new columns on existing tables we run ALTER TABLE
    statements guarded by PRAGMA table_info so they are no-ops after the
    first apply.

    M6 (task 6.0.2): trades.context TEXT NOT NULL DEFAULT 'real'
    - Existing rows get 'real' via the DEFAULT clause — safe for all real/sim
      trades inserted before M6. Training drill trades will have 'training'
      written explicitly on insert.
    - We do NOT map old `simulated=True` rows to context='sim' here: the sim
      flag stays the source of truth for pre-M6 sim trades (analytics reads
      `simulated` for its existing filter). New sim trades inserted via
      ingest/sim.py can set context='sim' in addition to simulated=True for
      forward compatibility, but existing data is untouched.
    """
    # Check whether trades.context already exists.
    rows = conn.execute(text("PRAGMA table_info(trades)")).fetchall()
    existing_cols = {r[1] for r in rows}  # column name is index 1

    if "context" not in existing_cols:
        conn.execute(
            text("ALTER TABLE trades ADD COLUMN context TEXT NOT NULL DEFAULT 'real'")
        )

    # fills.multiplier REAL NOT NULL DEFAULT 1.0 (options contract multiplier).
    # Added in the options P&L fix: stores the contract size (100 for equity
    # options, 1 for stocks) so gross_pnl = pnl_per_share * qty * multiplier.
    # Existing rows default to 1.0 — correct for all pre-fix stock and mock fills.
    fill_rows = conn.execute(text("PRAGMA table_info(fills)")).fetchall()
    fill_cols = {r[1] for r in fill_rows}

    if "multiplier" not in fill_cols:
        conn.execute(
            text("ALTER TABLE fills ADD COLUMN multiplier REAL NOT NULL DEFAULT 1.0")
        )

    # fills.option_type / .strike / .expiry — option contract identity columns.
    # Added in the per-contract grouping fix (PR #18).  Existing rows (stock fills
    # imported before this change) get NULL — correct, since stocks have no legs.
    if "option_type" not in fill_cols:
        conn.execute(text("ALTER TABLE fills ADD COLUMN option_type TEXT"))
    if "strike" not in fill_cols:
        conn.execute(text("ALTER TABLE fills ADD COLUMN strike REAL"))
    if "expiry" not in fill_cols:
        conn.execute(text("ALTER TABLE fills ADD COLUMN expiry TEXT"))

    # trades.contract_label — human-readable label for the instrument, e.g.
    # "SPXW 7500C 07/09".  Added in the same per-contract grouping fix.
    # Existing rows get NULL; the UI falls back to symbol when NULL.
    rows = conn.execute(text("PRAGMA table_info(trades)")).fetchall()
    trade_cols = {r[1] for r in rows}

    if "contract_label" not in trade_cols:
        conn.execute(text("ALTER TABLE trades ADD COLUMN contract_label TEXT"))


_KNOWLEDGE_PREFIX = "knowledge_"


def _rename_legacy_tables(engine: Engine) -> None:
    """Rename tables from an older naming scheme to their ``knowledge_*`` names.

    Only active when ``TRADEDESK_LEGACY_TABLE_PREFIX`` is set (unset = no-op).
    For every ``knowledge_<x>`` table in the schema, a table called
    ``<legacy prefix><x>`` is renamed to ``knowledge_<x>`` -- but only when the
    new table does not exist yet, so the migration is idempotent and never
    overwrites data. A user upgrading an old local DB sets the variable once.
    """
    prefix = os.getenv("TRADEDESK_LEGACY_TABLE_PREFIX", "").strip()
    if not prefix:
        return
    if not re.fullmatch(r"[A-Za-z0-9_]+", prefix):
        raise ValueError("TRADEDESK_LEGACY_TABLE_PREFIX must match [A-Za-z0-9_]+")

    with engine.begin() as conn:
        existing = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        for new_name in metadata.tables:
            if not new_name.startswith(_KNOWLEDGE_PREFIX):
                continue
            old_name = prefix + new_name[len(_KNOWLEDGE_PREFIX):]
            if old_name == new_name:
                continue
            if old_name in existing and new_name not in existing:
                conn.execute(text(f'ALTER TABLE "{old_name}" RENAME TO "{new_name}"'))
                existing.discard(old_name)
                existing.add(new_name)


def init_db(engine: Engine | None = None) -> Engine:
    """Create all tables if absent, apply column migrations, and stamp the
    schema version. Idempotent."""
    from datetime import datetime, timezone

    engine = engine or get_engine()

    _rename_legacy_tables(engine)
    metadata.create_all(engine)
    _migrations_metadata.create_all(engine)

    with engine.begin() as conn:
        _apply_column_migrations(conn)

        existing = conn.execute(
            select(schema_migrations.c.version).where(
                schema_migrations.c.version == SCHEMA_VERSION
            )
        ).first()
        if existing is None:
            conn.execute(
                schema_migrations.insert().values(
                    version=SCHEMA_VERSION,
                    applied_at=datetime.now(timezone.utc).isoformat(),
                )
            )
    return engine

