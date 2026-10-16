"""Safely copy a SQLite DB and print a comparison (trade count, per-day net).

Uses the sqlite3 backup API, so WAL-mode sources are copied consistently. The
source is opened read-only and never modified.

Usage
-----
    python backend/scripts/copy_db.py --from OLD.db --to data/desk.db

Refuses to overwrite an existing --to unless --force is given.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _summary(path: Path) -> tuple[int, dict[str, float]]:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        rows = con.execute(
            "SELECT session_date, ROUND(COALESCE(SUM(net_pnl), 0), 2) "
            "FROM trades GROUP BY session_date ORDER BY session_date"
        ).fetchall()
    finally:
        con.close()
    return n, {str(d): float(v) for d, v in rows}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--from", dest="src", required=True, type=Path)
    p.add_argument("--to", dest="dst", required=True, type=Path)
    p.add_argument("--force", action="store_true", help="overwrite an existing --to")
    a = p.parse_args()

    if not a.src.is_file():
        print(f"source not found: {a.src}", file=sys.stderr)
        return 2
    if a.dst.exists() and not a.force:
        print(f"{a.dst} exists; pass --force to overwrite", file=sys.stderr)
        return 2
    if a.src.resolve() == a.dst.resolve():
        print("source and destination are the same file", file=sys.stderr)
        return 2

    a.dst.parent.mkdir(parents=True, exist_ok=True)
    if a.dst.exists():
        a.dst.unlink()
    src = sqlite3.connect(f"file:{a.src.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(a.dst)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    (n1, d1), (n2, d2) = _summary(a.src), _summary(a.dst)
    print(f"from {a.src}: {n1} trades, {len(d1)} days")
    print(f"to   {a.dst}: {n2} trades, {len(d2)} days")
    for day in sorted(set(d1) | set(d2)):
        mark = "ok" if d1.get(day) == d2.get(day) else "MISMATCH"
        print(f"  {day}  {d1.get(day, 0.0):+.2f}  {d2.get(day, 0.0):+.2f}  {mark}")
    ok = n1 == n2 and d1 == d2
    print("MATCH: counts and per-day nets identical" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
