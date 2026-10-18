"""Market-data domain models (provider-agnostic)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field
from app.models.utc import UtcDatetime


class Candle(BaseModel):
    """A single OHLCV bar. `ts` is the bar's OPEN time in UTC."""

    symbol: str
    ts: UtcDatetime  # UTC, bar open time
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1m"
    # Set when the bars are a stand-in, e.g. "SPY (proxy)" for SPX. Optional and
    # omitted by most providers (backward compatible).
    source_label: str | None = None


class OptionContract(BaseModel):
    """Identity of a single option contract (M7b premium charts).

    Carries the underlying ticker plus the contract's type/strike/expiry. The
    canonical ``contract_key`` (``SYMBOL_YYMMDD_C|P_STRIKE``) matches the
    journal's per-contract FIFO grouping and is the PK component in
    ``option_bars_cache``.
    """

    underlying: str            # ticker the option is written on, e.g. "SPY"
    option_type: str           # "CALL" | "PUT"
    strike: float
    expiry: str                # ISO date, e.g. "2026-07-09"

    @staticmethod
    def _fmt_strike(strike: float) -> str:
        return str(int(strike)) if strike == int(strike) else str(strike)

    @property
    def contract_key(self) -> str:
        """Canonical grouping key: ``SPY_260709_C_500`` (see PRD-M7 §5.3)."""
        cp = "C" if str(self.option_type).upper() == "CALL" else "P"
        yymmdd = self.expiry[2:4] + self.expiry[5:7] + self.expiry[8:10]
        return f"{self.underlying}_{yymmdd}_{cp}_{self._fmt_strike(self.strike)}"

    @property
    def descriptor(self) -> str:
        """Human label: ``SPY 500C 07/09`` (mirrors trades.contract_label)."""
        cp = "C" if str(self.option_type).upper() == "CALL" else "P"
        mmdd = f"{self.expiry[5:7]}/{self.expiry[8:10]}" if len(self.expiry) >= 10 else self.expiry
        return f"{self.underlying} {self._fmt_strike(self.strike)}{cp} {mmdd}"


class Position(BaseModel):
    symbol: str
    qty: float
    avg_price: float
    market_value: float | None = None
    unrealized_pnl: float | None = None


class Account(BaseModel):
    account_id: str
    currency: str = "USD"
    cash: float | None = None
    equity: float | None = None
    buying_power: float | None = None
    positions: list[Position] = Field(default_factory=list)
