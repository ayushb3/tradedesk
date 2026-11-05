"""Replay navigation: a trade's neighbours, and a day's trades for the day replay.

Plain row reads on ``trades`` (no hydration, no rule results) so stepping through
replays stays cheap. Training-context trades are excluded, as in ``list_trades``.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import trades as trades_t


def _as_utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _ordered_ids(engine: Engine) -> list[int]:
    stmt = (
        select(trades_t.c.id)
        .where(trades_t.c.context != "training")
        .order_by(trades_t.c.session_date, trades_t.c.open_ts_utc, trades_t.c.id)
    )
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(stmt)]


def neighbors(trade_id: int, engine: Engine | None = None) -> tuple[int | None, int | None] | None:
    """(prev_id, next_id) for a trade: same day in time order, then the adjacent day.
    None when the trade doesn't exist (or is a training trade)."""
    engine = engine or get_engine()
    init_db(engine)
    ids = _ordered_ids(engine)
    try:
        i = ids.index(trade_id)
    except ValueError:
        return None
    return (ids[i - 1] if i > 0 else None, ids[i + 1] if i + 1 < len(ids) else None)


def day_trades(day: date, engine: Engine | None = None) -> list[dict]:
    """The day's trades in time order: id, underlying symbol, contract label, open time."""
    engine = engine or get_engine()
    init_db(engine)
    stmt = (
        select(trades_t.c.id, trades_t.c.symbol, trades_t.c.contract_label, trades_t.c.open_ts_utc)
        .where(trades_t.c.session_date == day)
        .where(trades_t.c.context != "training")
        .order_by(trades_t.c.open_ts_utc, trades_t.c.id)
    )
    with engine.connect() as conn:
        return [
            {"trade_id": r.id, "symbol": (r.symbol or "").upper(),
             "contract_label": r.contract_label, "open_ts": _as_utc(r.open_ts_utc)}
            for r in conn.execute(stmt)
        ]


def day_symbols(trades: list[dict]) -> list[str]:
    """Underlyings traded that day, most trades first (ties: first traded first)."""
    counts = Counter(t["symbol"] for t in trades)
    first = {}
    for i, t in enumerate(trades):
        first.setdefault(t["symbol"], i)
    return sorted(counts, key=lambda s: (-counts[s], first[s]))
