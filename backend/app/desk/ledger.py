"""Fill Ledger analysis: pure functions over fills and ``Rules``. No DB, no I/O.

Inputs are plain dataclasses (``Fill`` -> ``Position`` -> ``DayInput``); outputs are
dataclasses too, so the API, the MCP server and migration scripts can all reuse it.

Conventions
- P&L is on average cost, fees included (``cash`` already nets fees).
- Option contracts are x100 (``CONTRACT_MULT``).
- ``Rules.daily_stop`` is negative; its magnitude is the per-day stop.
- ``Rules.default_risk_pct`` is a percent of premium (30 means 30%), used as the
  risk for R when no max loss was written for the day.
- A fill is "late" when its aware timestamp is after the window end on the day.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.desk.rules import Rules, window_bounds

CONTRACT_MULT = 100
EPS = 1e-9


# ----------------------------------------------------------------- inputs

@dataclass
class Fill:
    side: str  # "B" or "S"
    qty: int
    px: float
    cash: float  # signed: negative for buys, positive for sells, fees in
    ts: datetime  # timezone-aware
    otype: str | None = None


@dataclass
class Position:
    fills: list[Fill]
    status: str = "closed"  # "closed" | "open"
    swing: bool = False  # held past the opening day
    ticker: str = ""
    type: str = ""  # "C" | "P"
    strike: float = 0.0
    expiry: str = ""
    seq: int = 0


@dataclass
class StopUsage:
    placed: int = 0
    filled: int = 0
    moves: int = 0  # stop trailed (cancel + replace)
    pulled: int = 0  # stop cancelled with no replacement and no fill


@dataclass
class DayInput:
    date: date
    positions: list[Position]
    stops: StopUsage = field(default_factory=StopUsage)
    max_loss: float | None = None  # written max loss in dollars (positive), if any


# ---------------------------------------------------------------- outputs

@dataclass
class Tag:
    code: str
    kind: str  # r = red flag, w = warning, a = note, g = good
    text: str


@dataclass
class FillRow:
    side: str
    qty: int
    px: float
    cash: float
    ts: datetime
    otype: str | None
    pnl: float | None  # sells only: on average cost
    pnl_pct: float | None
    avg_in_after: float | None  # buys only: average entry price after this fill


@dataclass
class PositionAnalysis:
    position: Position
    fills: list[FillRow]
    qty_in: int
    qty_out: int
    cost: float
    proceeds: float
    avg_in: float | None
    avg_out: float | None
    closed: bool
    pnl: float | None
    add_lower: int
    add_higher: int
    add_same: int
    runner_paid: bool
    gave_back: bool
    held_loser: bool
    best_gap: float
    risk_x: float  # cost as a multiple of the daily stop
    risk: float | None  # dollars risked for R
    r: float | None
    tags: list[Tag]
    n_buys: int


@dataclass
class DayStats:
    date: date
    net: float
    entries: int
    positions: int
    win_pct: float | None
    avg_win: float | None
    avg_loss: float | None
    rr: float | None
    break_even_win_pct: float | None
    max_risk: float
    max_risk_x_stop: float
    max_risk_pct_account: float | None
    add_lower: int
    held_loser: int
    gave_back: int
    runner_paid: int
    deployed: float
    late_fills: int
    last_fill: datetime | None
    stops: StopUsage
    n_wins: int
    n_losses: int
    n_closed: int
    sum_wins: float
    sum_losses: float


@dataclass
class DayResult:
    day: date
    positions: list[PositionAnalysis]
    stats: DayStats


@dataclass
class AcrossDays:
    days: int
    net: float
    green_days: int
    green_sum: float
    red_days: int
    red_sum: float
    n_closed: int
    n_wins: int
    n_losses: int
    win_pct: float | None  # actual
    avg_win: float | None
    avg_loss: float | None
    rr: float | None  # all positions pooled across days
    break_even_win_pct: float | None  # win rate that rr needs
    tag_counts: dict[str, int]
    late_days: list[date]
    late_fills: int
    stops: StopUsage


# ---------------------------------------------------------------- helpers

def rr(avg_win: float | None, avg_loss: float | None) -> float | None:
    """Reward:risk = average win / |average loss|."""
    if avg_win is not None and avg_loss is not None and avg_loss < 0:
        return avg_win / -avg_loss
    return None


def break_even_win_pct(ratio: float | None) -> float | None:
    """Win rate (percent) a reward:risk ratio needs to break even."""
    return None if ratio is None else 100 / (1 + ratio)


def _stop_magnitude(rules: Rules) -> float:
    return abs(rules.daily_stop)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


# --------------------------------------------------------------- per fill

def fill_rows(p: Position) -> list[FillRow]:
    """Per-fill P&L on average cost (fees in) and average entry after each buy."""
    run_q = 0
    run_cost = 0.0
    px_q = 0
    px_cost = 0.0
    rows: list[FillRow] = []
    for f in p.fills:
        avg_now = fp = fp_pct = None
        if f.side == "B":
            run_q += f.qty
            run_cost += -f.cash
            px_q += f.qty
            px_cost += f.px * f.qty
            avg_now = px_cost / px_q
        elif run_q > 0:
            basis = (run_cost / run_q) * f.qty
            fp = f.cash - basis
            fp_pct = fp / basis * 100 if basis else None
            run_cost -= basis
            run_q -= f.qty
        rows.append(FillRow(f.side, f.qty, f.px, f.cash, f.ts, f.otype, fp, fp_pct, avg_now))
    return rows


# ----------------------------------------------------------- per position

def analyze_position(
    p: Position, rules: Rules, max_loss: float | None = None
) -> PositionAnalysis:
    buys = [f for f in p.fills if f.side == "B"]
    sells = [f for f in p.fills if f.side == "S"]
    qty_in = sum(f.qty for f in buys)
    qty_out = sum(f.qty for f in sells)
    cost = -sum(f.cash for f in buys)
    proceeds = sum(f.cash for f in sells)
    avg_in = sum(f.px * f.qty for f in buys) / qty_in if qty_in else None
    avg_out = sum(f.px * f.qty for f in sells) / qty_out if qty_out else None
    closed = p.status != "open"
    pnl = proceeds - cost if closed else None

    run_q = 0
    run_cost = 0.0
    add_lower = add_higher = add_same = 0
    for f in p.fills:
        if f.side != "B":
            continue
        if run_q > 0:
            ra = run_cost / run_q
            if f.px < ra - EPS:
                add_lower += 1
            elif f.px > ra + EPS:
                add_higher += 1
            else:
                add_same += 1
        run_q += f.qty
        run_cost += f.px * f.qty

    first = sells[0].px if sells else None
    last = sells[-1].px if sells else None
    best = max((f.px for f in sells), default=None)
    multi_exit = len(sells) >= 2
    runner_paid = bool(multi_exit and pnl is not None and pnl > 0 and last > first + EPS)
    gave_back = bool(multi_exit and pnl is not None and pnl > 0 and last < first - EPS)
    held_loser = bool(multi_exit and pnl is not None and pnl < 0 and last < first - EPS)
    best_gap = (best - avg_out) * qty_out * CONTRACT_MULT if best is not None and qty_out else 0.0
    risk_x = cost / _stop_magnitude(rules)

    risk = max_loss if max_loss is not None else cost * rules.default_risk_pct / 100
    r = pnl / risk if pnl is not None and risk else None

    a = PositionAnalysis(
        position=p, fills=fill_rows(p), qty_in=qty_in, qty_out=qty_out, cost=cost,
        proceeds=proceeds, avg_in=avg_in, avg_out=avg_out, closed=closed, pnl=pnl,
        add_lower=add_lower, add_higher=add_higher, add_same=add_same,
        runner_paid=runner_paid, gave_back=gave_back, held_loser=held_loser,
        best_gap=best_gap, risk_x=risk_x, risk=risk, r=r, tags=[], n_buys=len(buys),
    )
    a.tags = tags_for(a)
    return a


def tags_for(a: PositionAnalysis) -> list[Tag]:
    t: list[Tag] = []
    if a.add_lower:
        t.append(Tag("added_lower", "r", f"added lower x{a.add_lower}" if a.add_lower > 1 else "added lower"))
    if a.add_higher:
        t.append(Tag("added_higher", "a", "added higher"))
    if a.add_same:
        t.append(Tag("scaled_in", "a", f"scaled in x{a.n_buys}"))
    if a.held_loser:
        t.append(Tag("held_loser", "r", "held loser down"))
    if a.gave_back:
        t.append(Tag("gave_back", "w", "gave back"))
    if a.runner_paid:
        t.append(Tag("runner_paid", "g", "runner paid"))
    if a.risk_x > 1:
        t.append(Tag("stop_risk", "r" if a.risk_x >= 3 else "w", f"{a.risk_x:.1f}x stop at risk"))
    if a.position.swing:
        t.append(Tag("overnight", "w", "held overnight"))
    if a.position.status == "open":
        t.append(Tag("still_open", "w", "still open"))
    return t


# ---------------------------------------------------------------- per day

def analyze_day(
    day: DayInput, rules: Rules, account_size: float | None = None
) -> DayResult:
    ps = [analyze_position(p, rules, day.max_loss) for p in day.positions]
    closed = [a for a in ps if a.closed]
    wins = [a.pnl for a in closed if a.pnl > 0]
    losses = [a.pnl for a in closed if a.pnl < 0]
    avg_win, avg_loss = _mean(wins), _mean(losses)
    ratio = rr(avg_win, avg_loss)
    max_risk = max([0.0] + [a.cost for a in ps])

    end = window_bounds(rules, day.date)[1]
    tz = ZoneInfo(rules.timezone)
    day_fills = [
        f.ts for a in ps for f in a.position.fills if f.ts.astimezone(tz).date() == day.date
    ]
    late = sum(1 for ts in day_fills if ts > end)

    stats = DayStats(
        date=day.date,
        net=sum(a.pnl for a in closed),
        entries=sum(a.n_buys for a in ps),
        positions=len(ps),
        win_pct=len(wins) / len(closed) * 100 if closed else None,
        avg_win=avg_win, avg_loss=avg_loss, rr=ratio,
        break_even_win_pct=break_even_win_pct(ratio),
        max_risk=max_risk,
        max_risk_x_stop=max_risk / _stop_magnitude(rules),
        max_risk_pct_account=max_risk / account_size * 100 if account_size else None,
        add_lower=sum(1 for a in ps if a.add_lower > 0),
        held_loser=sum(1 for a in ps if a.held_loser),
        gave_back=sum(1 for a in ps if a.gave_back),
        runner_paid=sum(1 for a in ps if a.runner_paid),
        deployed=sum(a.cost for a in ps),
        late_fills=late,
        last_fill=max(day_fills) if day_fills else None,
        stops=day.stops,
        n_wins=len(wins), n_losses=len(losses), n_closed=len(closed),
        sum_wins=sum(wins), sum_losses=sum(losses),
    )
    return DayResult(day=day.date, positions=ps, stats=stats)


# ------------------------------------------------------------ across days

def across_days(results: list[DayResult]) -> AcrossDays:
    stats = [r.stats for r in results]
    all_closed = [a.pnl for r in results for a in r.positions if a.closed]
    wins = [x for x in all_closed if x > 0]
    losses = [x for x in all_closed if x < 0]
    avg_win, avg_loss = _mean(wins), _mean(losses)
    ratio = rr(avg_win, avg_loss)
    tags = Counter(t.code for r in results for a in r.positions for t in a.tags)
    green = [s for s in stats if s.net > 0]
    red = [s for s in stats if s.net < 0]
    stops = StopUsage(
        placed=sum(s.stops.placed for s in stats),
        filled=sum(s.stops.filled for s in stats),
        moves=sum(s.stops.moves for s in stats),
        pulled=sum(s.stops.pulled for s in stats),
    )
    return AcrossDays(
        days=len(stats),
        net=sum(s.net for s in stats),
        green_days=len(green), green_sum=sum(s.net for s in green),
        red_days=len(red), red_sum=sum(s.net for s in red),
        n_closed=len(all_closed), n_wins=len(wins), n_losses=len(losses),
        win_pct=len(wins) / len(all_closed) * 100 if all_closed else None,
        avg_win=avg_win, avg_loss=avg_loss, rr=ratio,
        break_even_win_pct=break_even_win_pct(ratio),
        tag_counts=dict(tags),
        late_days=[s.date for s in stats if s.late_fills > 0],
        late_fills=sum(s.late_fills for s in stats),
        stops=stops,
    )
