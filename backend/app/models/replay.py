"""Replay-session response contract.

CRITICAL no-lookahead invariant: every array in `indicators` and `levels` is
aligned index-for-index with `candles`. The value at index i is computed using
ONLY candles[0..i] (inclusive). A value is `null` until it is knowable
(e.g. opening-range high before the OR window closes). The frontend reveals
these arrays progressively and MUST NOT recompute forward.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models.market import Candle
from app.models.trades import Side
from app.models.utc import UtcDatetime


class ReplaySessionResponse(BaseModel):
    symbol: str
    session_date: date
    candles: list[Candle]

    # Each key -> array aligned with candles; value at i uses only candles[0..i].
    # indicators keys are dynamic: one ema<period> per requested EMA (default
    # ema9/ema21/ema50/ema200), always vwap, and rsi<period> (default rsi14). The
    # replay route lets the client override the EMA periods and RSI period.
    indicators: dict[str, list[float | None]] = Field(default_factory=dict)

    # levels keys: pdh, pdl, pmh, pml, day_high, day_low, or_high, or_low
    # (pdh/pdl/pmh/pml are constant across the session but still returned as
    #  aligned arrays for uniform, testable causality.)
    levels: dict[str, list[float | None]] = Field(default_factory=dict)

    meta: dict = Field(default_factory=dict)  # {"or_minutes": int, "tz": str, "timeframe": str}


class RealResult(BaseModel):
    """The real trade's NET result (fees in, the one R definition). Sent only once
    the playhead is at or past the exit; null on every replay endpoint before it."""

    net_pnl: float
    r: float | None = None
    r_basis: str | None = None  # "written" | "assumed"


class ReplayFill(BaseModel):
    """A trade fill revealed to the replay. Never carries P&L."""

    seq: int
    role: str  # "entry" | "exit"
    side: Side
    qty: float
    price: float
    ts: UtcDatetime
    time_pt: str  # e.g. "06:47:12 PT" (display zone, labelled)


class ReplayFillsResponse(BaseModel):
    """Fills at or before `playhead` only. Later fills are never serialized."""

    trade_id: int
    playhead: UtcDatetime
    fills: list[ReplayFill]
    window_end_ts: UtcDatetime | None = None  # set only once playhead >= exit
    real_result: RealResult | None = None  # same gate as window_end_ts


class ReplayTradeResponse(BaseModel):
    """'Replay my trade': the day's underlying session, started `lead_minutes`
    before the trade's entry, with fills gated by the playhead."""

    trade_id: int
    symbol: str
    lead_minutes: int
    start_ts: UtcDatetime  # entry time minus lead
    start_index: int       # first candle at or after start_ts
    window_end_ts: UtcDatetime | None = None  # exit + 30 min, only once playhead >= exit
    real_result: RealResult | None = None  # same gate as window_end_ts
    playhead: UtcDatetime
    session: ReplaySessionResponse
    fills: list[ReplayFill]


class ReplayContractResponse(BaseModel):
    """'Replay my trade', Contract mode: the trade's own option premium 1-min
    bars on the same playhead, started `lead_minutes` before entry. Fills are
    gated by the playhead exactly as in the underlying mode. No P&L."""

    trade_id: int
    available: bool
    reason: str | None = None
    symbol: str | None = None        # contract descriptor, when known
    session_date: date | None = None
    lead_minutes: int
    start_ts: UtcDatetime
    start_index: int = 0
    window_end_ts: UtcDatetime | None = None  # exit + 30 min, only once playhead >= exit
    real_result: RealResult | None = None  # same gate as window_end_ts
    playhead: UtcDatetime
    candles: list[Candle] = Field(default_factory=list)
    stop_price: float | None = None  # premium level at which the written/default risk is lost
    stop_assumed: bool = False       # True when no max loss was written: stop_price is the default-risk stop
    synthetic: bool = False          # bars are a model, not provider prints
    estimated: bool = False          # same flag, named for the UI badge
    method: str | None = None        # "provider" | "bs_interp"
    fills: list[ReplayFill] = Field(default_factory=list)


class DaySoFarResponse(BaseModel):
    """The replayed day as it stood BEFORE this trade's entry, for the rule bar.

    Aggregates only: positions fully closed strictly before the entry (never this
    trade). No later trade, no end-of-day stop/target flag, no final day net."""

    trade_id: int
    session_date: date
    net_so_far: float | None = None  # None when the rules file is unreadable
    prior_trades: int = 0
    daily_stop: float | None = None
    daily_target: float | None = None
    cutoff: str | None = None  # "HH:MM" PT
    entry: str | None = None  # "before" | "after" the cutoff
    call_source: str | None = None  # the trade's mentor/own field, else its plan's caller
