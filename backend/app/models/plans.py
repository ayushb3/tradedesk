"""Plan card models (issue #9)."""
from __future__ import annotations

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel

from .utc import UtcDatetime

PlanStatus = Literal["open", "matched", "unmatched"]


class Plan(BaseModel):
    id: int
    created_at: UtcDatetime
    created_pt: str  # "HH:MM PT"
    session_date: date_type
    caller: str
    symbol: str
    option_type: Literal["CALL", "PUT"] | None = None
    strike: float | None = None
    expiry: date_type | None = None
    size_usd: float
    max_loss_usd: float
    target: str | None = None
    note: str = ""
    status: PlanStatus = "open"
    late: bool = False
    matched_trade_id: int | None = None


class PlanCreate(BaseModel):
    caller: str
    contract: str  # e.g. "SPY 580P" or "SPY 580P 10/17"
    size_usd: float
    max_loss_usd: float
    target: str | None = None
    note: str = ""
    session_date: date_type | None = None  # defaults to today (PT)
