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
    hold_time_s: int | None = None
    grade: Grade | None = None
    open_ts: UtcDatetime
    close_ts: UtcDatetime | None = None
    simulated: bool = False
    sparkline: Sparkline | None = None


class DayTradesResponse(BaseModel):
    date: str  # ISO date
    trades: list[DayTradeCard] = Field(default_factory=list)
