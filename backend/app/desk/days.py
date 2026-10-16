"""Desk day numbers from the DB: trades + fills -> Fill Ledger inputs -> API data.

A "position" is one TradeDesk trade (FIFO round-trip). Days are keyed on the
trade's ``session_date``. Simulated and training trades are excluded.

Fill cash matches how ``ingest/robinhood.py`` stored fees: a buy moves
``-(qty * px * mult + fees)``, a sell ``+(qty * px * mult - fees)``, so a closed
position's ledger P&L equals the trade's ``net_pnl`` and a day's net equals the
sum of its trades' ``net_pnl``.

Stops: the export only keeps orders that executed, so ``filled`` = executed stop
orders and ``moves`` = trailed replacements recorded on them. A trailed chain is
one stop, so ``placed`` equals ``filled`` (the stop chains we can see), and
``pulled`` is 0 (a stop cancelled with no fill never reaches the DB).

``as_of``: no import timestamp is stored, so it is the newest real fill's
``ts_utc`` (None when there are no fills).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import fills as fills_t
from app.db.schema import trade_fills as trade_fills_t
from app.db.schema import trades as trades_t
from app.desk import ledger
from app.desk.day_fields import get_day_fields
from app.desk.ledger import DayInput, DayResult, Fill, Position, StopUsage
from app.desk.rules import Rules, account_size, load_rules, window_bounds
from app.models.desk import DayFields

UTC = timezone.utc


# ------------------------------------------------------------------ helpers

def _aware(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def pt_clock(ts: datetime, rules: Rules, seconds: bool = True) -> str:
    local = _aware(ts).astimezone(ZoneInfo(rules.timezone))
    return local.strftime("%H:%M:%S PT" if seconds else "%H:%M PT")


def _base_filter():
    return (trades_t.c.simulated.is_(False)) & (trades_t.c.context != "training")


def as_of(engine: Engine | None = None) -> datetime | None:
    """Newest real fill timestamp (aware UTC); stand-in for the last import time."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        v = conn.execute(
            select(func.max(fills_t.c.ts_utc)).where(fills_t.c.simulated.is_(False))
        ).scalar()
    return _aware(v) if v is not None else None


def _pt_date(rules: Rules, now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(rules.timezone)).date()


# -------------------------------------------------------------- DB -> ledger

@dataclass
class LoadedPosition:
    trade_id: int
    trade_key: str | None
    label: str | None
    position: Position


def _fill_cash(side: str, qty: float, px: float, mult: float, fees: float) -> float:
    gross = qty * px * mult
    return -(gross + fees) if side == "buy" else gross - fees


def _load_positions(conn, rules: Rules, rows) -> dict[date, list[LoadedPosition]]:
    today = _pt_date(rules)
    tz = ZoneInfo(rules.timezone)
    by_day: dict[date, list[LoadedPosition]] = {}
    ids = [r._mapping["id"] for r in rows]
    members: dict[int, list] = {i: [] for i in ids}
    if ids:
        q = (
            select(trade_fills_t.c.trade_id, trade_fills_t.c.seq, fills_t)
            .select_from(trade_fills_t.join(fills_t, trade_fills_t.c.fill_id == fills_t.c.id))
            .where(trade_fills_t.c.trade_id.in_(ids))
            .order_by(trade_fills_t.c.trade_id, trade_fills_t.c.seq)
        )
        for m in conn.execute(q):
            members[m._mapping["trade_id"]].append(m._mapping)

    for r in rows:
        t = r._mapping
        fl: list[Fill] = []
        for f in members[t["id"]]:
            raw = f["raw_json"] if isinstance(f["raw_json"], dict) else {}
            mult = float(f["multiplier"] or 1.0)
            fl.append(Fill(
                side="B" if f["side"] == "buy" else "S",
                qty=int(round(f["qty"])),
                px=float(f["price"]),
                cash=_fill_cash(f["side"], f["qty"], f["price"], mult, float(f["fees"] or 0.0)),
                ts=_aware(f["ts_utc"]),
                otype=raw.get("order_type"),
            ))
        open_day = _aware(t["open_ts_utc"]).astimezone(tz).date()
        if t["status"] == "open" or t["close_ts_utc"] is None:
            swing = open_day < today
        else:
            swing = _aware(t["close_ts_utc"]).astimezone(tz).date() > open_day
        first = members[t["id"]][0] if members[t["id"]] else None
        pos = Position(
            fills=fl, status="open" if t["status"] == "open" else "closed", swing=swing,
            ticker=t["symbol"] or "",
            type={"CALL": "C", "PUT": "P"}.get(first["option_type"] if first else None, ""),
            strike=float(first["strike"] or 0.0) if first else 0.0,
            expiry=(first["expiry"] or "") if first else "",
        )
        by_day.setdefault(t["session_date"], []).append(
            LoadedPosition(t["id"], t["trade_key"], t["contract_label"], pos)
        )
    for lst in by_day.values():
        for i, lp in enumerate(lst):
            lp.position.seq = i
    return by_day


def _stops_for(conn, trade_ids: list[int]) -> StopUsage:
    if not trade_ids:
        return StopUsage()
    q = (
        select(fills_t.c.raw_json)
        .select_from(trade_fills_t.join(fills_t, trade_fills_t.c.fill_id == fills_t.c.id))
        .where(trade_fills_t.c.trade_id.in_(trade_ids))
    )
    filled = moves = 0
    for (raw,) in conn.execute(q):
        if isinstance(raw, dict) and raw.get("order_type") == "stop":
            filled += 1
            moves += int(raw.get("stop_moves") or 0)
    return StopUsage(placed=filled, filled=filled, moves=moves, pulled=0)


@dataclass
class DayData:
    result: DayResult
    loaded: list[LoadedPosition]
    rules: Rules
    fields: DayFields | None


def _analyze(conn, engine, rules, by_day, day: date) -> DayData:
    loaded = by_day.get(day, [])
    fields = get_day_fields(day, engine)
    di = DayInput(
        date=day, positions=[lp.position for lp in loaded],
        stops=_stops_for(conn, [lp.trade_id for lp in loaded]),
        max_loss=fields.max_loss_declared if fields else None,
    )
    res = ledger.analyze_day(di, rules, account_size(rules))
    return DayData(res, loaded, rules, fields)


def _rows(conn, d_from: date | None = None, d_to: date | None = None):
    stmt = select(trades_t).where(_base_filter())
    if d_from:
        stmt = stmt.where(trades_t.c.session_date >= d_from)
    if d_to:
        stmt = stmt.where(trades_t.c.session_date <= d_to)
    return conn.execute(stmt.order_by(trades_t.c.open_ts_utc.asc(), trades_t.c.id.asc())).fetchall()


# ------------------------------------------------------------------- public

def _load_days(d_from, d_to, rules, engine) -> tuple[Rules, list[DayData]]:
    engine = engine or get_engine()
    init_db(engine)
    rules = rules or load_rules()
    with engine.connect() as conn:
        by_day = _load_positions(conn, rules, _rows(conn, d_from, d_to))
        days = [_analyze(conn, engine, rules, by_day, d) for d in sorted(by_day)]
    return rules, days


def list_days(
    rules: Rules | None = None, engine: Engine | None = None
) -> tuple[Rules, list[DayData]]:
    return _load_days(None, None, rules, engine)


def get_day(
    day: date, rules: Rules | None = None, engine: Engine | None = None
) -> DayData | None:
    """One day's analysis, or None when there are neither trades nor day fields."""
    engine = engine or get_engine()
    init_db(engine)
    rules = rules or load_rules()
    with engine.connect() as conn:
        by_day = _load_positions(conn, rules, _rows(conn, day, day))
        data = _analyze(conn, engine, rules, by_day, day)
    if not data.loaded and data.fields is None:
        return None
    return data


def patterns(
    d_from: date | None = None, d_to: date | None = None,
    rules: Rules | None = None, engine: Engine | None = None,
) -> tuple[Rules, ledger.AcrossDays, list[DayData]]:
    rules, days = _load_days(d_from, d_to, rules, engine)
    return rules, ledger.across_days([d.result for d in days]), days


@dataclass
class LineStatus:
    rules: Rules
    now: datetime
    pnl: float
    left: float
    trades_counted: int
    state: str  # before_window | in_window | closed
    today: date
    today_net: float
    stop_hit: bool
    target_hit: bool
    line_hit: bool
    window: tuple[datetime, datetime]


def line_status(
    now: datetime | None = None, rules: Rules | None = None, engine: Engine | None = None
) -> LineStatus:
    """P&L since the loss line start (every closed trade) plus today's state/flags."""
    engine = engine or get_engine()
    init_db(engine)
    rules = rules or load_rules()
    now = _aware(now) if now else datetime.now(UTC)
    today = _pt_date(rules, now)
    with engine.connect() as conn:
        rows = conn.execute(
            select(trades_t.c.session_date, trades_t.c.net_pnl)
            .where(_base_filter())
            .where(trades_t.c.status == "closed")
            .where(trades_t.c.session_date >= rules.loss_line.start_date)
        ).fetchall()
    pnl = sum(float(r.net_pnl or 0.0) for r in rows)
    today_net = sum(float(r.net_pnl or 0.0) for r in rows if r.session_date == today)
    left = rules.loss_line.amount + min(0.0, pnl)
    start, end = window_bounds(rules, today)
    state = "before_window" if now < start else "in_window" if now <= end else "closed"
    return LineStatus(
        rules=rules, now=now, pnl=pnl, left=left, trades_counted=len(rows), state=state,
        today=today, today_net=today_net,
        stop_hit=today_net <= rules.daily_stop,
        target_hit=today_net >= rules.daily_target,
        line_hit=left <= 0,
        window=(start, end),
    )
