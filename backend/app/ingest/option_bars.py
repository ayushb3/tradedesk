"""Option premium bars: provider seam + synthetic fallback + cache (M7b).

Orchestrates the premium-series data path for one option trade:

    provider.get_option_bars(...)  ->  bars           (synthetic=False, "provider")
        provider bars off the fills' scale, or returns None
                                   ->  BS fallback     (synthetic=True,  "bs_interp")

A provider series is only used verbatim when it is on the trade's scale AT THE
FILLS: each fill price must sit inside (with tolerance) the range of the bars
around that fill's minute. A whole-day comparison is wrong for 0DTE: a contract
bought for cents near the close traded for dollars in the morning. Otherwise (or
when the provider has no option-bar source and returns None) we use the
fill-anchored Black-Scholes fallback, which provably passes through the actual
executions -- unless the underlying it would price off is on a different scale
than the strike (an SPX/SPXW contract on the SPY proxy), in which case there is
no honest synthetic series and the result is "no premium data".

Results are cached into ``option_bars_cache`` keyed by the canonical
``contract_key`` + timeframe. Reads are filtered to the trade's session day so a
multi-session contract can't leak bars across days (PRD-M7 risk #3 restricts the
chart to the close-date session). Read-only throughout.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
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

# A provider premium series is trusted only if it sits on the fills' scale AT
# THE FILLS. For each fill we take the provider bars within
# ``_FILL_WINDOW_MIN`` minutes of the fill's minute; the fill matches when its
# price lies inside that window's [low / F, high x F] with F =
# ``_FILL_SCALE_FACTOR``: a scale check (orders of magnitude), not a print
# match -- thin 0DTE bars and fills a few minutes off a sparse bar still pass.
# The series is kept when at least half of the judged fills match. Fills with
# no provider bar nearby are not judged; if no fill can be judged at all the
# series is not trusted. Catches gross mismatches (a deep-ITM curve for an
# index option on an equity-scale underlying) without rejecting a real 0DTE
# series that was dollars in the morning and cents at the fills.
_FILL_WINDOW_MIN = 3
_FILL_SCALE_FACTOR = 25.0

# The BS fallback needs the underlying on the strike's scale. An SPX/SPXW
# contract priced off the SPY proxy (~1/10 of SPX) comes out at its intrinsic
# value (thousands of dollars). When the provider served the underlying as a
# labelled proxy (``Candle.source_label``) and its median close / strike falls
# outside this band -> no synthetic series ("no premium data").
_UNDERLYING_STRIKE_BAND = (0.5, 2.0)


class OptionBars(NamedTuple):
    bars: list[Candle]
    synthetic: bool
    method: str  # "provider" | "bs_interp"


def _as_utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)).astimezone(UTC)


def cache_source(provider) -> str:
    """Stable cache key for a provider: ``cache_source`` attr, else the class name
    minus "Provider" (MockProvider -> "mock", AlpacaProvider -> "alpaca")."""
    explicit = getattr(provider, "cache_source", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return type(provider).__name__.removesuffix("Provider").lower() or "unknown"


def _read_cache(
    conn, contract_key: str, timeframe: str, session_day: date, source: str
) -> OptionBars | None:
    """Cached bars for one source only. Rows from another provider (or NULL =
    unknown source) are never returned; provider bars beat synthetic ones."""
    rows = conn.execute(
        select(cache_t)
        .where(
            and_(
                cache_t.c.contract_key == contract_key,
                cache_t.c.timeframe == timeframe,
                cache_t.c.source == source,
            )
        )
        .order_by(cache_t.c.ts_utc)
    ).fetchall()
    # Filter to the trade's session day (the PK has no session column).
    day_rows = [r for r in rows if _as_utc(r._mapping["ts_utc"]).date() == session_day]
    if not day_rows:
        return None
    real_rows = [r for r in day_rows if not r._mapping["synthetic"]]
    if real_rows:
        day_rows = real_rows
    else:
        day_rows = [r for r in day_rows if r._mapping["method"] == day_rows[0]._mapping["method"]]
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
    conn, contract_key: str, timeframe: str, bars: list[Candle], synthetic: bool, method: str,
    source: str,
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
            source=source,
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
                "source": source,
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
    if not _underlying_on_strike_scale(underlying, contract):
        return None
    if fills is None:
        recorded = get_trade_fills(trade_id, engine=engine)
        fills = [(f.ts, f.price) for f in recorded]
    bars = bs_interp_premium_bars(underlying, contract, fills, timeframe)
    return OptionBars(bars=bars, synthetic=True, method="bs_interp")


def _underlying_on_strike_scale(underlying: list[Candle], contract: OptionContract) -> bool:
    """False for an index contract whose underlying came back as an off-scale proxy.

    An SPXW strike ~7000 on SPY ~700: pricing BS off it yields the option's
    intrinsic value, not a premium. Only proxied candles (``source_label`` set)
    are judged; a real underlying, or no candles, -> True.
    """
    if not any(c.source_label for c in underlying):
        return True
    closes = [c.close for c in underlying if c.close and c.close > 0]
    if not closes or contract.strike <= 0:
        return True
    lo, hi = _UNDERLYING_STRIKE_BAND
    return lo <= median(closes) / contract.strike <= hi


def _provider_series_matches_fills(
    bars: list[Candle], fills: list[tuple[datetime, float]]
) -> bool:
    """True if the provider premium series sits on the fills' scale at the fills.

    Judged per fill against the bars within ``_FILL_WINDOW_MIN`` minutes of the
    fill's minute (bar stamps are bar opens), never against the whole day: a
    0DTE contract can be dollars at the open and cents at the fills. A fill
    matches when its price is inside the window's
    [low / _FILL_SCALE_FACTOR, high x _FILL_SCALE_FACTOR]. Kept when at least half the judged fills match; no bars
    or no judgeable fill -> not matching (prefer the anchored series).
    """
    if not bars or not fills:
        return False
    stamped = [(_as_utc(b.ts), b) for b in bars]
    reach = timedelta(minutes=_FILL_WINDOW_MIN)
    judged = matched = 0
    for ts, price in fills:
        if price is None or price <= 0:
            continue
        minute = _as_utc(ts).replace(second=0, microsecond=0)
        near = [b for t, b in stamped if minute - reach <= t <= minute + reach]
        if not near:
            continue
        judged += 1
        lo = min(b.low for b in near) / _FILL_SCALE_FACTOR
        hi = max(b.high for b in near) * _FILL_SCALE_FACTOR
        if lo <= price <= hi:
            matched += 1
    return judged > 0 and 2 * matched >= judged


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
    source = cache_source(provider)
    with engine.begin() as conn:
        if use_cache:
            cached = _read_cache(conn, contract.contract_key, timeframe, session_day, source)
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
            # With no fills to judge against, keep the provider series as-is
            # (nothing to anchor a fallback to anyway).
            if not fill_anchors or _provider_series_matches_fills(bars, fill_anchors):
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
                source,
            )
    return result


def get_cached_option_bars_for_trade(
    trade_id: int,
    timeframe: str = "1m",
    engine: Engine | None = None,
    provider=None,
    source: str | None = None,
) -> OptionBars | None:
    """Cache-only read: premium bars already stored for a trade's contract.

    Only rows from the active provider's source are returned: pass ``provider`` (or
    an explicit ``source``); with neither, the configured provider is used.

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
    if source is None:
        if provider is None:
            from app.providers.base import get_provider

            provider = get_provider()
        source = cache_source(provider)
    with engine.connect() as conn:
        return _read_cache(conn, contract.contract_key, timeframe, trade.session_date, source)
