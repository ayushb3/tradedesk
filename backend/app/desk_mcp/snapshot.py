"""Phone snapshot builder (issue #18). Pure data: no network, no writes.

The server cannot reach claude.ai, so ``push_phone_snapshot`` only RETURNS the
documents for the desk page's ``db``; Claude writes each one with its ArtifactData
tool. The phone copy is trimmed: ``trade_key`` is the only position id, no account or
order ids, no paths, and no lesson text or titles (only ``lesson_id``, the user's own
``reason``, ``pattern`` and ``available``; ``href`` points at localhost so it is dropped).
"""
from __future__ import annotations

import json
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import tools

MAX_DOC_BYTES = 200 * 1024  # each db doc must stay under 200 KB of JSON

INSTRUCTIONS = (
    "Write each item in `writes` to the desk page artifact with the ArtifactData tool: "
    "action set, collection, doc_id and data exactly as given."
)

_STATS = ("net", "entries", "positions", "win_pct", "avg_win", "avg_loss", "rr",
          "break_even_win_pct", "max_risk", "max_risk_x_stop", "max_risk_pct_account",
          "add_lower", "held_loser", "gave_back", "runner_paid", "deployed", "late_fills",
          "last_fill_pt", "stops", "n_wins", "n_losses", "n_closed", "sum_wins", "sum_losses")
_POSITION = ("trade_key", "label", "ticker", "type", "strike", "expiry", "status", "qty_in",
             "cost", "avg_in", "avg_out", "pnl", "r", "off_plan", "blown_max_loss")
_FILL = ("side", "qty", "px", "cash", "ts_pt", "order_type", "pnl")
_PLAN = ("created_pt", "caller", "symbol", "option_type", "strike", "size_usd",
         "max_loss_usd", "status", "late")
_FIELDS = ("why", "how_felt", "notes", "callers", "size_written_first", "max_loss_declared",
           "closed_by_window_end")
_LESSON = ("pattern", "lesson_id", "reason", "available")
_PATTERNS = ("days", "net", "green_days", "green_sum", "red_days", "red_sum", "n_closed",
             "n_wins", "n_losses", "win_pct", "avg_win", "avg_loss", "rr",
             "break_even_win_pct", "tag_counts", "late_days", "late_fills", "stops",
             "biggest_risk")


def _pick(src: dict, keys: tuple[str, ...]) -> dict:
    return {k: src.get(k) for k in keys}


def _lessons(items: list[dict]) -> list[dict]:
    return [_pick(x, _LESSON) for x in items]


def _position(p: dict, with_fills: bool) -> dict:
    out = _pick(p, _POSITION)
    out["tags"] = [_pick(t, ("code", "kind", "text")) for t in p.get("tags", [])]
    plan = p.get("plan")
    out["plan"] = {"caller": plan["caller"]} if plan else None
    if with_fills:
        out["fills"] = [_pick(f, _FILL) for f in p.get("fills", [])]
    return out


def _day_doc(day: dict, with_fills: bool = True) -> dict:
    doc = {
        "date": day["date"],
        "window": _pick(day["window"], ("start_pt", "end_pt")),
        "daily_stop": day["daily_stop"],
        "daily_target": day["daily_target"],
        "stats": _pick(day["stats"], _STATS),
        "positions": [_position(p, with_fills) for p in day["positions"]],
        "plans": [_pick(p, _PLAN) for p in day.get("plans", [])],
        "size_written_first": day.get("size_written_first"),
        "off_plan_count": day.get("off_plan_count", 0),
        "plans_not_taken": day.get("plans_not_taken", 0),
        "fields": _pick(day["fields"], _FIELDS) if day.get("fields") else None,
        "lessons": _lessons(day.get("lessons", [])),
    }
    if not with_fills:
        doc["fills_trimmed"] = True
    return doc


def _size(doc: dict) -> int:
    return len(json.dumps(doc, separators=(",", ":")).encode("utf-8"))


def _pt_label(now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz))
    return f"{local.hour}:{local.minute:02d} PT"


def push_phone_snapshot(
    ctx: tools.Ctx, date: str | None = None, now: datetime | None = None,
) -> dict:
    days = tools.list_days(ctx)["days"]
    if date is None:
        if not days:
            raise ValueError("no trading days to snapshot")
        date = days[0]["date"]
    day = tools.get_day(ctx, date)  # raises ValueError on a bad or empty date
    end = date_type.fromisoformat(date)
    now = now or datetime.now(timezone.utc)
    meta_t = tools._meta(ctx)

    doc = _day_doc(day)
    if _size(doc) >= MAX_DOC_BYTES:
        doc = _day_doc(day, with_fills=False)

    ls = tools.line_status(ctx)
    pat = tools.patterns(ctx, (end - timedelta(days=6)).isoformat(), date)
    week = {
        **_pick(pat, _PATTERNS),
        "date_from": (end - timedelta(days=6)).isoformat(),
        "date_to": date,
        "lessons": _lessons(pat.get("lessons", [])),
    }
    generated_pt = _pt_label(now, meta_t["tz"])
    generated_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    meta = {
        "generated_at": generated_at,
        "generated_pt": generated_pt,
        "latest_date": days[0]["date"] if days else date,
        "line": {
            **_pick(ls, ("amount", "start_date", "pnl", "left", "trades_counted")),
            "line_hit": ls["today"]["line_hit"],
        },
        "as_of": meta_t["as_of"],
    }
    writes = [
        {"collection": "snapshot", "doc_id": "meta", "data": meta},
        {"collection": "snapshot_days", "doc_id": date, "data": doc},
        {"collection": "snapshot_week", "doc_id": "current", "data": week},
    ]
    for w in writes:
        if _size(w["data"]) >= MAX_DOC_BYTES:
            raise ValueError(f"snapshot doc {w['collection']}/{w['doc_id']} exceeds 200 KB")
    return {
        **meta_t, "generated_at": generated_at, "generated_pt": generated_pt,
        "writes": writes, "instructions": INSTRUCTIONS,
    }
