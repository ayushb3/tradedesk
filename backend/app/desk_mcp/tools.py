"""Desk MCP read tools: thin wrappers over ``app.desk`` returning JSON-able dicts.

Every result carries ``tz`` and ``as_of`` (newest fill time, UTC). Times are Pacific
and labelled "PT" in the ``*_pt`` fields. Nothing here touches the network; positions
are identified by ``trade_id`` / ``trade_key`` only (never an account or order id).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.db.connection import get_engine, init_db
from app.db.schema import fills as fills_t
from app.db.schema import plans as plans_t
from app.db.schema import trades as trades_t
from app.desk import days as desk_days
from app.desk.plans import list_plans
from app.desk.rules import Rules, load_rules, window_bounds
from app.models.desk import (
    BiggestRisk, DayDetail, DaysResponse, DayStatsOut, DaySummary, FillOut, LineResponse,
    LineToday, PatternsResponse, PositionOut, StopUsageOut, TagOut, WindowOut,
)

from . import mock


@dataclass
class Ctx:
    mode: str  # "mock" | "real"
    db_path: str
    engine: Engine
    rules: Rules


def build_ctx() -> Ctx:
    """Mock mode (TRADEDESK_MODE=mock) -> synthetic fixture DB + placeholder rules.
    Anything else -> the configured desk DB and the personal rules file."""
    settings = get_settings()
    if settings.mode == "mock":
        path = mock.ensure_mock_db()
        engine = get_engine(str(path))
        init_db(engine)
        return Ctx("mock", str(path), engine, mock.mock_rules())
    engine = get_engine(settings.db_path)
    init_db(engine)
    return Ctx("real", settings.db_path, engine, load_rules())


def _as_of_iso(ctx: Ctx) -> str | None:
    as_of = desk_days.as_of(ctx.engine)
    return as_of.isoformat().replace("+00:00", "Z") if as_of else None


def _meta(ctx: Ctx) -> dict:
    return {"tz": ctx.rules.timezone, "as_of": _as_of_iso(ctx)}


def _parse_date(value: str) -> date_type:
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}") from exc


# ---------------------------------------------------------------- serializers
# Same shapes as the HTTP API (app.models.desk), built here so the tool server
# never imports the web layer.

def _stops(s) -> StopUsageOut:
    return StopUsageOut(placed=s.placed, filled=s.filled, moves=s.moves, pulled=s.pulled)


def _window(start, end, rules: Rules) -> WindowOut:
    return WindowOut(
        start=start, end=end,
        start_pt=desk_days.pt_clock(start, rules, seconds=False),
        end_pt=desk_days.pt_clock(end, rules, seconds=False),
    )


def _stats(s, rules: Rules) -> DayStatsOut:
    return DayStatsOut(
        net=s.net, entries=s.entries, positions=s.positions, win_pct=s.win_pct,
        avg_win=s.avg_win, avg_loss=s.avg_loss, rr=s.rr,
        break_even_win_pct=s.break_even_win_pct, max_risk=s.max_risk,
        max_risk_x_stop=s.max_risk_x_stop, max_risk_pct_account=s.max_risk_pct_account,
        add_lower=s.add_lower, held_loser=s.held_loser, gave_back=s.gave_back,
        runner_paid=s.runner_paid, deployed=s.deployed, late_fills=s.late_fills,
        last_fill=s.last_fill,
        last_fill_pt=desk_days.pt_clock(s.last_fill, rules) if s.last_fill else None,
        stops=_stops(s.stops), n_wins=s.n_wins, n_losses=s.n_losses, n_closed=s.n_closed,
        sum_wins=s.sum_wins, sum_losses=s.sum_losses,
    )


def _position(lp, a, rules: Rules) -> PositionOut:
    p = lp.position
    return PositionOut(
        trade_id=lp.trade_id, trade_key=lp.trade_key, label=lp.label,
        ticker=p.ticker, type=p.type, strike=p.strike, expiry=p.expiry,
        status=p.status, swing=p.swing, qty_in=a.qty_in, qty_out=a.qty_out,
        cost=a.cost, proceeds=a.proceeds, avg_in=a.avg_in, avg_out=a.avg_out,
        pnl=a.pnl, risk=a.risk, r=a.r, stop_multiple=a.risk_x,
        add_lower=a.add_lower, add_higher=a.add_higher, add_same=a.add_same,
        runner_paid=a.runner_paid, gave_back=a.gave_back, held_loser=a.held_loser,
        best_gap=a.best_gap,
        tags=[TagOut(code=t.code, kind=t.kind, text=t.text) for t in a.tags],
        fills=[
            FillOut(
                side=f.side, qty=f.qty, px=f.px, cash=f.cash, ts=f.ts,
                ts_pt=desk_days.pt_clock(f.ts, rules), order_type=f.otype,
                pnl=f.pnl, pnl_pct=f.pnl_pct, avg_in_after=f.avg_in_after,
            )
            for f in a.fills
        ],
    )


# ----------------------------------------------------------------------- tools

def status(ctx: Ctx) -> dict:
    """DB path, mode, last import (as_of), and row counts."""
    with ctx.engine.connect() as conn:
        n_fills = conn.execute(
            select(func.count()).select_from(fills_t).where(fills_t.c.simulated.is_(False))
        ).scalar() or 0
        n_trades = conn.execute(
            select(func.count()).select_from(trades_t)
            .where(trades_t.c.simulated.is_(False)).where(trades_t.c.context != "training")
        ).scalar() or 0
        n_plans = conn.execute(select(func.count()).select_from(plans_t)).scalar() or 0
    _, days = desk_days.list_days(ctx.rules, ctx.engine)
    meta = _meta(ctx)
    return {
        **meta, "mode": ctx.mode, "db_path": str(Path(ctx.db_path)),
        "last_import": meta["as_of"],
        "counts": {"fills": n_fills, "trades": n_trades, "days": len(days), "plans": n_plans},
    }


def rules(ctx: Ctx) -> dict:
    return {**ctx.rules.model_dump(mode="json"), **_meta(ctx)}


def list_days(ctx: Ctx) -> dict:
    _, days = desk_days.list_days(ctx.rules, ctx.engine)
    out = [
        DaySummary(
            date=d.result.day, net=d.result.stats.net, positions=d.result.stats.positions,
            entries=d.result.stats.entries, win_pct=d.result.stats.win_pct,
            rr=d.result.stats.rr, max_risk_x_stop=d.result.stats.max_risk_x_stop,
            late_fills=d.result.stats.late_fills, has_fields=d.fields is not None,
        )
        for d in reversed(days)
    ]
    resp = DaysResponse(tz=ctx.rules.timezone, as_of=desk_days.as_of(ctx.engine), days=out)
    return resp.model_dump(mode="json")


def get_day(ctx: Ctx, date: str) -> dict:
    day = _parse_date(date)
    d = desk_days.get_day(day, ctx.rules, ctx.engine)
    if d is None:
        raise ValueError(f"no trades or day fields for {day}")
    start, end = window_bounds(ctx.rules, day)
    resp = DayDetail(
        tz=ctx.rules.timezone, as_of=desk_days.as_of(ctx.engine), date=day,
        window=_window(start, end, ctx.rules),
        daily_stop=ctx.rules.daily_stop, daily_target=ctx.rules.daily_target,
        stats=_stats(d.result.stats, ctx.rules),
        positions=[_position(lp, a, ctx.rules) for lp, a in zip(d.loaded, d.result.positions)],
        fields=d.fields,
    )
    return resp.model_dump(mode="json")


def get_trade(ctx: Ctx, id: int) -> dict:
    """One position (with fills) from its day, plus its plan if one matches."""
    with ctx.engine.connect() as conn:
        session_date = conn.execute(
            select(trades_t.c.session_date).where(trades_t.c.id == id)
            .where(trades_t.c.simulated.is_(False)).where(trades_t.c.context != "training")
        ).scalar()
    if session_date is None:
        raise ValueError(f"no trade with id {id}")
    day = (
        session_date if isinstance(session_date, date_type)
        else date_type.fromisoformat(str(session_date))
    )
    d = desk_days.get_day(day, ctx.rules, ctx.engine)
    pair = None
    if d is not None:
        pair = next(
            ((lp, a) for lp, a in zip(d.loaded, d.result.positions) if lp.trade_id == id), None
        )
    if pair is None:
        raise ValueError(f"no trade with id {id}")
    lp, a = pair
    pos = lp.position
    plan = None
    for p in list_plans(day, ctx.engine):
        same_contract = (
            p.symbol == pos.ticker and p.strike == pos.strike
            and (p.option_type or "")[:1] == pos.type
            and (p.expiry is None or p.expiry.isoformat() == pos.expiry)
        )
        if p.matched_trade_id == id or (p.matched_trade_id is None and same_contract):
            plan = p.model_dump(mode="json")
            break
    return {
        **_meta(ctx), "date": day.isoformat(),
        "trade": _position(lp, a, ctx.rules).model_dump(mode="json"), "plan": plan,
    }


def patterns(ctx: Ctx, date_from: str | None = None, date_to: str | None = None) -> dict:
    d_from = _parse_date(date_from) if date_from else None
    d_to = _parse_date(date_to) if date_to else None
    _, a, days = desk_days.patterns(d_from, d_to, ctx.rules, ctx.engine)
    biggest = None
    if days:
        top = max(days, key=lambda d: d.result.stats.max_risk)
        s = top.result.stats
        if s.max_risk > 0:
            biggest = BiggestRisk(date=s.date, dollars=s.max_risk, x_stop=s.max_risk_x_stop)
    resp = PatternsResponse(
        tz=ctx.rules.timezone, as_of=desk_days.as_of(ctx.engine), date_from=d_from, date_to=d_to,
        days=a.days, net=a.net, green_days=a.green_days, green_sum=a.green_sum,
        red_days=a.red_days, red_sum=a.red_sum, n_closed=a.n_closed, n_wins=a.n_wins,
        n_losses=a.n_losses, win_pct=a.win_pct, avg_win=a.avg_win, avg_loss=a.avg_loss,
        rr=a.rr, break_even_win_pct=a.break_even_win_pct, tag_counts=a.tag_counts,
        late_days=a.late_days, late_fills=a.late_fills, stops=_stops(a.stops),
        biggest_risk=biggest,
    )
    return resp.model_dump(mode="json")


def line_status(ctx: Ctx) -> dict:
    ls = desk_days.line_status(rules=ctx.rules, engine=ctx.engine)
    r = ls.rules
    resp = LineResponse(
        tz=r.timezone, as_of=desk_days.as_of(ctx.engine), now=ls.now,
        now_pt=desk_days.pt_clock(ls.now, r),
        start_date=r.loss_line.start_date, amount=r.loss_line.amount,
        pnl=ls.pnl, left=ls.left, trades_counted=ls.trades_counted, state=ls.state,
        today=LineToday(
            date=ls.today, net=ls.today_net, daily_stop=r.daily_stop,
            daily_target=r.daily_target, stop_hit=ls.stop_hit,
            target_hit=ls.target_hit, line_hit=ls.line_hit,
            window=_window(ls.window[0], ls.window[1], r),
        ),
    )
    return resp.model_dump(mode="json")
