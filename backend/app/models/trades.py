"""Trade/journal domain models (shared by live + simulated trades)."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class Side(str, Enum):
    buy = "buy"
    sell = "sell"


class Direction(str, Enum):
    long = "long"
    short = "short"


class TradeStatus(str, Enum):
    open = "open"
    closed = "closed"


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class Fill(BaseModel):
    """A single execution. `simulated=True` for replay-generated fills."""

    id: int | None = None
    provider_fill_id: str | None = None
    account: str | None = None
    symbol: str
    side: Side
    qty: float
    price: float
    ts: datetime  # UTC
    fees: float = 0.0
    multiplier: float = 1.0  # options contract multiplier (100 for equity options, 1 for stocks)
    # Option contract identity — extracted from legs[0] by the Webull mapper.
    # None for stock fills (no legs field).
    option_type: str | None = None   # "CALL" | "PUT"
    strike: float | None = None       # strike price
    expiry: str | None = None         # ISO date string, e.g. "2026-07-09"
    simulated: bool = False
    raw_json: dict | None = None


class Tag(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    active: bool = True


class TradeEnrichment(BaseModel):
    notes: str | None = None
    grade: Grade | None = None
    checklist: list[dict] = Field(default_factory=list)  # [{"item": str, "checked": bool}]
    screenshots: list[str] = Field(default_factory=list)  # file paths under data/screenshots/


class Trade(BaseModel):
    """A round-trip trade built from ordered fills (FIFO)."""

    id: int | None = None
    symbol: str
    # Human-readable contract label: "SPXW 7500C 07/09" for options, ticker
    # for stocks.  Set by _rebuild_trades; None on pre-fix rows and sim trades.
    contract_label: str | None = None
    direction: Direction
    open_ts: datetime
    close_ts: datetime | None = None
    session_date: date  # ET trading date
    qty: float
    avg_entry: float
    avg_exit: float | None = None
    gross_pnl: float | None = None
    fees: float = 0.0
    net_pnl: float | None = None
    pnl_pct: float | None = None
    stop_price: float | None = None
    r_multiple: float | None = None
    hold_time_s: int | None = None
    status: TradeStatus = TradeStatus.closed
    simulated: bool = False
    tags: list[Tag] = Field(default_factory=list)
    enrichment: TradeEnrichment = Field(default_factory=TradeEnrichment)


class TradeUpdate(BaseModel):
    """PATCH /api/trades/{id} — manual enrichment. All fields optional."""

    notes: str | None = None
    grade: Grade | None = None
    stop_price: float | None = None
    tag_ids: list[int] | None = None
    checklist: list[dict] | None = None  # [{"item": str, "checked": bool}]
    screenshots: list[str] | None = None


class TagCreate(BaseModel):
    name: str
    description: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    active: bool | None = None


class SimTradeCreate(BaseModel):
    """Payload for POST /api/sim/trades — a marked replay trade."""

    symbol: str
    direction: Direction
    session_date: date
    entries: list[dict]  # [{"ts": iso, "qty": float, "price": float}]
    exits: list[dict]
    stop_price: float | None = None
    notes: str | None = None
