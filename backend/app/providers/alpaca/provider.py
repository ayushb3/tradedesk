"""Read-only Alpaca market-data provider behind the DataProvider protocol.

Market data only (bars). ``get_fills`` / ``get_positions`` / ``get_account`` are
delegated to the mode provider (mock or webull) -- Alpaca is never a broker here.
Results are cached in ``candles_cache`` / ``option_bars_cache`` /
``second_bars_cache``; a session that is not yet closed is never cached.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from app.config import Settings
from app.db.schema import (
    candles_cache,
    option_bars_cache,
    second_bars_cache,
    second_bars_coverage,
)
from app.models.market import Account, Candle, OptionContract, Position
from app.models.trades import Fill

from app.providers.seconds import SecondsUnavailable, aggregate_ticks, ticks_from_alpaca

from .client import AlpacaDataClient
from .errors import AlpacaHTTPError
from .symbols import alpaca_timeframe, occ_symbol, rfc3339, stock_symbol_for

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")
PROVIDER_NAME = "alpaca"
_FEEDS = ("iex", "sip")
# Option bars exist from about Feb 2024; earlier days return empty without a request.
OPTION_HISTORY_START = date(2024, 2, 1)
# Free plan blocks SIP for the latest 15 minutes.
SIP_DELAY = timedelta(minutes=15)
# Hard stop on trade-print pagination (10k prints a page) so a busy window stays bounded.
_MAX_TRADE_PAGES = 80


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _session_window(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time(4, 0), ET).astimezone(UTC),
        datetime.combine(day, time(20, 0), ET).astimezone(UTC),
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _is_closed(day: date, now: datetime | None = None) -> bool:
    """True once the whole 04:00-20:00 ET session (plus 15 min SIP delay) is over."""
    now = (now or _now()).astimezone(UTC)
    return now >= _session_window(day)[1] + timedelta(minutes=15)


class AlpacaProvider:
    #: Second-bar windows come over the network: callers may keep them in memory.
    memoize_seconds = True

    def __init__(
        self,
        settings: Settings,
        client: AlpacaDataClient | None = None,
        engine: Engine | None = None,
        base=None,
    ) -> None:
        self.settings = settings
        self.client = client or AlpacaDataClient()
        self._engine = engine
        self._base = base
        self.feed = (getattr(settings, "alpaca_feed", "sip") or "sip").lower()
        if self.feed not in _FEEDS:
            self.feed = "iex"
        self.last_feed_used: str | None = None

    # -- plumbing ------------------------------------------------------- #
    def _eng(self) -> Engine:
        if self._engine is None:
            from app.db.connection import get_engine, init_db

            self._engine = get_engine()
            init_db(self._engine)
        return self._engine

    def _delegate(self):
        if self._base is None:
            if self.settings.mode == "webull":
                from app.providers.webull import WebullProvider

                self._base = WebullProvider(self.settings)
            else:
                from app.providers.mock import MockProvider

                self._base = MockProvider(self.settings)
        return self._base

    # -- stock bars ----------------------------------------------------- #
    def _fetch_stock(
        self, sym: str, start: datetime, end: datetime, timeframe: str,
        label: str | None, out_symbol: str,
    ) -> list[Candle]:
        feed = self.feed
        if feed == "sip" and end > datetime.now(UTC) - SIP_DELAY:
            feed = "iex"
        params = {
            "timeframe": alpaca_timeframe(timeframe),
            "start": rfc3339(start),
            "end": rfc3339(end),
            "limit": 10000,
            "adjustment": "raw",
            "feed": feed,
        }
        path = f"/v2/stocks/{sym}/bars"
        try:
            pages = list(self.client.pages(path, params))
            self.last_feed_used = params["feed"]
        except AlpacaHTTPError as exc:
            if exc.status == 403 and params["feed"] != "iex":
                params["feed"] = "iex"
                pages = list(self.client.pages(path, params))
                self.last_feed_used = "iex"
            else:
                raise
        out: list[Candle] = []
        for body in pages:
            for b in body.get("bars") or []:
                out.append(
                    Candle(
                        symbol=out_symbol, ts=_parse_ts(b["t"]), open=b["o"], high=b["h"],
                        low=b["l"], close=b["c"], volume=b["v"], timeframe=timeframe,
                        source_label=label,
                    )
                )
        out.sort(key=lambda c: c.ts)
        return out

    def get_candles(self, symbol: str, day: date, timeframe: str = "1m") -> list[Candle]:
        out_symbol = symbol.upper()
        sym, label = stock_symbol_for(out_symbol)
        cached = self._read_candles(sym, timeframe, day)
        if cached:
            return [
                c.model_copy(update={"symbol": out_symbol, "source_label": label})
                for c in cached
            ]
        start, end = _session_window(day)
        bars = self._fetch_stock(sym, start, end, timeframe, label, out_symbol)
        if bars and _is_closed(day):
            self._write_candles(sym, timeframe, day, bars)
        return bars

    def get_candles_range(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1m"
    ) -> list[Candle]:
        def u(dt: datetime) -> datetime:
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

        s, e = u(start), u(end)
        if e < s:
            return []
        out_symbol = symbol.upper()
        sym, label = stock_symbol_for(out_symbol)
        bars = self._fetch_stock(sym, s, e, timeframe, label, out_symbol)
        return [c for c in bars if s <= c.ts <= e]

    # -- option bars ---------------------------------------------------- #
    def get_option_bars(
        self, contract: OptionContract, day: date, timeframe: str = "1m"
    ) -> list[Candle] | None:
        key = contract.contract_key
        if day < OPTION_HISTORY_START:
            return []
        cached = self._read_option(key, timeframe, day)
        if cached:
            return cached
        occ = occ_symbol(contract)
        start, end = _session_window(day)
        # The free plan answers 403 for any request whose end is inside the latest
        # 15 minutes (or in the future). Same-day reviews run while the extended
        # session is still open, so clamp the end to what the plan may serve.
        end = min(end, _now() - SIP_DELAY - timedelta(minutes=1))
        if end <= start:
            return None
        params = {
            "symbols": occ,
            "timeframe": alpaca_timeframe(timeframe),
            "start": rfc3339(start),
            "end": rfc3339(end),
            "limit": 10000,
        }
        try:
            pages = list(self.client.pages("/v1beta1/options/bars", params))
        except AlpacaHTTPError as exc:
            if exc.status in (400, 403, 404, 422):
                return None  # no option-bar source for this contract -> synthetic fallback
            raise
        bars: list[Candle] = []
        for body in pages:
            for b in (body.get("bars") or {}).get(occ, []):
                bars.append(
                    Candle(
                        symbol=key, ts=_parse_ts(b["t"]), open=b["o"], high=b["h"],
                        low=b["l"], close=b["c"], volume=b["v"], timeframe=timeframe,
                    )
                )
        if not bars:
            return None
        bars.sort(key=lambda c: c.ts)
        if _is_closed(day):
            self._write_option(key, timeframe, bars)
        return bars

    # -- second bars (aggregated from tick trades) ----------------------- #
    def get_second_bars(self, symbol: str, start: datetime, end: datetime) -> list[Candle]:
        """1-second bars for the stock (or its labelled proxy) over [start, end].

        Raises :class:`SecondsUnavailable` when the plan/source has no prints.
        """
        out_symbol = symbol.upper()
        sym, label = stock_symbol_for(out_symbol)

        def fetch(s: datetime, e: datetime) -> list[dict]:
            feed = self.feed
            if feed == "sip" and e > _now() - SIP_DELAY:
                feed = "iex"
            params = {"start": rfc3339(s), "end": rfc3339(e), "limit": 10000, "feed": feed}
            path = f"/v2/stocks/{sym}/trades"
            try:
                pages = self._trade_pages(path, params)
            except AlpacaHTTPError as exc:
                if exc.status == 403 and feed != "iex":
                    params["feed"] = "iex"
                    pages = self._trade_pages(path, params)
                else:
                    raise
            return [r for body in pages for r in (body.get("trades") or [])]

        return self._seconds(sym, out_symbol, label, start, end, fetch)

    def get_option_second_bars(
        self, contract: OptionContract, start: datetime, end: datetime
    ) -> list[Candle]:
        """1-second premium bars for one option contract over [start, end]."""
        occ = occ_symbol(contract)

        def fetch(s: datetime, e: datetime) -> list[dict]:
            params = {"symbols": occ, "start": rfc3339(s), "end": rfc3339(e), "limit": 10000}
            pages = self._trade_pages("/v1beta1/options/trades", params)
            return [r for body in pages for r in (body.get("trades") or {}).get(occ, [])]

        return self._seconds(contract.contract_key, contract.contract_key, None, start, end, fetch)

    def _trade_pages(self, path: str, params: dict) -> list[dict]:
        pages: list[dict] = []
        for body in self.client.pages(path, params):
            pages.append(body)
            if len(pages) >= _MAX_TRADE_PAGES and body.get("next_page_token"):
                # A partial window would silently end early (and must never be cached).
                raise SecondsUnavailable("too many trade prints in the replay window")
        return pages

    def _seconds(self, series_key, out_symbol, label, start, end, fetch) -> list[Candle]:
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        day = start.astimezone(ET).date()
        if day < OPTION_HISTORY_START:
            raise SecondsUnavailable("no trade prints for that date")
        cached = self._read_seconds(series_key, out_symbol, label, start, end)
        if cached is not None:
            return cached
        # The free plan 403s any request whose end is inside the latest 15 minutes.
        usable_end = min(end, _now() - SIP_DELAY - timedelta(minutes=1))
        if usable_end <= start:
            raise SecondsUnavailable("trade prints are delayed 15 minutes on this plan")
        try:
            rows = fetch(start, usable_end)
        except AlpacaHTTPError as exc:
            if exc.status in (400, 403, 404, 422):
                raise SecondsUnavailable("trade prints are not available on this plan") from None
            raise
        bars = aggregate_ticks(ticks_from_alpaca(rows), out_symbol, 1, label)
        if usable_end == end and _is_closed(day):
            self._write_seconds(series_key, bars, start, end)
        return bars

    def _read_seconds(self, key, out_symbol, label, start, end) -> list[Candle] | None:
        cov, t = second_bars_coverage, second_bars_cache
        s, e = start.replace(tzinfo=None), end.replace(tzinfo=None)
        with self._eng().connect() as conn:
            covered = conn.execute(
                select(cov.c.start_utc).where(
                    cov.c.source == PROVIDER_NAME, cov.c.series_key == key,
                    cov.c.start_utc <= s, cov.c.end_utc >= e,
                ).limit(1)
            ).first()
            if covered is None:
                return None
            rows = conn.execute(
                select(t).where(
                    t.c.source == PROVIDER_NAME, t.c.series_key == key,
                    t.c.ts_utc >= s, t.c.ts_utc <= e,
                ).order_by(t.c.ts_utc)
            ).fetchall()
        return [
            Candle(
                symbol=out_symbol, ts=m["ts_utc"].replace(tzinfo=UTC), open=m["open"], high=m["high"], low=m["low"],
                close=m["close"], volume=m["volume"], timeframe="1s", source_label=label,
            )
            for m in (r._mapping for r in rows)
        ]

    def _write_seconds(self, key: str, bars: list[Candle], start: datetime, end: datetime) -> None:
        with self._eng().begin() as conn:
            for b in bars:
                vals = dict(
                    source=PROVIDER_NAME, series_key=key,
                    ts_utc=b.ts.astimezone(UTC).replace(tzinfo=None),
                    open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
                )
                stmt = sqlite_insert(second_bars_cache).values(**vals)
                conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["source", "series_key", "ts_utc"],
                        set_={k: v for k, v in vals.items()
                              if k not in ("source", "series_key", "ts_utc")},
                    )
                )
            conn.execute(
                sqlite_insert(second_bars_coverage)
                .values(source=PROVIDER_NAME, series_key=key,
                        start_utc=start.replace(tzinfo=None), end_utc=end.replace(tzinfo=None))
                .on_conflict_do_nothing()
            )

    # -- cache ---------------------------------------------------------- #
    def _read_candles(self, sym: str, timeframe: str, day: date) -> list[Candle]:
        t = candles_cache
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(t)
                .where(
                    t.c.symbol == sym, t.c.timeframe == timeframe,
                    t.c.provider == PROVIDER_NAME, t.c.session_date == day,
                )
                .order_by(t.c.ts_utc)
            ).fetchall()
        return [
            Candle(
                symbol=sym, ts=m["ts_utc"], open=m["open"], high=m["high"], low=m["low"],
                close=m["close"], volume=m["volume"], timeframe=timeframe,
            )
            for m in (r._mapping for r in rows)
        ]

    def _write_candles(self, sym: str, timeframe: str, day: date, bars: list[Candle]) -> None:
        with self._eng().begin() as conn:
            for b in bars:
                vals = dict(
                    symbol=sym, timeframe=timeframe,
                    ts_utc=b.ts.astimezone(UTC).replace(tzinfo=None),
                    open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
                    provider=PROVIDER_NAME, session_date=day,
                )
                stmt = sqlite_insert(candles_cache).values(**vals)
                conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["symbol", "timeframe", "ts_utc"],
                        set_={k: v for k, v in vals.items()
                              if k not in ("symbol", "timeframe", "ts_utc")},
                    )
                )

    def _read_option(self, key: str, timeframe: str, day: date) -> list[Candle]:
        t = option_bars_cache
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(t)
                .where(
                    t.c.contract_key == key, t.c.timeframe == timeframe,
                    t.c.method == "provider", t.c.synthetic.is_(False),
                    t.c.source == PROVIDER_NAME,
                )
                .order_by(t.c.ts_utc)
            ).fetchall()
        return [
            Candle(
                symbol=key, ts=m["ts_utc"], open=m["open"], high=m["high"], low=m["low"],
                close=m["close"], volume=m["volume"], timeframe=timeframe,
            )
            for m in (r._mapping for r in rows)
            if m["ts_utc"].replace(tzinfo=UTC).astimezone(ET).date() == day
        ]

    def _write_option(self, key: str, timeframe: str, bars: list[Candle]) -> None:
        with self._eng().begin() as conn:
            for b in bars:
                vals = dict(
                    contract_key=key, timeframe=timeframe,
                    ts_utc=b.ts.astimezone(UTC).replace(tzinfo=None),
                    open=b.open, high=b.high, low=b.low, close=b.close, volume=b.volume,
                    synthetic=False, method="provider", source=PROVIDER_NAME,
                )
                stmt = sqlite_insert(option_bars_cache).values(**vals)
                conn.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["contract_key", "timeframe", "ts_utc"],
                        set_={k: v for k, v in vals.items()
                              if k not in ("contract_key", "timeframe", "ts_utc")},
                    )
                )

    # -- not market data: delegate to the mode provider ------------------ #
    def get_fills(self, since: datetime | None = None) -> list[Fill]:
        return self._delegate().get_fills(since)

    def get_positions(self) -> list[Position]:
        return self._delegate().get_positions()

    def get_account(self) -> Account:
        return self._delegate().get_account()
