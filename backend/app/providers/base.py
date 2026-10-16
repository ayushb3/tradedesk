"""DataProvider interface (frozen v1) + factory.

The single seam between the app and any market-data/broker source. Mock, Webull,
or any future provider (Polygon, yfinance) implements this Protocol. Nothing
outside `app/providers/` may import a vendor SDK.

READ-ONLY BY DESIGN: there is intentionally no place_order / cancel_order /
modify_order. Do not add order-entry methods.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from app.config import Settings, get_settings
from app.models.market import Account, Candle, OptionContract, Position
from app.models.trades import Fill


@runtime_checkable
class DataProvider(Protocol):
    def get_candles(self, symbol: str, day: date, timeframe: str = "1m") -> list[Candle]: ...

    def get_candles_range(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1m"
    ) -> list[Candle]: ...

    def get_option_bars(
        self, contract: OptionContract, day: date, timeframe: str = "1m"
    ) -> list[Candle] | None:
        """Historical premium bars for one option contract, or ``None`` if the
        provider has no option-bar source (M7b). ``None`` triggers the server-
        side synthetic Black-Scholes fallback. Read-only, like everything here.
        """
        ...

    def get_fills(self, since: datetime | None = None) -> list[Fill]: ...

    def get_positions(self) -> list[Position]: ...

    def get_account(self) -> Account: ...


def get_provider(settings: Settings | None = None) -> DataProvider:
    """Return the active provider based on settings.mode. Imports are lazy so a
    missing Webull SDK never breaks mock mode (and vice-versa)."""
    settings = settings or get_settings()
    if settings.mode == "webull":
        from app.providers.webull import WebullProvider

        return WebullProvider(settings)
    from app.providers.mock import MockProvider

    return MockProvider(settings)
