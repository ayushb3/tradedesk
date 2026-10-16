"""Match a day's trades to its plan cards (issue #10).

Rule: same session date, same contract (symbol, option type, strike, expiry). A plan
written before the trade's opening fill is a proper match (nearest before wins). A plan
written after the opening fill can still match, but is `late` and does not count as
"size written first". Each plan matches at most one trade and each trade at most one plan.
Trades with no plan are off-plan.

`match_day` is idempotent: it recomputes from scratch each time and writes the result.
Trade ids are stable across re-imports (issue #3), so the persisted match survives too.

Plan status: matched | unmatched | open. Unmatched means no trade by the end of the day;
today's plans stay `open` until the trading window ends.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.db.connection import get_engine, init_db
from app.db.schema import fills as fills_t
from app.db.schema import plans as plans_t
from app.db.schema import trade_fills as trade_fills_t
from app.db.schema import trades as trades_t
from app.desk.plans import _naive_utc, _to_model
from app.desk.rules import Rules, load_rules, window_bounds
from app.models.plans import Plan

STRIKE_EPS = 1e-6


@dataclass(frozen=True)
class Match:
    plan_id: int
    trade_id: int
    late: bool


@dataclass
class _TradeKey:
    trade_id: int
    open_ts: datetime  # naive UTC
    contract: tuple  # (symbol, option_type, strike, expiry)


def _same_contract(a: tuple, b: tuple) -> bool:
    if a[0] != b[0] or a[1] != b[1] or (a[3] or None) != (b[3] or None):
        return False
    if a[2] is None or b[2] is None:
        return a[2] is None and b[2] is None
    return abs(float(a[2]) - float(b[2])) < STRIKE_EPS


def day_is_over(day: date_type, rules: Rules | None, now: datetime | None = None) -> bool:
    """True once no more trades can be taken for `day`: a past day, or today after the
    trading window ends. With no rules, today stays open."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    tz = ZoneInfo(rules.timezone) if rules else ZoneInfo("America/Los_Angeles")
    today = now.astimezone(tz).date()
    if day < today:
        return True
    if day > today or rules is None:
        return False
    return now > window_bounds(rules, day)[1]


def effective_status(plan: Plan, rules: Rules | None, now: datetime | None = None) -> str:
    """Persisted status, except an unmatched `open` plan on a finished day reads as unmatched."""
    if (
        plan.status == "open"
        and plan.matched_trade_id is None
        and day_is_over(plan.session_date, rules, now)
    ):
        return "unmatched"
    return plan.status


def _day_trades(conn, day: date_type) -> list[_TradeKey]:
    rows = conn.execute(
        select(trades_t.c.id, trades_t.c.open_ts_utc)
        .where(trades_t.c.session_date == day)
        .where(trades_t.c.simulated.is_(False))
        .where(trades_t.c.context != "training")
        .order_by(trades_t.c.open_ts_utc, trades_t.c.id)
    ).all()
    out: list[_TradeKey] = []
    for tid, open_ts in rows:
        f = conn.execute(
            select(fills_t.c.symbol, fills_t.c.option_type, fills_t.c.strike, fills_t.c.expiry)
            .select_from(trade_fills_t.join(fills_t, trade_fills_t.c.fill_id == fills_t.c.id))
            .where(trade_fills_t.c.trade_id == tid)
            .order_by(trade_fills_t.c.seq)
            .limit(1)
        ).first()
        if f is None:
            continue
        out.append(
            _TradeKey(tid, _naive_utc(open_ts), (f.symbol, f.option_type, f.strike, f.expiry))
        )
    return out


def _plan_contract(p: Plan) -> tuple:
    return (p.symbol, p.option_type, p.strike, p.expiry.isoformat() if p.expiry else None)


def compute_matches(plans: list[Plan], trades: list[_TradeKey]) -> list[Match]:
    """Pure assignment. Pass 1: plans written before the opening fill, nearest first, trades
    in time order. Pass 2: leftover trades take leftover plans written after (late), earliest
    first, so a late plan never steals a trade that has a proper one."""
    free = {p.id: p for p in plans}
    matches: dict[int, Match] = {}

    def cands(t: _TradeKey) -> list[Plan]:
        return [p for p in free.values() if _same_contract(_plan_contract(p), t.contract)]

    for t in trades:
        before = [p for p in cands(t) if _naive_utc(p.created_at) <= t.open_ts]
        if before:
            best = max(before, key=lambda p: (_naive_utc(p.created_at), p.id))
            matches[t.trade_id] = Match(best.id, t.trade_id, False)
            del free[best.id]
    for t in trades:
        if t.trade_id in matches:
            continue
        after = cands(t)
        if after:
            best = min(after, key=lambda p: (_naive_utc(p.created_at), p.id))
            matches[t.trade_id] = Match(best.id, t.trade_id, True)
            del free[best.id]
    return list(matches.values())


def match_day(
    session_date: date_type | str,
    engine: Engine | None = None,
    *,
    rules: Rules | None = None,
    now: datetime | None = None,
    finalize: bool = True,
) -> list[Match]:
    """Recompute and persist plan <-> trade matches for one day.

    `finalize=False` never marks a plan unmatched (used when a plan is just created,
    before that day's trades may have been imported)."""
    day = (
        session_date
        if isinstance(session_date, date_type)
        else date_type.fromisoformat(session_date)
    )
    engine = engine or get_engine()
    init_db(engine)
    if rules is None:
        try:
            rules = load_rules()
        except Exception:  # noqa: BLE001 - rules are optional here
            rules = None

    with engine.begin() as conn:
        rows = conn.execute(
            select(plans_t)
            .where(plans_t.c.session_date == day.isoformat())
            .order_by(plans_t.c.created_at, plans_t.c.id)
        ).all()
        if not rows:
            return []
        plans = [_to_model(r) for r in rows]
        matches = compute_matches(plans, _day_trades(conn, day))
        by_plan = {m.plan_id: m for m in matches}
        over = finalize and day_is_over(day, rules, now)
        for p in plans:
            m = by_plan.get(p.id)
            if m:
                vals = {"status": "matched", "matched_trade_id": m.trade_id, "late": m.late}
            else:
                vals = {
                    "status": "unmatched" if over else "open",
                    "matched_trade_id": None,
                    "late": False,
                }
            conn.execute(plans_t.update().where(plans_t.c.id == p.id).values(**vals))
    return matches


def match_plan_days(engine: Engine | None = None) -> int:
    """Re-match every day that has plans (used after an import). Returns days processed."""
    engine = engine or get_engine()
    init_db(engine)
    with engine.connect() as conn:
        days = [r[0] for r in conn.execute(select(plans_t.c.session_date).distinct()).all()]
    for d in days:
        match_day(d, engine)
    return len(days)
