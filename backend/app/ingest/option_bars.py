"""Option premium bars: provider seam + synthetic fallback + cache (M7b).

Orchestrates the premium-series data path for one option trade:

    provider.get_option_bars(...)  ->  bars           (synthetic=False, "provider")
        provider bars off the fills' scale, or returns None
                                   ->  BS fallback     (synthetic=True,  "bs_interp")

A provider series is only used verbatim when its median close is on the same
scale as the trade's recorded fills; otherwise (or when the provider has no
option-bar source and returns None) we use the fill-anchored Black-Scholes
fallback, which provably passes through the actual executions.

Results are cached into ``option_bars_cache`` keyed by the canonical
``contract_key`` + timeframe. Reads are filtered to the trade's session day so a
multi-session contract can't leak bars across days (PRD-M7 risk #3 restricts the
chart to the close-date session). Read-only throughout.
"""
from __future__ import annotations

from datetime import date, datetime
from statistics import median
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from app.analytics.options import bs_interp_premium_bars
from app.db.connection import get_engine, init_db
from app.db.schema import option_bars_cache as cache_t
from app.ingest.query import get_trade, get_trade_fills, get_trade_option_contract
from app.models.market import Candle, OptionContract
from app.providers.webull.errors import WebullError

UTC = ZoneInfo("UTC")

# A provider's synthesized premium series is only trustworthy if it sits on the
# same order of magnitude as the trade's recorded fills. When the provider
# curve's median is more than this multiple away from the fills' median (either
# direction) we treat it as scale-incompatible and fall back to the
# fill-anchored BS series, which provably passes through the real executions.
# This catches gross (orders-of-magnitude) mismatches — e.g. the mock
# synthesizing a deep-ITM ~7300 curve for an index option struck at 7575 whose
# random-walk underlying is on an equity (~$260) scale — without disturbing a
# provider series that merely wandered a little in-the-money.
_PROVIDER_FILL_SCALE_TOL = 25.0


class OptionBars(NamedTuple):
    bars: list[Candle]
    synthetic: bool
    method: str  # "provider" | "bs_interp"


def _as_utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)).astimezone(UTC)


def _read_cache(
    conn, contract_key: str, timeframe: str, session_day: date
) -> OptionBars | None:
    rows = conn.execute(
        select(cache_t)
        .where(
            and_(
                cache_t.c.contract_key == contract_key,
                cache_t.c.timeframe == timeframe,
            )
        )
        .order_by(cache_t.c.ts_utc)
    ).fetchall()
    # Filter to the trade's session day (the PK has no session column).
    day_rows = [r for r in rows if _as_utc(r._mapping["ts_utc"]).date() == session_day]
    if not day_rows:
        return None
    bars = [
        Candle(
            symbol=contract_key,
            ts=m["ts_utc"],
            open=m["open"],
            high=m["high"],
            low=m["low"],
            close=m["close"],
            volume=m["volume"],
            timeframe=timeframe,
        )
        for m in (r._mapping for r in day_rows)
    ]
    first = day_rows[0]._mapping
    return OptionBars(bars=bars, synthetic=bool(first["synthetic"]), method=first["method"])


def _write_cache(
    conn, contract_key: str, timeframe: str, bars: list[Candle], synthetic: bool, method: str
) -> None:
    for b in bars:
        stmt = sqlite_insert(cache_t).values(
            contract_key=contract_key,
            timeframe=timeframe,
            ts_utc=_as_utc(b.ts).replace(tzinfo=None),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            synthetic=synthetic,
            method=method,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["contract_key", "timeframe", "ts_utc"],
            set_={
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "synthetic": synthetic,
                "method": method,
            },
        )
        conn.execute(stmt)


def _synthetic_fallback(
    provider,
    contract: OptionContract,
    trade_id: int,
    session_day: date,
    timeframe: str,
    engine: Engine,
    fills: list[tuple[datetime, float]] | None = None,
) -> OptionBars | None:
    """BS premium series anchored to the trade's recorded fills.

    Returns ``None`` when the underlying can't be fetched — e.g. an index-option
    root like SPXW, which Webull rejects as ``US_STOCK`` (417 INVALID_SYMBOL).
    Without underlying candles there is no curve to anchor, so callers degrade to
    "no option bars" rather than escalating a decorative fetch failure to a 502.
    """
    try:
        underlying = provider.get_candles(contract.underlying, session_day, timeframe)
    except WebullError:
        return None
    if fills is None:
        recorded = get_trade_fills(trade_id, engine=engine)
        fills = [(f.ts, f.price) for f in recorded]
    bars = bs_interp_premium_bars(underlying, contract, fills, timeframe)
    return OptionBars(bars=bars, synthetic=True, method="bs_interp")


def _provider_series_matches_fills(
    bars: list[Candle], fill_prices: list[float]
) -> bool:
    """True if the provider premium series sits on the fills' price scale.

    Compares median close to median fill price; a series more than
    ``_PROVIDER_FILL_SCALE_TOL``x off (either way) is scale-incompatible (e.g.
    a deep-ITM curve for an index option whose strike dwarfs the mock's
    equity-scale underlying) and should defer to the fill-anchored fallback.
    Missing bars or fills → treat as non-matching (prefer the anchored series).
    """
    if not bars or not fill_prices:
        return False
    fill_med = median(fill_prices)
    bar_med = median(b.close for b in bars)
    if fill_med <= 0 or bar_med <= 0:
        return False
    ratio = bar_med / fill_med
    return (1.0 / _PROVIDER_FILL_SCALE_TOL) <= ratio <= _PROVIDER_FILL_SCALE_TOL


def get_option_bars_for_trade(
    trade_id: int,
    provider,
    timeframe: str = "1m",
    engine: Engine | None = None,
    use_cache: bool = True,
) -> OptionBars | None:
    """Premium bars for a trade's option contract, cached.

    ``use_cache=False`` skips both the cache read and write (used by read-only
    callers such as the MCP tools, which must not mutate the database).

    Returns ``None`` when the trade doesn't exist OR isn't an option trade (the
    route maps both to 404). Otherwise returns the bars plus provenance
    (``synthetic`` / ``method``), reading the cache first and populating it on a
    miss.
    """
    engine = engine or get_engine()
    init_db(engine)

    trade = get_trade(trade_id, engine=engine)
    if trade is None:
        return None
    contract = get_trade_option_contract(trade_id, engine=engine)
    if contract is None:
        return None

    session_day = trade.session_date
    with engine.begin() as conn:
        if use_cache:
            cached = _read_cache(conn, contract.contract_key, timeframe, session_day)
            if cached is not None:
                return cached

        bars = provider.get_option_bars(contract, session_day, timeframe)
        if bars is not None:
            # Trust the provider series only if it's on the fills' scale.
            # Otherwise (e.g. mock synthesizing a deep-ITM curve for an index
            # option whose strike dwarfs its equity-scale underlying) defer to
            # the fill-anchored fallback so the curve honors real executions.
            recorded = get_trade_fills(trade_id, engine=engine)
            fill_anchors = [(f.ts, f.price) for f in recorded]
            fill_prices = [p for _, p in fill_anchors]
            # With no fills to judge against, keep the provider series as-is
            # (nothing to anchor a fallback to anyway).
            if not fill_prices or _provider_series_matches_fills(bars, fill_prices):
                result = OptionBars(bars=bars, synthetic=False, method="provider")
            else:
                result = _synthetic_fallback(
                    provider, contract, trade_id, session_day, timeframe, engine,
                    fills=fill_anchors,
                )
        else:
            result = _synthetic_fallback(
                provider, contract, trade_id, session_day, timeframe, engine
            )

        if use_cache and result is not None and result.bars:
            _write_cache(
                conn,
                contract.contract_key,
                timeframe,
                result.bars,
                result.synthetic,
                result.method,
            )
    return result


def get_cached_option_bars_for_trade(
    trade_id: int, timeframe: str = "1m", engine: Engine | None = None
) -> OptionBars | None:
    """Cache-only read: premium bars already stored for a trade's contract.

    Never calls a provider and never writes. ``None`` when the trade is unknown,
    is not an option trade, or nothing is cached for its session day.
    """
    engine = engine or get_engine()
    init_db(engine)
    trade = get_trade(trade_id, engine=engine)
    if trade is None:
        return None
    contract = get_trade_option_contract(trade_id, engine=engine)
    if contract is None:
        return None
    with engine.connect() as conn:
        return _read_cache(conn, contract.contract_key, timeframe, trade.session_date)
