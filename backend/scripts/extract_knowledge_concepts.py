#!/usr/bin/env python
"""Course concept-extraction CLI (PRD-M8 §R6, milestone M8c, PR-1).

Runs the offline concept-extraction pass over the ingested lessons: seeds the
controlled concept vocabulary, then for each lesson calls the LLM generation seam
(`TRADEDESK_LLM`) to tag it with the concepts it teaches. The output is
drift-guarded to the seed vocabulary — the model can never invent a concept.

Resumable (content-hash per lesson): unchanged lessons are skipped unless
``--force``. Under ``TRADEDESK_LLM=none`` (the default) the seam degrades to
nothing: every lesson is counted as ``skipped_no_llm``, no concepts are written,
and the script exits 0.

Usage
-----
    python backend/scripts/extract_knowledge_concepts.py             # full pass (idempotent)
    python backend/scripts/extract_knowledge_concepts.py --force     # reprocess all lessons
    python backend/scripts/extract_knowledge_concepts.py --limit 3   # smoke run (first 3)
    TRADEDESK_LLM=claude-cli python backend/scripts/extract_knowledge_concepts.py --limit 3

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
        description="Extract course concepts from ingested lessons into TradeDesk."
    )
    ap.add_argument("--force", action="store_true", help="reprocess all lessons")
    ap.add_argument("--limit", type=int, default=None, help="cap lessons (smoke run)")
    args = ap.parse_args()

    from app.config import get_settings
    from app.knowledge import store
    from app.knowledge.concepts_pass import run_concept_extraction

    if store.lesson_count() == 0:
        print(
            "ERROR: no lessons ingested. Run `make knowledge-ingest` first.",
            file=sys.stderr,
        )
        return 1

    print(f"LLM backend: {get_settings().llm}")
    print("Extracting concepts... (writes nothing under TRADEDESK_LLM=none)")

    try:
        report = run_concept_extraction(force=args.force, limit=args.limit)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during concept extraction: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 2

    print("\n=== Concept pass report ===")
    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
