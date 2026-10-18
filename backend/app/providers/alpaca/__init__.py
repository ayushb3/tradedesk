"""Read-only Alpaca market-data provider (bars only; no trading client, ever).

Selected by ``TRADEDESK_MARKET_DATA=alpaca`` in :func:`app.providers.base.get_provider`.
"""
from __future__ import annotations

from .errors import AlpacaConfigError, AlpacaError, AlpacaHTTPError
from .provider import AlpacaProvider
from .symbols import occ_symbol

__all__ = ["AlpacaProvider", "AlpacaError", "AlpacaConfigError", "AlpacaHTTPError", "occ_symbol"]
