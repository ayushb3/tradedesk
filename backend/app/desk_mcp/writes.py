"""Desk MCP write tools (issue #12): journal-only, whitelisted, every call audited.

Writes go through the same code the web app uses (``app.desk.plans``,
``app.desk.day_fields``, ``app.ingest.query.update_trade``, ``app.ingest.imports``).
Nothing here touches the network or the broker; ``import_robinhood`` only reads a
``raw-option-orders-*.json`` that the separate read-only exporter already wrote, and
only from inside the export directory.

Every call, accepted or rejected, lands in the ``audit`` table with sanitized args.
Responses carry counts and journal ids only, never an account or order id.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.schema import audit as audit_t
from app.db.schema import tags as tags_t
from app.db.schema import trade_tags as trade_tags_t
from app.db.schema import trades as trades_t
from app.desk import day_fields as desk_day_fields
from app.desk import plans as desk_plans
from app.desk.rules import REPO_ROOT, TZ_NAME
from app.ingest import imports as ingest_imports
from app.ingest import query as ingest_query
from app.ingest.robinhood import RobinhoodExport
from app.ingest.tags import create_tag
from app.models.trades import Grade, TagCreate, TradeUpdate

from .tools import Ctx, _meta, _parse_date

PT = ZoneInfo(TZ_NAME)
EXPORT_DIR_ENV = "TRADEDESK_EXPORT_DIR"
EXPORT_PATTERN = "raw-option-orders-*.json"
MAX_EXPORT_BYTES = 64 * 1024 * 1024
CALLER_TAG_PREFIX = "caller:"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")

# ------------------------------------------------------------------- auditing

_ID_PATTERNS = (
    re.compile(r"\brh-[A-Za-z0-9\-]+", re.I),
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
    re.compile(r"\b[0-9a-f]{24,}\b", re.I),
    re.compile(r"\b\d{8,}\b"),
)
_MAX_STR = 500


def _scrub(value):
    """Recursively redact anything shaped like an account/order id and cap long strings."""
    if isinstance(value, str):
        for pat in _ID_PATTERNS:
            value = pat.sub("[redacted-id]", value)
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_scrub(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _scrub(str(value))


def _record(ctx: Ctx, tool: str, args: dict, result: str) -> None:
    try:
        with ctx.engine.begin() as conn:
            conn.execute(audit_t.insert().values(
                ts=datetime.now(timezone.utc).replace(tzinfo=None), tool=tool,
                args_json=json.dumps(_scrub(args), sort_keys=True, default=str),
                result=_scrub(result), mode=ctx.mode,
            ))
    except Exception:  # noqa: BLE001 - never let logging mask the real outcome
        pass


def _audited(ctx: Ctx, tool: str, args: dict, fn):
    """Run ``fn``; log "ok" or "error: ..." and re-raise rejections."""
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001
        _record(ctx, tool, args, f"error: {exc}")
        raise
    _record(ctx, tool, args, "ok")
    return out


def audit_rows(ctx: Ctx, limit: int = 100) -> list[dict]:
    """Newest-first audit rows with a PT label (used by tests and by humans)."""
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(audit_t).order_by(audit_t.c.id.desc()).limit(limit)
        ).all()
    out = []
    for r in rows:
        m = r._mapping
        ts = m["ts"].replace(tzinfo=timezone.utc)
        out.append({
            "id": m["id"], "ts": ts.isoformat().replace("+00:00", "Z"),
            "ts_pt": ts.astimezone(PT).strftime("%Y-%m-%d %H:%M") + " PT",
            "tool": m["tool"], "args": json.loads(m["args_json"]),
            "result": m["result"], "mode": m["mode"],
        })
    return out


# ----------------------------------------------------------------- path guard

def export_dir() -> Path:
    env = os.environ.get(EXPORT_DIR_ENV)
    return Path(env) if env else REPO_ROOT.parent / "rh-export" / "out"


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


def resolve_export_file(path: str) -> Path:
    """Resolve ``path`` (symlinks, ``..``, case on Windows) and require it to sit inside
    the export directory and be a ``raw-option-orders-*.json`` file."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("path must be a non-empty string")
    try:
        base = export_dir().resolve(strict=True)
    except OSError as exc:
        raise ValueError("export directory does not exist; set TRADEDESK_EXPORT_DIR") from exc
    given = Path(path)
    candidate = given if given.is_absolute() else base / given
    try:
        real = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError("no such file inside the export directory") from exc
    base_s, real_s = _norm(base), _norm(real)
    try:
        inside = os.path.commonpath([base_s, real_s]) == base_s and real_s != base_s
    except ValueError:  # different drives
        inside = False
    if not inside:
        raise ValueError("path is outside the export directory")
    if not real.is_file():
        raise ValueError("path is not a file")
    if not fnmatch.fnmatch(real.name.lower(), EXPORT_PATTERN):
        raise ValueError(f"only {EXPORT_PATTERN} files can be imported")
    if real.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError("export file is too large")
    return real


# ---------------------------------------------------------------------- tools

def import_robinhood(ctx: Ctx, path: str) -> dict:
    """Import fills from an exported ``raw-option-orders-*.json`` (counts only)."""
    def run() -> dict:
        real = resolve_export_file(path)
        try:
            source = RobinhoodExport.from_file(real)
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
            raise ValueError("file is not a valid option-orders export") from exc
        fills = source.parsed.fills
        if not fills:
            raise ValueError("no executed single-leg option orders in this export")
        # The import path re-runs plan matching for every day that has plans.
        result = ingest_imports.import_fills(source, ctx.engine)
        days = Counter(f.ts.astimezone(PT).date().isoformat() for f in fills)
        return {
            **_meta(ctx),
            "fills_in_export": len(fills),
            "fills_added": result["fills_imported"],
            "trades": result["trades_built"],
            "days_touched": len(days),
            "first_day": min(days), "last_day": max(days),
            "multi_leg_skipped": source.parsed.skipped_multi_leg,
        }

    return _audited(ctx, "import_robinhood", {"path": path}, run)


def add_plan(
    ctx: Ctx, caller: str, contract: str, size_usd: float, max_loss_usd: float,
    target: str | None = None, note: str | None = None, session_date: str | None = None,
) -> dict:
    args = {"caller": caller, "contract": contract, "size_usd": size_usd,
            "max_loss_usd": max_loss_usd, "target": target, "note": note,
            "session_date": session_date}

    def run() -> dict:
        day = _parse_date(session_date) if session_date else None
        plan = desk_plans.add_plan(
            caller=caller, contract=contract, size_usd=size_usd, max_loss_usd=max_loss_usd,
            target=target, note=note or "", session_date=day, engine=ctx.engine,
        )
        return {
            **_meta(ctx), "plan": plan.model_dump(mode="json"),
            "matched": plan.matched_trade_id is not None,
        }

    return _audited(ctx, "add_plan", args, run)


def _clean_name(value, what: str) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.match(value.strip()):
        raise ValueError(f"{what} must be 1-40 chars of letters, digits, space, _ . -")
    return value.strip()


def _tag_ids(ctx: Ctx, names: list[str]) -> list[int]:
    ids = []
    for name in names:
        with ctx.engine.connect() as conn:
            tid = conn.execute(select(tags_t.c.id).where(tags_t.c.name == name)).scalar()
        if tid is None:
            tid = create_tag(TagCreate(name=name), ctx.engine).id
        ids.append(tid)
    return ids


def annotate_trade(
    ctx: Ctx, trade_id: int, caller: str | None = None, notes: str | None = None,
    grade: str | None = None, tags: list[str] | None = None, **unknown,
) -> dict:
    """Write caller / notes / grade / tags on one trade. Nothing else is writable.

    ``caller`` is stored as the tag ``caller:<name>`` (the trade tables have no caller
    column); it replaces any earlier caller tag. ``tags`` are added to existing tags."""
    args = {"trade_id": trade_id, "caller": caller, "notes": notes, "grade": grade,
            "tags": tags, **unknown}

    def run() -> dict:
        if unknown:
            raise ValueError(
                f"field(s) not writable: {', '.join(sorted(unknown))}. "
                "Allowed: caller, notes, grade, tags"
            )
        if isinstance(trade_id, bool) or not isinstance(trade_id, int):
            raise ValueError("trade_id must be an integer")
        if caller is None and notes is None and grade is None and tags is None:
            raise ValueError("nothing to write: pass caller, notes, grade or tags")
        if notes is not None and not isinstance(notes, str):
            raise ValueError("notes must be a string")
        g = None
        if grade is not None:
            try:
                g = Grade(grade)
            except ValueError:
                raise ValueError("grade must be A, B or C") from None
        wanted: list[str] = []
        if tags is not None:
            if not isinstance(tags, list):
                raise ValueError("tags must be a list of strings")
            for t in tags:
                if isinstance(t, str) and t.strip().lower().startswith(CALLER_TAG_PREFIX):
                    raise ValueError("use the caller field for caller tags")
                wanted.append(_clean_name(t, "tag"))
        caller_tag = CALLER_TAG_PREFIX + _clean_name(caller, "caller") if caller else None

        with ctx.engine.connect() as conn:
            exists = conn.execute(
                select(trades_t.c.id).where(trades_t.c.id == trade_id)
                .where(trades_t.c.simulated.is_(False)).where(trades_t.c.context != "training")
            ).first()
            if exists is None:
                raise ValueError(f"no trade with id {trade_id}")
            current_rows = conn.execute(
                select(tags_t.c.id, tags_t.c.name)
                .select_from(trade_tags_t.join(tags_t, trade_tags_t.c.tag_id == tags_t.c.id))
                .where(trade_tags_t.c.trade_id == trade_id)
            ).all()
        tag_ids = None
        if wanted or caller_tag:
            keep = [
                tid for tid, name in current_rows
                if not (caller_tag and name.startswith(CALLER_TAG_PREFIX))
            ]
            new = _tag_ids(ctx, wanted + ([caller_tag] if caller_tag else []))
            tag_ids = list(dict.fromkeys(keep + new))

        patch = TradeUpdate(notes=notes, grade=g, tag_ids=tag_ids)
        updated = ingest_query.update_trade(trade_id, patch, engine=ctx.engine)
        if updated is None:
            raise ValueError(f"no trade with id {trade_id}")
        written = [n for n, v in (("caller", caller), ("notes", notes), ("grade", grade),
                                  ("tags", tags)) if v is not None]
        return {
            **_meta(ctx), "trade_id": trade_id, "written": written,
            "grade": updated.enrichment.grade.value if updated.enrichment.grade else None,
            "tags": sorted(t.name for t in updated.tags),
        }

    return _audited(ctx, "annotate_trade", args, run)


def day_fields(ctx: Ctx, date: str, **fields) -> dict:
    """Upsert the day self-report; only the ``day_fields`` whitelist is accepted."""
    def run() -> dict:
        key = _parse_date(date)
        if not fields:
            raise ValueError("nothing to write: pass at least one field")
        row = desk_day_fields.upsert_day_fields(key, engine=ctx.engine, **fields)
        return {**_meta(ctx), "date": key.isoformat(), "day_fields": row.model_dump(mode="json")}

    return _audited(ctx, "day_fields", {"date": date, **fields}, run)
