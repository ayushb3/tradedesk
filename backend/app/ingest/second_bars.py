"""Second-level bars for 'Replay my trade' (the Seconds mode).

Glue between the trade, the provider's tick-trade aggregation and the replay
window. This module returns the WHOLE replay window; it is never serialized as
is. The replay route cuts it at the playhead (``app.providers.seconds.revealed``)
before anything leaves the server, so the exit time and later prices cannot be
derived from the response.

Window: ``lead`` minutes before the entry (floored to the minute, so the first
minute is complete) to 30 minutes after the exit, bounded by the 20:00 ET session end.
Read-only; the only network access is the provider's market-data GETs.
"""
from __future__ import annotations

import time as _time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine

from app.ingest.option_bars import cache_source, get_option_bars_for_trade
from app.ingest.query import get_trade, get_trade_option_contract
from app.models.market import Candle
from app.providers.seconds import SecondsUnavailable

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")
AFTER_EXIT_MINUTES = 30
NO_SOURCE = "this data source has no trade prints"

_MEMO_TTL_S = 600.0
_MEMO_MAX = 8
# Whole-window results for providers that fetch over the network, so a 1 s poll does
# not re-read the cache or the API. Server-side only; never sent as a whole.
_memo: "OrderedDict[tuple, tuple[float, TradeSeconds]]" = OrderedDict()


def clear_memo() -> None:
    _memo.clear()


@dataclass
class TradeSeconds:
    available: bool
    reason: str | None = None
    start: datetime | None = None
    contract: list[Candle] = field(default_factory=list)
    underlying: list[Candle] = field(default_factory=list)
    underlying_symbol: str | None = None
    contract_label: str | None = None
    source_label: str | None = None


def _as_utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)).astimezone(UTC)


def replay_window(trade, lead_minutes: int) -> tuple[datetime, datetime]:
    """(start, end) of the seconds window for a trade, in UTC."""
    start = (_as_utc(trade.open_ts) - timedelta(minutes=lead_minutes)).replace(second=0, microsecond=0)
    session_end = datetime.combine(trade.session_date, time(20, 0), ET).astimezone(UTC)
    if trade.close_ts is None:
        return start, session_end
    return start, min(_as_utc(trade.close_ts) + timedelta(minutes=AFTER_EXIT_MINUTES), session_end)


def get_trade_seconds(
    trade_id: int, provider, lead_minutes: int, engine: Engine | None = None
) -> TradeSeconds | None:
    """Second bars for the trade's contract and underlying over its whole replay window.
    ``None`` when the trade does not exist."""
    from app.db.connection import get_engine

    engine = engine or get_engine()
    trade = get_trade(trade_id, engine=engine)
    if trade is None:
        return None

    memoize = bool(getattr(provider, "memoize_seconds", False))
    key = (cache_source(provider), str(engine.url), trade_id, lead_minutes)
    if memoize:
        hit = _memo.get(key)
        if hit is not None and _time.monotonic() - hit[0] < _MEMO_TTL_S:
            _memo.move_to_end(key)
            return hit[1]

    result = _build(trade, trade_id, provider, lead_minutes, engine)
    if memoize and result.available:
        _memo[key] = (_time.monotonic(), result)
        while len(_memo) > _MEMO_MAX:
            _memo.popitem(last=False)
    return result


def _build(trade, trade_id: int, provider, lead_minutes: int, engine: Engine) -> TradeSeconds:
    start, end = replay_window(trade, lead_minutes)
    if not hasattr(provider, "get_second_bars"):
        return TradeSeconds(available=False, reason=NO_SOURCE, start=start)

    reasons: list[str] = []
    underlying: list[Candle] = []
    try:
        underlying = provider.get_second_bars(trade.symbol.upper(), start, end)
    except SecondsUnavailable as exc:
        reasons.append(str(exc))

    contract_bars: list[Candle] = []
    contract = get_trade_option_contract(trade_id, engine=engine)
    if contract is not None:
        try:
            contract_bars = _contract_seconds(trade_id, contract, provider, start, end, engine)
        except SecondsUnavailable as exc:
            reasons.append(str(exc))

    if not underlying and not contract_bars:
        reason = reasons[0] if reasons else "no trade prints in the replay window"
        return TradeSeconds(available=False, reason=reason, start=start)
    label = next((b.source_label for b in underlying if b.source_label), None)
    return TradeSeconds(
        available=True, start=start, contract=contract_bars, underlying=underlying,
        underlying_symbol=trade.symbol.upper(),
        contract_label=contract.descriptor if contract is not None else None,
        source_label=label,
    )


def _contract_seconds(trade_id, contract, provider, start, end, engine) -> list[Candle]:
    if getattr(provider, "synthesizes_seconds", False):
        # Mock: split the very same minute bars the Contract endpoint serves, so the
        # seconds agree with the minutes on screen.
        bars = get_option_bars_for_trade(trade_id, provider, "1m", engine)
        if bars is None or not bars.bars:
            raise SecondsUnavailable("option bars unavailable")
        return provider.seconds_from_minutes(bars.bars, start, end)
    fn = getattr(provider, "get_option_second_bars", None)
    if fn is None:
        raise SecondsUnavailable(NO_SOURCE)
    return fn(contract, start, end)
