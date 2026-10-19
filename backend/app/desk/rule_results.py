"""Rule results: did each trade, and each day, keep the desk's rules?

Pure functions over ledger objects (``PositionAnalysis``), the matched plan, the
per-trade call source and ``Rules``. No DB, no I/O, no network.

Every result is ``RuleResult(code, kept, detail)``:
- ``kept=True``  the rule was kept;
- ``kept=False`` the rule was broken;
- ``kept=None``  unknown (the data to judge it was never recorded). Never a guess.

Rules (codes are stable, the UI maps them to labels):
- ``before_cutoff``    the trade was entered (opening fill) at or before the window end
                       (``rules.window.end``, PT) on its session date.
- ``daily_stop``       the trade was not entered after the day's stop was hit. The stop is
                       hit when the day's realized net (closed positions, fees in, summed
                       fill by fill in time order) reaches ``rules.daily_stop``. Rules on
                       NET, like the ledger. Broken for trades opened strictly after the hit
                       time, AND for the trade that crossed the stop when its last sell left
                       the day's net strictly past ``break_limit`` = stop x (1 + tolerance),
                       ``rules.daily_stop_tolerance_pct`` (owner 2026-10-19: a -250 stop at
                       30% breaks below -325; between -250 and -325 is kept). ``blown_past``
                       still means "strictly past the stop itself". Day level: broken when
                       either happened.
- ``max_loss_written`` a max loss was written before entry: a matched plan that is not
                       ``late``, else the day's declared max loss (its timing is not
                       recorded, so it is trusted). A late plan counts as NOT written.
                       This is exactly the condition under which ``r_basis == "written"``.
- ``call_source``      the trade was a mentor call. ``own`` is broken, no recorded source
                       is unknown.
- ``size_within_plan`` gross premium paid (fees excluded) is within the matched plan's
                       ``size_usd``. No plan, or a late plan, is unknown.

Day level results use the same five codes: any broken trade -> broken; else any unknown ->
unknown; else kept; no trades -> unknown. (``daily_stop`` is described above.)

R itself lives in ``app.desk.ledger.resolve_r`` (re-exported here as ``resolve_r``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.desk.ledger import (
    CONTRACT_MULT, EPS, R_ASSUMED, R_WRITTEN, PositionAnalysis, resolve_r,
)
from app.desk.rules import Rules, window_bounds
from app.models.plans import Plan

__all__ = [
    "CALL_SOURCES", "RULE_CODES", "R_ASSUMED", "R_WRITTEN", "RuleResult", "StopResult",
    "TradeInput", "TradeRules", "DayRules", "evaluate_day", "resolve_r",
]

CALL_SOURCES = ("mentor", "own")
RULE_CODES = ("before_cutoff", "daily_stop", "max_loss_written", "call_source", "size_within_plan")


@dataclass(frozen=True)
class RuleResult:
    code: str
    kept: bool | None
    detail: str


@dataclass
class TradeInput:
    trade_id: int
    analysis: PositionAnalysis
    plan: Plan | None = None
    call_source: str | None = None  # "mentor" | "own" | None (not recorded)


@dataclass
class StopResult:
    limit: float  # rules.daily_stop (negative)
    hit: bool
    net: float  # the day's realized net
    hit_at: datetime | None = None  # fill time at which the running net reached the limit
    hit_at_pt: str | None = None
    trades_after: int = 0  # trades opened strictly after hit_at
    trade_ids_after: list[int] = field(default_factory=list)
    crossed_by: int | None = None  # trade whose sell fill reached the limit
    crossed_net: float | None = None  # day net after that trade's last sell
    blown_past: bool = False  # crossed_net strictly past the limit
    tolerance_pct: float = 0.0  # rules.daily_stop_tolerance_pct
    break_limit: float = 0.0  # limit x (1 + tolerance); past this the crossing trade is a break
    past_tolerance: bool = False  # crossed_net strictly past break_limit (rule broken)


@dataclass
class TradeRules:
    trade_id: int
    r: float | None
    risk: float | None
    r_basis: str
    results: list[RuleResult]


@dataclass
class DayRules:
    day: date
    trades: dict[int, TradeRules]
    results: list[RuleResult]
    stop: StopResult


# ------------------------------------------------------------------ helpers

def _pt(ts: datetime, rules: Rules, seconds: bool = False) -> str:
    local = ts.astimezone(ZoneInfo(rules.timezone))
    return local.strftime("%H:%M:%S PT" if seconds else "%H:%M PT")


def _usd(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def _open_ts(a: PositionAnalysis) -> datetime | None:
    return a.fills[0].ts if a.fills else None


def _stop_hit(
    inputs: list[TradeInput], rules: Rules,
) -> tuple[bool, datetime | None, float, int | None, float | None]:
    """(hit, hit_at, day net, crossing trade id, day net after the crossing trade's last sell).
    Realized P&L accrues on each sell fill of a closed position."""
    events = sorted(
        (f.ts, f.pnl, t.trade_id) for t in inputs if t.analysis.closed
        for f in t.analysis.fills if f.side == "S" and f.pnl is not None
    )
    run = 0.0
    hit_at = None
    crossed_by = None
    crossed_net = None
    for ts, pnl, tid in events:
        run += pnl
        if hit_at is None and run <= rules.daily_stop + EPS:
            hit_at, crossed_by = ts, tid
        if crossed_by is not None and tid == crossed_by:
            crossed_net = run
    return hit_at is not None, hit_at, run, crossed_by, crossed_net


def _rollup(code: str, results: list[RuleResult], ok: str, bad: str, unknown: str) -> RuleResult:
    """Day result from trade results. ``bad``/``unknown`` take the counts via format()."""
    n = len(results)
    broken = sum(1 for r in results if r.kept is False)
    unk = sum(1 for r in results if r.kept is None)
    if n == 0:
        return RuleResult(code, None, "No trades")
    if broken:
        return RuleResult(code, False, bad.format(broken=broken, n=n))
    if unk:
        return RuleResult(code, None, unknown.format(unknown=unk, n=n))
    return RuleResult(code, True, ok.format(n=n))


# --------------------------------------------------------------- per trade

def _before_cutoff(a: PositionAnalysis, end: datetime, rules: Rules) -> RuleResult:
    ts = _open_ts(a)
    if ts is None:
        return RuleResult("before_cutoff", None, "No fills")
    when, cut = _pt(ts, rules, seconds=True), _pt(end, rules)
    if ts <= end:
        return RuleResult("before_cutoff", True, f"Entered {when}, by the {cut} cutoff")
    return RuleResult("before_cutoff", False, f"Entered {when}, after the {cut} cutoff")


def _daily_stop(t: TradeInput, stop: StopResult, rules: Rules) -> RuleResult:
    ts = _open_ts(t.analysis)
    if not stop.hit or stop.hit_at is None or ts is None:
        return RuleResult("daily_stop", True, "Entered before the daily stop was hit")
    hit = _pt(stop.hit_at, rules, seconds=True)
    if ts > stop.hit_at:
        mins = int((ts - stop.hit_at).total_seconds() // 60)
        return RuleResult(
            "daily_stop", False,
            f"Entered {_pt(ts, rules, seconds=True)}, {mins} min after the daily stop was hit at {hit}",
        )
    if t.trade_id == stop.crossed_by and stop.past_tolerance:
        return RuleResult(
            "daily_stop", False,
            f"Took the day to {_usd(stop.crossed_net or 0)}, past the "
            f"{_usd(rules.daily_stop)} stop by more than {stop.tolerance_pct:g}% (hit {hit})",
        )
    if t.trade_id == stop.crossed_by and stop.blown_past:
        return RuleResult(
            "daily_stop", True,
            f"Took the day to {_usd(stop.crossed_net or 0)}, within {stop.tolerance_pct:g}% "
            f"of the {_usd(rules.daily_stop)} stop (hit {hit})",
        )
    return RuleResult("daily_stop", True, f"Entered before the daily stop was hit at {hit}")


def _max_loss_written(t: TradeInput, rules: Rules) -> RuleResult:
    a, plan = t.analysis, t.plan
    if a.r_basis == R_WRITTEN:
        if plan is not None and a.position.plan_max_loss is not None:
            return RuleResult(
                "max_loss_written", True,
                f"Max loss {_usd(plan.max_loss_usd)} written at {plan.created_pt} before entry",
            )
        return RuleResult(
            "max_loss_written", True,
            f"Day max loss {_usd(a.written_max_loss or 0)} declared (time not recorded)",
        )
    if plan is not None and plan.late:
        return RuleResult(
            "max_loss_written", False,
            f"Plan written at {plan.created_pt}, after entry; R assumes "
            f"{rules.default_risk_pct:g}% of cost",
        )
    return RuleResult(
        "max_loss_written", False,
        f"No max loss written; R assumes {rules.default_risk_pct:g}% of cost",
    )


def _call_source(t: TradeInput) -> RuleResult:
    if t.call_source == "mentor":
        return RuleResult("call_source", True, "Mentor call")
    if t.call_source == "own":
        return RuleResult("call_source", False, "Own idea")
    return RuleResult("call_source", None, "Call source not recorded")


def _size_within_plan(t: TradeInput) -> RuleResult:
    plan = t.plan
    if plan is None:
        return RuleResult("size_within_plan", None, "No plan to compare against")
    if plan.late:
        return RuleResult("size_within_plan", None, f"Plan written at {plan.created_pt}, after entry")
    gross = sum(
        f.qty * f.px * CONTRACT_MULT for f in t.analysis.position.fills if f.side == "B"
    )
    if gross <= plan.size_usd + 0.01:
        return RuleResult(
            "size_within_plan", True, f"Paid {_usd(gross)} of a {_usd(plan.size_usd)} plan"
        )
    return RuleResult(
        "size_within_plan", False, f"Paid {_usd(gross)}, plan was {_usd(plan.size_usd)}"
    )


def _trade_results(t: TradeInput, end: datetime, stop: StopResult, rules: Rules) -> TradeRules:
    a = t.analysis
    return TradeRules(
        trade_id=t.trade_id, r=a.r, risk=a.risk, r_basis=a.r_basis,
        results=[
            _before_cutoff(a, end, rules),
            _daily_stop(t, stop, rules),
            _max_loss_written(t, rules),
            _call_source(t),
            _size_within_plan(t),
        ],
    )


# ----------------------------------------------------------------- per day

def evaluate_day(day: date, inputs: list[TradeInput], rules: Rules) -> DayRules:
    """Rule results for every trade of one session day, plus the day's own results."""
    end = window_bounds(rules, day)[1]
    hit, hit_at, net, crossed_by, crossed_net = _stop_hit(inputs, rules)
    blown_past = crossed_net is not None and crossed_net < rules.daily_stop - EPS
    tol = rules.daily_stop_tolerance_pct
    break_limit = rules.daily_stop * (1 + tol / 100)
    past_tolerance = crossed_net is not None and crossed_net < break_limit - EPS
    after = [
        t.trade_id for t in inputs
        if hit_at is not None and (ts := _open_ts(t.analysis)) is not None and ts > hit_at
    ]
    stop = StopResult(
        limit=rules.daily_stop, hit=hit, net=net, hit_at=hit_at,
        hit_at_pt=_pt(hit_at, rules, seconds=True) if hit_at else None,
        trades_after=len(after), trade_ids_after=after,
        crossed_by=crossed_by, crossed_net=crossed_net, blown_past=blown_past,
        tolerance_pct=tol, break_limit=break_limit, past_tolerance=past_tolerance,
    )
    trades = {t.trade_id: _trade_results(t, end, stop, rules) for t in inputs}

    def col(code: str) -> list[RuleResult]:
        return [tr.results[RULE_CODES.index(code)] for tr in trades.values()]

    cut = _pt(end, rules)
    if not inputs:
        stop_day = RuleResult("daily_stop", None, "No trades")
    elif not hit:
        stop_day = RuleResult(
            "daily_stop", True,
            f"Daily stop {_usd(rules.daily_stop)} not hit (net {_usd(net)})",
        )
    elif after:
        stop_day = RuleResult(
            "daily_stop", False,
            f"Daily stop {_usd(rules.daily_stop)} hit {stop.hit_at_pt}; "
            f"{len(after)} trade{'s' if len(after) != 1 else ''} after it",
        )
    elif past_tolerance:
        stop_day = RuleResult(
            "daily_stop", False,
            f"Daily stop {_usd(rules.daily_stop)} hit {stop.hit_at_pt}: day went to "
            f"{_usd(crossed_net or 0)}, past the stop by more than {tol:g}%; no trades after it",
        )
    elif blown_past:
        stop_day = RuleResult(
            "daily_stop", True,
            f"Daily stop {_usd(rules.daily_stop)} hit {stop.hit_at_pt}: day went to "
            f"{_usd(crossed_net or 0)}, within {tol:g}% of the stop; no trades after it",
        )
    else:
        stop_day = RuleResult(
            "daily_stop", True,
            f"Daily stop {_usd(rules.daily_stop)} hit {stop.hit_at_pt}; no trades after it",
        )

    sources = col("call_source")
    if not inputs:
        call_day = RuleResult("call_source", None, "No trades")
    else:
        own = sum(1 for r in sources if r.kept is False)
        unk = sum(1 for r in sources if r.kept is None)
        if own:
            call_day = RuleResult("call_source", False, f"{own} of {len(sources)} trades were own ideas")
        elif unk:
            call_day = RuleResult(
                "call_source", None, f"{unk} of {len(sources)} trades have no call source recorded"
            )
        else:
            call_day = RuleResult("call_source", True, "All trades were mentor calls")

    results = [
        _rollup("before_cutoff", col("before_cutoff"),
                f"All {{n}} trades entered by the {cut} cutoff",
                f"{{broken}} of {{n}} trades entered after the {cut} cutoff",
                "{unknown} of {n} trades have no fills"),
        stop_day,
        _rollup("max_loss_written", col("max_loss_written"),
                "Max loss written before entry on all {n} trades",
                "Max loss not written before entry on {broken} of {n} trades",
                ""),
        call_day,
        _rollup("size_within_plan", col("size_within_plan"),
                "All {n} trades within plan size",
                "{broken} of {n} trades over plan size",
                "{unknown} of {n} trades have no plan to compare"),
    ]
    return DayRules(day=day, trades=trades, results=results, stop=stop)
