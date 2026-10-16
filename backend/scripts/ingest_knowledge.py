#!/usr/bin/env python
"""Knowledge base ingestion CLI (PRD-M8 §4.1, milestone M8a).

Reads your lesson transcript corpus, resolves each lesson's module from
the module files, chunks + embeds the text with a local (no-API)
embedder, and writes:

  - `knowledge_lessons` rows into the existing SQLite DB (source of truth), and
  - chunk vectors into the persisted vector index under `data/knowledge_chroma/`.

Re-runs are idempotent (content-hash per lesson): only new or changed lessons
are re-processed unless `--force`.

Usage
-----
    python backend/scripts/ingest_knowledge.py            # full ingest (idempotent)
    python backend/scripts/ingest_knowledge.py --force    # rebuild every lesson
    python backend/scripts/ingest_knowledge.py --limit 5  # smoke run (first 5 lessons)
    KNOWLEDGE_EMBEDDER=hashing python backend/scripts/ingest_knowledge.py   # no-torch fallback

Set ``TRADEDESK_LESSONS_DIR`` (or pass ``--lessons-dir``) to a directory of
``NNN_Title.txt`` lesson transcripts. The module grouping is read from
``Module_*.txt`` files in ``TRADEDESK_MODULES_DIR`` (or ``--modules-dir``);
default is a ``modules`` dir next to the lessons dir.

Exit codes
----------
  0  Ingest completed (some lessons may have been skipped as unchanged).
  1  No transcripts found / nothing resolved.
  2  Unexpected error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))

# Windows consoles default to cp1252 and choke on non-ASCII output; force UTF-8.
try:  # pragma: no cover - platform dependent
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest lesson transcripts into TradeDesk.")
    ap.add_argument("--lessons-dir", default=None)
    ap.add_argument("--modules-dir", default=None)
    ap.add_argument("--force", action="store_true", help="reprocess all lessons")
    ap.add_argument("--limit", type=int, default=None, help="cap lessons (smoke run)")
    args = ap.parse_args()

    from app.knowledge import store
    from app.knowledge.ingest import run_ingest

    tdir = Path(args.lessons_dir) if args.lessons_dir else store.lessons_dir()
    mdir = Path(args.modules_dir) if args.modules_dir else store.modules_dir()

    if not tdir.exists():
        print(f"ERROR: lessons dir not found: {tdir}", file=sys.stderr)
        print("Set TRADEDESK_LESSONS_DIR or pass --lessons-dir.", file=sys.stderr)
        return 1
    if not mdir.exists():
        print(f"ERROR: modules dir not found: {mdir}", file=sys.stderr)
        return 1

    print(f"Lessons:     {tdir}")
    print(f"Modules:     {mdir}")
    print(f"Vector dir:  {store.vector_dir()}")
    print("Ingesting... (first run downloads the local embedding model)")

    try:
        report = run_ingest(
            lessons_dir=tdir, modules_dir=mdir, force=args.force, limit=args.limit
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during ingest: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2

    print("\n=== Ingest report ===")
    print(report.summary())

    if report.resolved == 0:
        print("No lessons resolved — check the source directories.", file=sys.stderr)
        return 1
    if report.unresolved:
        print(
            f"\nWARNING: {len(report.unresolved)} lesson(s) unresolved to a module: "
            f"{report.unresolved}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
