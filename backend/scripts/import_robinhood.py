"""Import Robinhood option fills from a read-only order export.

The export is the raw option-order JSON written by the separate read-only
exporter (``rh-export/export.py`` -> ``out/raw-option-orders-<start>.json``).
This script only reads that file: no network, no broker login, no order entry.

Usage
-----
    python backend/scripts/import_robinhood.py PATH/raw-option-orders-2026-09-29.json
    python backend/scripts/import_robinhood.py PATH.json --db some/other.db   # default: data/desk.db

Re-running with the same or an overlapping export is safe: fills are keyed by
Robinhood order id, so nothing duplicates, and trade notes/tags/stops carry
over the rebuild. Demo (mock) fills already in the target DB are cleared.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "backend"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("export", type=Path, help="raw-option-orders-<start>.json")
    p.add_argument("--db", help="SQLite path (default: TRADEDESK_DB_PATH / settings)")
    args = p.parse_args()

    from app.db.connection import get_engine
    from app.ingest.imports import import_fills
    from app.ingest.robinhood import RobinhoodExport

    source = RobinhoodExport.from_file(args.export)
    fills = source.parsed.fills
    if not fills:
        print("No executed single-leg option orders in this export. Nothing imported.")
        return 1

    engine = get_engine(args.db)
    result = import_fills(source, engine)

    et = ZoneInfo("America/New_York")
    days = Counter(f.ts.astimezone(et).date().isoformat() for f in fills)
    print(f"{len(fills)} fills in export ({min(days)} .. {max(days)}), "
          f"{result['fills_imported']} new, {result['trades_built']} trades rebuilt "
          f"-> {engine.url.database}")
    if source.parsed.skipped_multi_leg:
        print(f"warn: {source.parsed.skipped_multi_leg} multi-leg orders skipped (issue #15)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
