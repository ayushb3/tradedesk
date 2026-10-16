# Migrations

This app is single-user and local-only (SQLite file, see `docs/PLAN.md` §1),
so it deliberately does **not** use Alembic.

## How it works
- `app/db/schema.py` is the single source of truth for the schema.
- `init_db()` (in `app/db/connection.py`) calls `metadata.create_all()`, which
  creates any missing tables and is a no-op for existing ones. It also records
  `SCHEMA_VERSION` in a `schema_migrations` bookkeeping table.

## Evolving the schema
For additive changes (new tables, new nullable columns), just edit `schema.py`;
`create_all()` picks up new tables on next `init_db()`.

For anything `create_all()` cannot do automatically (dropping/altering columns,
backfills), add a numbered SQL file here (e.g. `002_add_x.sql`), bump
`SCHEMA_VERSION`, and apply it manually — or, since this is a disposable local
DB, delete `tradedesk.db` and let `init_db()` rebuild from scratch.
