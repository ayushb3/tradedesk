"""Typed errors for the Alpaca market-data provider. Messages never contain keys."""
from __future__ import annotations


class AlpacaError(RuntimeError):
    """Base class for Alpaca provider errors."""


class AlpacaConfigError(AlpacaError):
    """Alpaca was selected but API keys are missing."""


class AlpacaHTTPError(AlpacaError):
    """Non-success HTTP response from the market-data API."""

    def __init__(self, status: int, message: str = "") -> None:
        self.status = status
        super().__init__(f"Alpaca market-data request failed (HTTP {status}) {message}".strip())
