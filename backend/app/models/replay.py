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


class CallerLevel(BaseModel):
    """A level a caller posted for the day (data/levels/YYYY-MM-DD.json)."""
    price: float
    kind: str = "level"  # level | ray | sma200_1h
    source: str = "somesh"


class ReplaySessionResponse(BaseModel):
    symbol: str
    session_date: date
    candles: list[Candle]

    # Each key -> array aligned with candles; value at i uses only candles[0..i].
    # indicators keys are dynamic: one ema<period> per requested EMA (default
    # ema9/ema21/ema50/ema200), always vwap, sma200 and sma400 (seeded with prior
    # sessions, null until a full window), and rsi<period> (default rsi14). The
    # replay route lets the client override the EMA periods and RSI period.
    indicators: dict[str, list[float | None]] = Field(default_factory=dict)

    # levels keys: pdh, pdl, pmh, pml, day_high, day_low, or_high, or_low
    # (pdh/pdl/pmh/pml are constant across the session but still returned as
    #  aligned arrays for uniform, testable causality.)
    levels: dict[str, list[float | None]] = Field(default_factory=dict)
    # Caller-posted levels for the day (constant; empty when none were saved).
    caller_levels: list[CallerLevel] = Field(default_factory=list)

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
    order_type: str | None = None  # "market" | "limit" | "stop"; None when the broker didn't say
    value: float  # gross dollars: qty x price x multiplier (x100 for options), before fees


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


class ReplaySecondsResponse(BaseModel):
    """Second-level bars for 'Replay my trade' (Seconds mode), cut at the playhead on
    the server: no bar covering a second after `playhead` is ever serialized, and
    nothing here (no fills, no window end, no result) can reveal the exit. `after`
    makes it incremental: only bars newer than that timestamp are sent."""

    trade_id: int
    available: bool
    reason: str | None = None
    res: str = "1s"                  # "1s" | "5s"
    playhead: UtcDatetime
    after: UtcDatetime | None = None
    start_ts: UtcDatetime | None = None  # first second of the window (lead start, whole minute)
    contract: list[Candle] = Field(default_factory=list)    # option premium bars (empty for a share trade)
    underlying: list[Candle] = Field(default_factory=list)  # underlying bars (SPY for index options)
    contract_label: str | None = None
    underlying_symbol: str | None = None
    source_label: str | None = None  # e.g. "SPY (proxy)" when the underlying is a stand-in


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


class ReplayNeighbors(BaseModel):
    """The trades before and after one trade, in time order across days. Ids only."""

    trade_id: int
    prev_id: int | None = None
    next_id: int | None = None


class ReplayDayResponse(BaseModel):
    """A whole day's replay: one underlying session. Carries no trade times, fills or
    results; those come from the playhead-gated /fills poll."""

    session_date: date
    symbol: str
    symbols: list[str] = Field(default_factory=list)  # every underlying traded that day
    trade_count: int = 0  # trades on `symbol` that day
    session: ReplaySessionResponse


class ReplayDayTrade(BaseModel):
    trade_id: int
    contract_label: str | None = None
    open_ts: UtcDatetime


class ReplayDayFill(ReplayFill):
    trade_id: int
    contract_label: str | None = None


class ReplayDayFillsResponse(BaseModel):
    """Fills (and trades entered) at or before `playhead` only, across the day."""

    session_date: date
    symbol: str
    playhead: UtcDatetime
    trades: list[ReplayDayTrade] = Field(default_factory=list)
    fills: list[ReplayDayFill] = Field(default_factory=list)


class StockQuoteResponse(BaseModel):
    """The underlying's latest bid/ask at or before `at` (historical NBBO-style quote).

    Nothing after `at` is ever looked up, so a replay asks with `at` <= its playhead.
    `available=false` (with a reason) when the provider has no quote source or no
    quote printed in the lookback; the client then shows the last price instead."""

    symbol: str
    at: UtcDatetime
    available: bool
    reason: str | None = None
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    ts: UtcDatetime | None = None        # when that quote printed (<= at)
    source_label: str | None = None      # e.g. "proxy for SPX"
