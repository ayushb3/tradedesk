"""Trade review + journal day-drill-in models (M7a).

New router-owned schemas for:
    GET /api/trades/{trade_id}/review  -> TradeReviewResponse
    GET /api/journal/days/{date}/trades -> DayTradesResponse

Kept separate from `app/models/trades.py` (frozen M3 contract) per
`.agents/m7a/CONTRACT.md` §2 so the existing `Trade` model is reused verbatim,
not redefined here.

Option/premium fields are stubbed `None` in M7a (share trades only) but present
in the schema from day one so M7b can populate them without a breaking change.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.market import Candle
from app.models.trades import Direction, Grade, Side, Trade
from app.models.utc import UtcDatetime


class ReviewFill(BaseModel):
    """One execution belonging to a reviewed trade, with entry/exit role."""

    id: int
    seq: int  # trade_fills.seq — FIFO membership order
    side: Side
    qty: float
    price: float
    ts: UtcDatetime  # UTC
    fees: float = 0.0
    role: str  # "entry" | "exit"


class ReviewOption(BaseModel):
    """Option contract descriptor. Populated in M7b for option trades."""

    contract_key: str
    descriptor: str
    option_type: str
    strike: float
    expiry: str
    synthetic: bool = False
    # Provenance of premium_bars: "provider" (provider-supplied) or "bs_interp"
    # (server-side Black-Scholes fallback). None only in the M7a stub case.
    method: str | None = None


class Excursion(BaseModel):
    """Max favorable / adverse excursion over the hold window (PRD-M7 R9).

    Computed from the premium series for option trades (``basis="premium"``) or
    the underlying for share trades (``basis="underlying"``). ``estimated`` is
    true when the premium series is synthetic (BS fallback), so the UI can label
    the readout as an estimate.
    """

    mfe: float          # max favorable excursion, price points (>= 0)
    mae: float          # max adverse excursion, price points (>= 0)
    mfe_pct: float      # as % of entry price
    mae_pct: float
    basis: str          # "premium" | "underlying"
    estimated: bool = False


class UnderlyingDescriptor(BaseModel):
    symbol: str
    session_date: str  # ISO date
    timeframes: list[str] = Field(default_factory=lambda: ["1m", "5m"])


class TradeReviewResponse(BaseModel):
    trade: Trade
    fills: list[ReviewFill] = Field(default_factory=list)
    underlying: UnderlyingDescriptor
    option: ReviewOption | None = None
    premium_bars: list[Candle] | None = None
    excursion: Excursion | None = None


class OptionBarsResponse(BaseModel):
    """`GET /api/trades/{id}/option-bars` payload."""

    bars: list[Candle] = Field(default_factory=list)
    synthetic: bool = False
    method: str  # "provider" | "bs_interp"


class Sparkline(BaseModel):
    symbol: str
    timeframe: str
    values: list[float]
    t0: UtcDatetime
    t1: UtcDatetime


class DayTradeCard(BaseModel):
    id: int
    symbol: str
    contract_label: str | None = None
    direction: Direction
    net_pnl: float | None = None
    pnl_pct: float | None = None
    r_multiple: float | None = None
    r_basis: str | None = None  # "written" | "assumed"
    hold_time_s: int | None = None
    grade: Grade | None = None
    open_ts: UtcDatetime
    close_ts: UtcDatetime | None = None
    simulated: bool = False
    sparkline: Sparkline | None = None


class DayTradesResponse(BaseModel):
    date: str  # ISO date
    trades: list[DayTradeCard] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Trade excursions (market data 3, #51): GET /api/trades/{id}/excursions
# --------------------------------------------------------------------------- #
class ExcursionFill(BaseModel):
    """One fill of the position, listed even when option bars are unavailable."""

    side: str  # "B" | "S"
    qty: int
    px: float
    ts: UtcDatetime
    ts_pt: str  # clock time in PT


class HoldExcursionOut(BaseModel):
    """MAE/MFE over the hold on the position's average cost (signed: MAE <= 0)."""

    mae_usd: float
    mfe_usd: float
    mae_r: float | None = None
    mfe_r: float | None = None
    mae_per_contract: float
    mfe_per_contract: float
    mae_ts: UtcDatetime | None = None
    mfe_ts: UtcDatetime | None = None
    mae_ts_pt: str | None = None
    mfe_ts_pt: str | None = None
    drawdown_minutes: int
    crossed_stop: bool
    crossed_stop_at: UtcDatetime | None = None
    crossed_stop_at_pt: str | None = None
    crossed_stop_minutes: int | None = None  # minutes after the first fill
    # Set when the data-quality check trimmed wicks: these are the values as printed.
    raw_mae_r: float | None = None
    raw_mfe_r: float | None = None


class AfterExitOut(BaseModel):
    """Did the premium get back to the average entry within the window after the final exit.
    Direction only: no dollars."""

    window_min: int
    bars: int = 0
    complete: bool = False
    returned_at: UtcDatetime | None = None
    returned_at_pt: str | None = None
    minutes_after_exit: int | None = None


class PostExitWindowOut(BaseModel):
    minutes: int
    best_r: float | None = None
    worst_r: float | None = None
    bars: int = 0
    complete: bool = False


class TradeExcursionsResponse(BaseModel):
    """Excursions for one option position. ``available`` is false (with a
    ``reason``) when option bars could not be had; fills are still listed.
    There is intentionally no dollars-left-on-the-table field."""

    trade_id: int
    tz: str = "America/Los_Angeles"
    tz_label: str = "PT"
    available: bool
    reason: str | None = None
    contract: str | None = None
    estimated: bool = False  # premium bars are a Black-Scholes fallback
    method: str | None = None
    status: str  # "open" | "closed"
    avg_in: float | None = None
    qty: int = 0
    risk: float | None = None  # same dollars-at-risk the ledger uses for R
    risk_source: str  # "written_max_loss" | "default_risk_pct"
    r_basis: str = "assumed"  # "written" | "assumed" (same fact as risk_source)
    stop_price: float | None = None  # premium where the position has lost `risk`
    fills: list[ExcursionFill] = Field(default_factory=list)
    hold: HoldExcursionOut | None = None
    post_exit: list[PostExitWindowOut] = Field(default_factory=list)
    after_exit: AfterExitOut | None = None
    # Data quality (thin 1-min bars): "ok" | "suspect_wicks" | "short_hold" (hold inside <= 2 bars:
    # MAE/MFE from the fills alone). When suspect, `hold` is
    # computed on bars trimmed to their body extremes (`clamped`), the raw R values are on
    # `hold.raw_mae_r` / `hold.raw_mfe_r`, and `quality_reason` says what was found.
    quality: str = "ok"
    quality_reason: str | None = None
    clamped: bool = False
    suspect_wicks: int = 0
