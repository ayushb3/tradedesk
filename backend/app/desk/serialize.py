"""Web-free response builders for the desk day/pattern/line payloads.

Shared by the HTTP routes and the MCP tools so both emit identical shapes.
No network, no FastAPI: just ledger/analysis objects in, ``app.models.desk`` out.
"""
from __future__ import annotations

from datetime import date as date_type, datetime

from collections import Counter

from app.desk import days as desk_days
from app.desk import lessons as desk_lessons
from app.desk.rules import Rules, window_bounds
from app.models.desk import (
    BiggestRisk, DayDetail, DaysResponse, DayStatsOut, DaySummary, FillOut, LineResponse,
    LessonLinkOut, LineToday, PatternsResponse, PlanRef, PositionOut, RecurringLesson, StopUsageOut, TagOut, WindowOut,
)


def stops_out(s) -> StopUsageOut:
    return StopUsageOut(placed=s.placed, filled=s.filled, moves=s.moves, pulled=s.pulled)


def window_out(start, end, rules: Rules) -> WindowOut:
    return WindowOut(
        start=start, end=end,
        start_pt=desk_days.pt_clock(start, rules, seconds=False),
        end_pt=desk_days.pt_clock(end, rules, seconds=False),
    )


def stats_out(s, rules: Rules) -> DayStatsOut:
    return DayStatsOut(
        net=s.net, entries=s.entries, positions=s.positions, win_pct=s.win_pct,
        avg_win=s.avg_win, avg_loss=s.avg_loss, rr=s.rr,
        break_even_win_pct=s.break_even_win_pct, max_risk=s.max_risk,
        max_risk_x_stop=s.max_risk_x_stop, max_risk_pct_account=s.max_risk_pct_account,
        add_lower=s.add_lower, held_loser=s.held_loser, gave_back=s.gave_back,
        runner_paid=s.runner_paid, deployed=s.deployed, late_fills=s.late_fills,
        last_fill=s.last_fill,
        last_fill_pt=desk_days.pt_clock(s.last_fill, rules) if s.last_fill else None,
        stops=stops_out(s.stops), n_wins=s.n_wins, n_losses=s.n_losses, n_closed=s.n_closed,
        sum_wins=s.sum_wins, sum_losses=s.sum_losses,
    )


def position_out(lp, a, rules: Rules) -> PositionOut:
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


def days_response(rules: Rules, days, as_of: datetime | None) -> DaysResponse:
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
    return DaysResponse(tz=rules.timezone, as_of=as_of, days=out)


def pattern_counts(d) -> Counter:
    """Positions carrying each pattern on one day (tag codes + off_plan/late_fill)."""
    c: Counter = Counter()
    for lp, a in zip(d.loaded, d.result.positions):
        for t in a.tags:
            c[t.code] += 1
        if lp.off_plan:
            c["off_plan"] += 1
    if d.result.stats.late_fills:
        c["late_fill"] += 1
    return c


def day_detail(day: date_type, d, rules: Rules, as_of: datetime | None) -> DayDetail:
    start, end = window_bounds(rules, day)
    return DayDetail(
        tz=rules.timezone, as_of=as_of, date=day,
        window=window_out(start, end, rules),
        daily_stop=rules.daily_stop, daily_target=rules.daily_target,
        stats=stats_out(d.result.stats, rules),
        positions=[position_out(lp, a, rules) for lp, a in zip(d.loaded, d.result.positions)],
        fields=d.fields, plans=d.plans, plans_not_taken=d.plans_not_taken,
        off_plan_count=d.off_plan_count, size_written_first=d.size_written_first,
        blown_max_loss=d.blown_max_loss,
        lessons=[
            LessonLinkOut(**x)
            for x in desk_lessons.lessons_for(pattern_counts(d), rules, d.engine)
        ],
    )


def patterns_response(rules: Rules, a, days, d_from, d_to, as_of) -> PatternsResponse:
    biggest = None
    if days:
        top = max(days, key=lambda d: d.result.stats.max_risk)
        s = top.result.stats
        if s.max_risk > 0:
            biggest = BiggestRisk(date=s.date, dollars=s.max_risk, x_stop=s.max_risk_x_stop)
    positions: Counter = Counter()
    day_count: Counter = Counter()
    for d in days:
        pc = pattern_counts(d)
        positions.update(pc)
        day_count.update(pc.keys())
    recurring = {k: v for k, v in positions.items() if day_count[k] >= 2}
    engine = days[0].engine if days else None
    lessons = [
        RecurringLesson(**x, days=day_count[x["pattern"]], positions=positions[x["pattern"]])
        for x in desk_lessons.lessons_for(recurring, rules, engine)
    ]
    return PatternsResponse(
        lessons=lessons,
        tz=rules.timezone, as_of=as_of, date_from=d_from, date_to=d_to,
        days=a.days, net=a.net, green_days=a.green_days, green_sum=a.green_sum,
        red_days=a.red_days, red_sum=a.red_sum, n_closed=a.n_closed, n_wins=a.n_wins,
        n_losses=a.n_losses, win_pct=a.win_pct, avg_win=a.avg_win, avg_loss=a.avg_loss,
        rr=a.rr, break_even_win_pct=a.break_even_win_pct, tag_counts=a.tag_counts,
        late_days=a.late_days, late_fills=a.late_fills, stops=stops_out(a.stops),
        biggest_risk=biggest,
    )


def line_response(ls, as_of: datetime | None) -> LineResponse:
    rules = ls.rules
    return LineResponse(
        tz=rules.timezone, as_of=as_of, now=ls.now,
        now_pt=desk_days.pt_clock(ls.now, rules),
        start_date=rules.loss_line.start_date, amount=rules.loss_line.amount,
        pnl=ls.pnl, left=ls.left, trades_counted=ls.trades_counted, state=ls.state,
        today=LineToday(
            date=ls.today, net=ls.today_net, daily_stop=rules.daily_stop,
            daily_target=rules.daily_target, stop_hit=ls.stop_hit,
            target_hit=ls.target_hit, line_hit=ls.line_hit,
            window=window_out(ls.window[0], ls.window[1], rules),
        ),
    )
