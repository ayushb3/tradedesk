"""Web-free response builders for the desk day/pattern/line payloads.

Shared by the HTTP routes and the MCP tools so both emit identical shapes.
No network, no FastAPI: just ledger/analysis objects in, ``app.models.desk`` out.
"""
from __future__ import annotations

from datetime import date as date_type, datetime

from collections import Counter

from app.analytics.excursions import (
    EXCURSION_PATTERN_CODES, EXITED_EARLY_THRESHOLD_R, EXITED_EARLY_WINDOW_MIN, ExcursionRollup,
    excursion_codes, excursion_tag_spec, rollup_excursions,
)
from app.desk import days as desk_days
from app.desk import lessons as desk_lessons
from app.desk.rules import Rules, window_bounds
from app.models.desk import (
    BiggestRisk, DayDetail, ExcursionRollupOut, ExitedEarlyOut, HeldPastStopOut, DaysResponse, DayStatsOut, DaySummary, FillOut, LineResponse,
    LessonLinkOut, LineToday, RuleResultOut, StopResultOut, PatternsResponse, PlanRef, PositionOut, RecurringLesson, StopUsageOut, TagOut, WindowOut,
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
        net=s.net, realized_before_fees=s.realized_before_fees, fees=s.fees, entries=s.entries, positions=s.positions, win_pct=s.win_pct,
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


def rule_result_out(r) -> RuleResultOut:
    return RuleResultOut(code=r.code, kept=r.kept, detail=r.detail)


def stop_result_out(s) -> StopResultOut:
    return StopResultOut(
        limit=s.limit, hit=s.hit, net=s.net, hit_at=s.hit_at, hit_at_pt=s.hit_at_pt,
        trades_after=s.trades_after, trade_ids_after=list(s.trade_ids_after),
    )


def position_out(lp, a, rules: Rules, fact=None, rr=None) -> PositionOut:
    """``rr``: this trade's ``TradeRules`` from the day's rule results."""
    p = lp.position
    extra = [
        TagOut(code=c, kind=excursion_tag_spec(fact, c)[0], text=excursion_tag_spec(fact, c)[1])
        for c in excursion_codes(fact)
    ]
    return PositionOut(
        trade_id=lp.trade_id, trade_key=lp.trade_key, label=lp.label,
        ticker=p.ticker, type=p.type, strike=p.strike, expiry=p.expiry,
        status=p.status, swing=p.swing, qty_in=a.qty_in, qty_out=a.qty_out,
        cost=a.cost, proceeds=a.proceeds, avg_in=a.avg_in, avg_out=a.avg_out,
        pnl=a.pnl, fees=a.fees, realized_before_fees=a.realized_before_fees, risk=a.risk, r=a.r, r_basis=a.r_basis,
        call_source=lp.call_source,
        rule_results=[rule_result_out(r) for r in (rr.results if rr else [])],
        stop_multiple=a.risk_x,
        add_lower=a.add_lower, add_higher=a.add_higher, add_same=a.add_same,
        runner_paid=a.runner_paid, gave_back=a.gave_back, held_loser=a.held_loser,
        best_gap=a.best_gap,
        plan=(PlanRef(id=lp.plan.id, caller=lp.plan.caller, max_loss=lp.plan.max_loss_usd,
                      late=lp.plan.late) if lp.plan else None),
        off_plan=lp.off_plan, blown_max_loss=a.blown_max_loss,
        written_max_loss=a.written_max_loss,
        tags=[TagOut(code=t.code, kind=t.kind, text=t.text) for t in a.tags] + extra,
        fills=[
            FillOut(
                side=f.side, qty=f.qty, px=f.px, cash=f.cash, ts=f.ts,
                ts_pt=desk_days.pt_clock(f.ts, rules), order_type=f.otype,
                pnl=f.pnl, pnl_pct=f.pnl_pct, avg_in_after=f.avg_in_after,
                fees=f.fees, realized_before_fees=f.realized_before_fees,
            )
            for f in a.fills
        ],
    )


def day_callers(d) -> list[str]:
    """Callers for the day: the self-report's list, else the plan cards' callers."""
    if d.fields is not None and d.fields.callers:
        return list(d.fields.callers)
    return list(dict.fromkeys(p.caller for p in d.plans if p.caller))


def days_response(rules: Rules, days, as_of: datetime | None) -> DaysResponse:
    out = [
        DaySummary(
            date=d.result.day, net=d.result.stats.net,
            realized_before_fees=d.result.stats.realized_before_fees, fees=d.result.stats.fees,
            positions=d.result.stats.positions,
            entries=d.result.stats.entries, win_pct=d.result.stats.win_pct,
            rr=d.result.stats.rr, max_risk_x_stop=d.result.stats.max_risk_x_stop,
            late_fills=d.result.stats.late_fills, has_fields=d.fields is not None,
            off_plan_count=d.off_plan_count, blown_max_loss=d.blown_max_loss,
            stats=stats_out(d.result.stats, rules), callers=day_callers(d),
            size_written_first=d.size_written_first,
            rule_results=[rule_result_out(r) for r in d.rule_results.results],
            stop_result=stop_result_out(d.rule_results.stop),
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
        for code in excursion_codes(getattr(d, "excursions", {}).get(lp.trade_id)):
            c[code] += 1
        if lp.off_plan:
            c["off_plan"] += 1
    if d.result.stats.late_fills:
        c["late_fill"] += 1
    return c


def rollup_out(r: ExcursionRollup, *, measured: bool, bars_mode: str | None = None) -> ExcursionRollupOut:
    return ExcursionRollupOut(
        measured=measured, n_trades=r.n_trades, n_measured=r.n_measured, n_skipped=r.n_skipped,
        summary=f"{r.n_measured} measured / {r.n_trades} trades",
        held_past_stop=HeldPastStopOut(
            count=r.held_past_stop, recovered=r.recovered, not_recovered=r.not_recovered,
            not_recovered_cost_usd=r.not_recovered_cost_usd,
            not_recovered_cost_r=r.not_recovered_cost_r,
        ),
        exited_early=ExitedEarlyOut(
            count=r.exited_early, winners_checked=r.winners_checked,
            window_minutes=EXITED_EARLY_WINDOW_MIN, threshold_r=EXITED_EARLY_THRESHOLD_R,
        ),
        median_drawdown_minutes=r.median_drawdown_minutes, bars_mode=bars_mode,
    )


def excursion_rollup_out(days, bars_mode: str | None = None) -> ExcursionRollupOut:
    """Rollup over days whose excursions were attached (else an unmeasured placeholder)."""
    if not days or not all(getattr(d, "excursions_attached", False) for d in days):
        return ExcursionRollupOut()
    facts = [f for d in days for f in d.excursions.values()]
    n_closed = sum(1 for d in days for a in d.result.positions if a.closed)
    return rollup_out(rollup_excursions(facts, n_closed), measured=True, bars_mode=bars_mode)


def day_detail(
    day: date_type, d, rules: Rules, as_of: datetime | None, bars_mode: str | None = None
) -> DayDetail:
    start, end = window_bounds(rules, day)
    rollup = excursion_rollup_out([d], bars_mode)
    return DayDetail(
        tz=rules.timezone, as_of=as_of, date=day,
        window=window_out(start, end, rules),
        daily_stop=rules.daily_stop, daily_target=rules.daily_target,
        stats=stats_out(d.result.stats, rules),
        positions=[
            position_out(lp, a, rules, d.excursions.get(lp.trade_id), d.rule_results.trades.get(lp.trade_id))
            for lp, a in zip(d.loaded, d.result.positions)
        ],
        fields=d.fields, plans=d.plans, plans_not_taken=d.plans_not_taken,
        off_plan_count=d.off_plan_count, size_written_first=d.size_written_first,
        blown_max_loss=d.blown_max_loss,
        rule_results=[rule_result_out(r) for r in d.rule_results.results],
        stop_result=stop_result_out(d.rule_results.stop),
        excursion_rollup=rollup,
        lessons=[
            LessonLinkOut(**x)
            for x in desk_lessons.lessons_for(pattern_counts(d), rules, d.engine)
        ],
    )


def patterns_response(
    rules: Rules, a, days, d_from, d_to, as_of, bars_mode: str | None = None
) -> PatternsResponse:
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
    rollup = excursion_rollup_out(days, bars_mode)
    tag_counts = dict(a.tag_counts)
    if rollup.measured:
        for code in EXCURSION_PATTERN_CODES:
            if positions[code]:
                tag_counts[code] = positions[code]
    return PatternsResponse(
        excursion_rollup=rollup,
        lessons=lessons,
        tz=rules.timezone, as_of=as_of, date_from=d_from, date_to=d_to,
        days=a.days, net=a.net, realized_before_fees=a.realized_before_fees, fees=a.fees,
        green_days=a.green_days, green_sum=a.green_sum,
        red_days=a.red_days, red_sum=a.red_sum, n_closed=a.n_closed, n_wins=a.n_wins,
        n_losses=a.n_losses, win_pct=a.win_pct, avg_win=a.avg_win, avg_loss=a.avg_loss,
        rr=a.rr, break_even_win_pct=a.break_even_win_pct, tag_counts=tag_counts,
        late_days=a.late_days, late_fills=a.late_fills, stops=stops_out(a.stops),
        biggest_risk=biggest,
    )


def line_response(ls, as_of: datetime | None) -> LineResponse:
    rules = ls.rules
    return LineResponse(
        tz=rules.timezone, as_of=as_of, now=ls.now,
        now_pt=desk_days.pt_clock(ls.now, rules),
        start_date=rules.loss_line.start_date, amount=rules.loss_line.amount,
        pnl=ls.pnl, fees=ls.fees, pnl_before_fees=ls.pnl_before_fees, left=ls.left, trades_counted=ls.trades_counted, state=ls.state,
        today=LineToday(
            date=ls.today, net=ls.today_net, daily_stop=rules.daily_stop,
            daily_target=rules.daily_target, stop_hit=ls.stop_hit,
            target_hit=ls.target_hit, line_hit=ls.line_hit,
            window=window_out(ls.window[0], ls.window[1], rules),
        ),
    )
