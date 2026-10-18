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


class ReplayTradeResponse(BaseModel):
    """'Replay my trade': the day's underlying session, started `lead_minutes`
    before the trade's entry, with fills gated by the playhead."""

    trade_id: int
    symbol: str
    lead_minutes: int
    start_ts: UtcDatetime  # entry time minus lead
    start_index: int       # first candle at or after start_ts
    playhead: UtcDatetime
    session: ReplaySessionResponse
    fills: list[ReplayFill]
