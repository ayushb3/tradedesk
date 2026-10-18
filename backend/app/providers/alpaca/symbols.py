"""Pure symbol/time helpers for the Alpaca provider (no I/O)."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.market import OptionContract

# Index underlyings have no free index bars on Alpaca; serve SPY and label it.
INDEX_PROXY = {"SPX": "SPY", "SPXW": "SPY"}
PROXY_LABEL = "SPY (proxy)"

_TIMEFRAMES = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "1h": "1Hour", "1d": "1Day"}


def alpaca_timeframe(tf: str) -> str:
    try:
        return _TIMEFRAMES[tf]
    except KeyError:
        raise ValueError(f"unsupported timeframe {tf!r}") from None


def stock_symbol_for(symbol: str) -> tuple[str, str | None]:
    """Return ``(symbol_to_fetch, source_label_or_None)``."""
    sym = symbol.upper()
    if sym in INDEX_PROXY:
        return INDEX_PROXY[sym], PROXY_LABEL
    return sym, None


def occ_symbol(contract: OptionContract) -> str:
    """OCC symbol: root + YYMMDD + C/P + strike*1000 zero-padded to 8 digits.

    ``SPY`` 600C exp 2026-06-19 -> ``SPY260619C00600000``. The root is the
    contract's underlying as given (use ``SPXW`` for weekly index options).
    """
    root = contract.underlying.upper()
    yymmdd = contract.expiry[2:4] + contract.expiry[5:7] + contract.expiry[8:10]
    cp = "C" if str(contract.option_type).upper() == "CALL" else "P"
    return f"{root}{yymmdd}{cp}{int(round(contract.strike * 1000)):08d}"


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
