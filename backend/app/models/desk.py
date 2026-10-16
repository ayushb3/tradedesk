"""Desk models (issue #7 onward). Later slices add to this file."""
from __future__ import annotations

from datetime import date as date_type

from pydantic import BaseModel, Field

from .plans import Plan
from .utc import UtcDatetime


class DayFields(BaseModel):
    """One day's self-report. Keyed on ISO date, independent of trades."""

    date: date_type
    why: str = ""
    callers: list[str] = Field(default_factory=list)
    size_written_first: bool | None = None
    max_loss_declared: float | None = None  # dollars
    closed_by_window_end: bool | None = None
    stop_hit: bool | None = None
    target_hit: bool | None = None
    how_felt: str = ""
    notes: str = ""
    updated_at: UtcDatetime


# --------------------------------------------------------------------------
# Days / patterns / line API (issue #8). Every response carries ``tz`` and
# ``as_of``. Times are aware and go out as UTC with a Z; ``*_pt`` strings are
# the same instant as a labelled Pacific clock for the UI. No account, order or
# provider ids appear here: positions are identified by ``trade_id`` + ``trade_key``.
# --------------------------------------------------------------------------

class StopUsageOut(BaseModel):
    placed: int = 0
    filled: int = 0
    moves: int = 0
    pulled: int = 0


class TagOut(BaseModel):
    code: str
    kind: str  # r = red flag, w = warning, a = note, g = good
    text: str


class FillOut(BaseModel):
    side: str  # "B" | "S"
    qty: int
    px: float
    cash: float
    ts: UtcDatetime
    ts_pt: str  # e.g. "06:41:07 PT"
    order_type: str | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    avg_in_after: float | None = None
    fees: float = 0.0
    realized_before_fees: float | None = None  # sells only


class PlanRef(BaseModel):
    id: int
    caller: str
    max_loss: float
    late: bool


class PositionOut(BaseModel):
    trade_id: int
    trade_key: str | None = None
    label: str | None = None
    ticker: str
    type: str  # "C" | "P" | ""
    strike: float
    expiry: str
    status: str
    swing: bool
    qty_in: int
    qty_out: int
    cost: float
    proceeds: float
    avg_in: float | None = None
    avg_out: float | None = None
    pnl: float | None = None
    risk: float | None = None
    r: float | None = None
    stop_multiple: float  # cost as a multiple of the daily stop
    add_lower: int
    add_higher: int
    add_same: int
    runner_paid: bool
    gave_back: bool
    held_loser: bool
    best_gap: float
    tags: list[TagOut]
    fills: list[FillOut]
    plan: PlanRef | None = None
    off_plan: bool = False
    blown_max_loss: bool = False
    written_max_loss: float | None = None
    fees: float = 0.0
    realized_before_fees: float | None = None  # closed only: pnl + fees


class DayStatsOut(BaseModel):
    net: float
    realized_before_fees: float = 0.0  # closed positions; the broker's realized figure
    fees: float = 0.0  # closed positions; realized_before_fees - fees == net
    entries: int
    positions: int
    win_pct: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    rr: float | None = None
    break_even_win_pct: float | None = None
    max_risk: float
    max_risk_x_stop: float
    max_risk_pct_account: float | None = None
    add_lower: int
    held_loser: int
    gave_back: int
    runner_paid: int
    deployed: float
    late_fills: int
    last_fill: UtcDatetime | None = None
    last_fill_pt: str | None = None
    stops: StopUsageOut
    n_wins: int
    n_losses: int
    n_closed: int
    sum_wins: float
    sum_losses: float


class WindowOut(BaseModel):
    start: UtcDatetime
    end: UtcDatetime
    start_pt: str
    end_pt: str


class DaySummary(BaseModel):
    date: date_type
    net: float
    realized_before_fees: float = 0.0
    fees: float = 0.0
    positions: int
    entries: int
    win_pct: float | None = None
    rr: float | None = None
    max_risk_x_stop: float
    late_fills: int
    has_fields: bool
    off_plan_count: int = 0
    blown_max_loss: bool = False
    # Full day stats and the day's callers, so one list call can fill an across-days table.
    stats: DayStatsOut | None = None
    callers: list[str] = Field(default_factory=list)
    size_written_first: bool | None = None


class DaysResponse(BaseModel):
    tz: str
    as_of: UtcDatetime | None = None
    days: list[DaySummary]


class LessonLinkOut(BaseModel):
    """A lesson that addresses a flagged pattern. Id and the user's reason only."""

    pattern: str
    lesson_id: str
    reason: str = ""
    href: str | None = None  # /knowledge/lessons/{id} when the id resolves locally
    available: bool = False


class RecurringLesson(LessonLinkOut):
    days: int  # days in the range on which the pattern appeared
    positions: int  # positions carrying the pattern across the range


class DayDetail(BaseModel):
    tz: str
    as_of: UtcDatetime | None = None
    date: date_type
    window: WindowOut
    daily_stop: float
    daily_target: float
    stats: DayStatsOut
    positions: list[PositionOut]
    fields: DayFields | None = None
    plans: list[Plan] = Field(default_factory=list)  # incl. "planned, not taken" (unmatched)
    plans_not_taken: int = 0  # a good outcome: the plan was written and not forced
    off_plan_count: int = 0
    size_written_first: bool | None = None  # derived: every trade has a non-late plan
    blown_max_loss: bool = False
    lessons: list[LessonLinkOut] = Field(default_factory=list)


class BiggestRisk(BaseModel):
    date: date_type
    dollars: float
    x_stop: float


class PatternsResponse(BaseModel):
    tz: str
    as_of: UtcDatetime | None = None
    date_from: date_type | None = None
    date_to: date_type | None = None
    days: int
    net: float
    realized_before_fees: float = 0.0
    fees: float = 0.0
    green_days: int
    green_sum: float
    red_days: int
    red_sum: float
    n_closed: int
    n_wins: int
    n_losses: int
    win_pct: float | None = None  # actual
    avg_win: float | None = None
    avg_loss: float | None = None
    rr: float | None = None  # all closed positions pooled across days
    break_even_win_pct: float | None = None  # win rate that rr needs
    tag_counts: dict[str, int]
    late_days: list[date_type]
    late_fills: int
    stops: StopUsageOut
    biggest_risk: BiggestRisk | None = None
    lessons: list[RecurringLesson] = Field(default_factory=list)  # patterns on 2+ days


class LineToday(BaseModel):
    date: date_type
    net: float
    daily_stop: float
    daily_target: float
    stop_hit: bool
    target_hit: bool
    line_hit: bool
    window: WindowOut


class LineResponse(BaseModel):
    tz: str
    as_of: UtcDatetime | None = None
    now: UtcDatetime
    now_pt: str
    start_date: date_type
    amount: float
    pnl: float  # every closed trade since start_date (net, after fees)
    fees: float = 0.0  # fees on those same trades
    pnl_before_fees: float = 0.0  # pnl + fees
    left: float  # amount + min(0, pnl)
    trades_counted: int
    state: str  # before_window | in_window | closed
    today: LineToday
