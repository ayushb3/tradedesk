#!/usr/bin/env python
"""Course study-sheet generation CLI (PRD-M8 §R7, milestone M8c, PR-2).

Runs the offline study-sheet generation pass over the ingested lessons: for
each lesson it calls the LLM generation seam (`TRADEDESK_LLM`) to distil the
transcript into a study sheet (summary / rules / optional Mermaid diagram / quiz).

Resumable (content-hash per lesson): unchanged lessons are skipped unless
``--force``. Under ``TRADEDESK_LLM=none`` (the default) the seam degrades to
nothing: every lesson is counted as ``skipped_no_llm``, no sheets are written,
and the script exits 0.

Usage
-----
    python backend/scripts/generate_knowledge_sheets.py             # full pass (idempotent)
    python backend/scripts/generate_knowledge_sheets.py --force     # regenerate all lessons
    python backend/scripts/generate_knowledge_sheets.py --limit 3   # smoke run (first 3)
    TRADEDESK_LLM=claude-cli python backend/scripts/generate_knowledge_sheets.py --limit 3

Exit codes
----------
  0  Pass completed (some lessons may have been skipped, incl. all under llm=none).
  1  No lessons ingested — run `make knowledge-ingest` first.
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
    ap = argparse.ArgumentParser(
        description="Generate course study sheets from ingested lessons into TradeDesk."
    )
    ap.add_argument("--force", action="store_true", help="regenerate all lessons")
    ap.add_argument("--limit", type=int, default=None, help="cap lessons (smoke run)")
    args = ap.parse_args()

    from app.config import get_settings
    from app.knowledge import store
    from app.knowledge.sheets_pass import run_sheet_generation

    if store.lesson_count() == 0:
        print(
            "ERROR: no lessons ingested. Run `make knowledge-ingest` first.",
            file=sys.stderr,
        )
        return 1

    print(f"LLM backend: {get_settings().llm}")
    print("Generating study sheets... (writes nothing under TRADEDESK_LLM=none)")

    try:
        report = run_sheet_generation(force=args.force, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during study-sheet generation: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2

    print("\n=== Study-sheet pass report ===")
    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
