"""Desk routes: GET /api/rules, /api/days, /api/days/{date}, /api/patterns, /api/line."""
from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, HTTPException, Query

from app.desk import days as desk_days
from app.desk.rules import TZ_NAME, Rules, load_rules, window_bounds
from app.models.desk import (
    BiggestRisk,
    DayDetail,
    DaysResponse,
    DayStatsOut,
    DaySummary,
    FillOut,
    LineResponse,
    LineToday,
    PatternsResponse,
    PlanRef,
    PositionOut,
    StopUsageOut,
    TagOut,
    WindowOut,
)

router = APIRouter(tags=["desk"])


def _rules() -> Rules:
    try:
        return load_rules()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/rules")
def get_rules() -> dict:
    rules = _rules()
    return {**rules.model_dump(mode="json"), "tz": TZ_NAME}


def _stops(s) -> StopUsageOut:
    return StopUsageOut(placed=s.placed, filled=s.filled, moves=s.moves, pulled=s.pulled)


def _window(start, end, rules: Rules) -> WindowOut:
    return WindowOut(
        start=start, end=end,
        start_pt=desk_days.pt_clock(start, rules, seconds=False),
        end_pt=desk_days.pt_clock(end, rules, seconds=False),
    )


def _stats_out(s, rules: Rules) -> DayStatsOut:
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


def _position_out(lp, a, rules: Rules) -> PositionOut:
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
        plan=(PlanRef(id=lp.plan.id, caller=lp.plan.caller, max_loss=lp.plan.max_loss_usd,
                      late=lp.plan.late) if lp.plan else None),
        off_plan=lp.off_plan, blown_max_loss=a.blown_max_loss,
        written_max_loss=a.written_max_loss,
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


@router.get("/days", response_model=DaysResponse)
def get_days() -> DaysResponse:
    rules, days = desk_days.list_days(_rules())
    out = [
        DaySummary(
            date=d.result.day, net=d.result.stats.net, positions=d.result.stats.positions,
            entries=d.result.stats.entries, win_pct=d.result.stats.win_pct,
            rr=d.result.stats.rr, max_risk_x_stop=d.result.stats.max_risk_x_stop,
            late_fills=d.result.stats.late_fills, has_fields=d.fields is not None,
            off_plan_count=d.off_plan_count, blown_max_loss=d.blown_max_loss,
        )
        for d in reversed(days)
    ]
    return DaysResponse(tz=rules.timezone, as_of=desk_days.as_of(), days=out)


@router.get("/days/{day}", response_model=DayDetail)
def get_day(day: date_type) -> DayDetail:
    rules = _rules()
    d = desk_days.get_day(day, rules)
    if d is None:
        raise HTTPException(status_code=404, detail=f"no trades or day fields for {day}")
    start, end = window_bounds(rules, day)
    return DayDetail(
        tz=rules.timezone, as_of=desk_days.as_of(), date=day,
        window=_window(start, end, rules),
        daily_stop=rules.daily_stop, daily_target=rules.daily_target,
        stats=_stats_out(d.result.stats, rules),
        positions=[
            _position_out(lp, a, rules) for lp, a in zip(d.loaded, d.result.positions)
        ],
        fields=d.fields, plans=d.plans, plans_not_taken=d.plans_not_taken,
        off_plan_count=d.off_plan_count, size_written_first=d.size_written_first,
        blown_max_loss=d.blown_max_loss,
    )


@router.get("/patterns", response_model=PatternsResponse)
def get_patterns(
    from_: date_type | None = Query(None, alias="from"),
    to: date_type | None = Query(None),
) -> PatternsResponse:
    rules, a, days = desk_days.patterns(from_, to, _rules())
    biggest = None
    if days:
        top = max(days, key=lambda d: d.result.stats.max_risk)
        s = top.result.stats
        if s.max_risk > 0:
            biggest = BiggestRisk(date=s.date, dollars=s.max_risk, x_stop=s.max_risk_x_stop)
    return PatternsResponse(
        tz=rules.timezone, as_of=desk_days.as_of(), date_from=from_, date_to=to,
        days=a.days, net=a.net, green_days=a.green_days, green_sum=a.green_sum,
        red_days=a.red_days, red_sum=a.red_sum, n_closed=a.n_closed, n_wins=a.n_wins,
        n_losses=a.n_losses, win_pct=a.win_pct, avg_win=a.avg_win, avg_loss=a.avg_loss,
        rr=a.rr, break_even_win_pct=a.break_even_win_pct, tag_counts=a.tag_counts,
        late_days=a.late_days, late_fills=a.late_fills, stops=_stops(a.stops),
        biggest_risk=biggest,
    )


@router.get("/line", response_model=LineResponse)
def get_line() -> LineResponse:
    ls = desk_days.line_status(rules=_rules())
    rules = ls.rules
    return LineResponse(
        tz=rules.timezone, as_of=desk_days.as_of(), now=ls.now,
        now_pt=desk_days.pt_clock(ls.now, rules),
        start_date=rules.loss_line.start_date, amount=rules.loss_line.amount,
        pnl=ls.pnl, left=ls.left, trades_counted=ls.trades_counted, state=ls.state,
        today=LineToday(
            date=ls.today, net=ls.today_net, daily_stop=rules.daily_stop,
            daily_target=rules.daily_target, stop_hit=ls.stop_hit,
            target_hit=ls.target_hit, line_hit=ls.line_hit,
            window=_window(ls.window[0], ls.window[1], rules),
        ),
    )
