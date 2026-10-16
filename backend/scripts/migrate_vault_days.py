"""Migrate the vault's day log (days.jsonl) into ``day_fields`` and check the line totals.

Reads two append-only JSONL files by path and never writes to them. Both use
"highest ``rev`` per key wins" (days keyed by ``date``, trades by ``id``).

For each date the newest day row is mapped onto the neutral ``day_fields`` names:

    max_loss_declared_usd   -> max_loss_declared
    platform_closed_by_8    -> closed_by_window_end
    size_written_first, stop_hit, target_hit, why, how_felt -> same name
    <name>_only (boolean)   -> callers (true -> ["mentor"], false -> [])
    notes                   -> notes
    any other key           -> appended to notes as "key: value"

``schema_version``, ``date``, ``rev`` and the derived ``traded`` / ``pnl_usd`` have no
home (the P&L lives in the trades table) and are not copied. Null values are skipped so
a migration never clears something already recorded in TradeDesk.

Then it prints two line totals, TradeDesk's ``line_status`` and the sum of the vault's
non-excluded trades since the loss-line start date, and exits 1 if they differ (with
the dates that differ). Idempotent: rows already equal to the target are left alone.

Usage (the DB is the one ``TRADEDESK_DB_PATH`` points at)
-----
    python -m scripts.migrate_vault_days --days PATH --trades PATH [--dry-run]

Exit codes: 0 migrated and totals match, 1 totals differ, 2 bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import trades as trades_t
from app.desk import days as desk_days
from app.desk.day_fields import get_day_fields, upsert_day_fields
from app.desk.rules import Rules, load_rules

CALLER_NAME = "mentor"
_RENAMED = {
    "max_loss_declared_usd": "max_loss_declared",
    "platform_closed_by_8": "closed_by_window_end",
}
_SAME = ("size_written_first", "stop_hit", "target_hit", "why", "how_felt")
_DROPPED = {"schema_version", "date", "rev", "traded", "pnl_usd"}
_FIELD_ORDER = ("why", "callers", "size_written_first", "max_loss_declared",
                "closed_by_window_end", "stop_hit", "target_hit", "how_felt", "notes")
_EPS = 0.005


class InputError(Exception):
    pass


def read_latest(path: Path, key: str) -> dict[str, dict]:
    """Highest ``rev`` per ``key``; first-seen order. Blank lines are ignored."""
    out: dict[str, dict] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise InputError(f"cannot read {Path(path).name}: {e.strerror}") from e
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise InputError(f"{Path(path).name}:{i}: invalid JSON ({e.msg})") from e
        if not isinstance(row, dict) or key not in row:
            raise InputError(f"{Path(path).name}:{i}: expected an object with {key!r}")
        k = row[key]
        if k not in out or (row.get("rev") or 0) > (out[k].get("rev") or 0):
            out[k] = row
    return out


def map_day(row: dict) -> dict:
    """Vault day row -> day_fields kwargs (non-null values only)."""
    fields: dict = {}
    extras: list[str] = []
    for k, v in row.items():
        if k in _DROPPED or k == "notes" or v is None:
            continue
        if k in _RENAMED:
            fields[_RENAMED[k]] = v
        elif k in _SAME:
            fields[k] = v
        elif k == "callers" and isinstance(v, list):
            fields["callers"] = v
        elif k.endswith("_only") and isinstance(v, bool):
            fields["callers"] = [CALLER_NAME] if v else []
        else:
            extras.append(f"{k}: {v}")
    notes = [str(row.get("notes") or "").strip(), *extras]
    joined = "\n".join(n for n in notes if n)
    if joined:
        fields["notes"] = joined
    return fields


def _differs(existing, fields: dict) -> bool:
    if existing is None:
        return True
    return any(getattr(existing, k) != v for k, v in fields.items())


def migrate_days(days: dict[str, dict], engine: Engine, dry_run: bool) -> tuple[int, int]:
    """Returns (rows written or that would be, rows already current)."""
    changed = unchanged = 0
    for date in sorted(days):
        fields = map_day(days[date])
        if _differs(get_day_fields(date, engine), fields):
            changed += 1
            if not dry_run:
                upsert_day_fields(date, engine, **{k: fields[k] for k in _FIELD_ORDER if k in fields})
        else:
            unchanged += 1
    return changed, unchanged


def vault_by_date(trades: dict[str, dict], start: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in trades.values():
        if t.get("excluded") or t.get("pnl_usd") is None or str(t["date"]) < start:
            continue
        out[str(t["date"])] = out.get(str(t["date"]), 0.0) + float(t["pnl_usd"])
    return out


def desk_by_date(engine: Engine, rules: Rules) -> dict[str, float]:
    """Same rows and filter as ``line_status``, grouped by session date."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(trades_t.c.session_date, trades_t.c.net_pnl)
            .where(desk_days._base_filter())
            .where(trades_t.c.status == "closed")
            .where(trades_t.c.session_date >= rules.loss_line.start_date)
        ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        k = r.session_date.isoformat()
        out[k] = out.get(k, 0.0) + float(r.net_pnl or 0.0)
    return out


def run(days_path, trades_path, dry_run: bool = False, engine: Engine | None = None,
        rules: Rules | None = None, out=print) -> int:
    days = read_latest(Path(days_path), "date")
    trades = read_latest(Path(trades_path), "id")
    engine = engine or get_engine()
    init_db(engine)
    rules = rules or load_rules()

    changed, unchanged = migrate_days(days, engine, dry_run)
    verb = "would migrate" if dry_run else "migrated"
    out(f"day rows in vault (latest rev per date): {len(days)}")
    out(f"{verb} {changed}, already current {unchanged}")

    start = rules.loss_line.start_date.isoformat()
    desk = desk_by_date(engine, rules)
    vault = vault_by_date(trades, start)
    desk_total = desk_days.line_status(rules=rules, engine=engine).pnl
    vault_total = sum(vault.values())
    out(f"line since {start}: TradeDesk {desk_total:.2f} | vault trades {vault_total:.2f}")
    diffs = [(d, desk.get(d, 0.0), vault.get(d, 0.0)) for d in sorted(set(desk) | set(vault))]
    diffs = [x for x in diffs if abs(x[1] - x[2]) >= _EPS]
    if abs(desk_total - vault_total) < _EPS and not diffs:
        out("totals match")
        return 0
    out(f"totals DIFFER on {len(diffs)} date(s) (delta = TradeDesk minus vault):")
    for d, a, b in diffs:
        out(f"  {d}  delta {a - b:+.2f}")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--days", required=True, type=Path, help="vault days.jsonl (read only)")
    p.add_argument("--trades", required=True, type=Path, help="vault trades.jsonl (read only)")
    p.add_argument("--dry-run", action="store_true", help="write nothing, report what would change")
    args = p.parse_args(argv)
    try:
        return run(args.days, args.trades, args.dry_run)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
