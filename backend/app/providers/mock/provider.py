"""Deterministic in-memory market-data/broker provider.

``MockProvider`` implements the full :class:`app.providers.base.DataProvider`
protocol with zero external dependencies. Candles come from
:func:`app.providers.mock.synthetic.generate_candles`; fills, positions and the
account are derived from those same candles so everything is internally
consistent (fill prices always sit inside their day's real bar range).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from app.analytics.options import synthesize_mock_premium_bars
from app.config import Settings
from app.models.market import Account, Candle, OptionContract, Position
from app.models.trades import Fill, Side

from .synthetic import generate_candles, resample_candles, seed_for

UTC = ZoneInfo("UTC")


def _as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to tz-aware UTC (naive is assumed to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class MockProvider:
    """A fully deterministic, offline data provider for mock mode."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tz = getattr(settings, "tz", "America/New_York")

    # ------------------------------------------------------------------ #
    # Candles
    # ------------------------------------------------------------------ #
    def get_candles(self, symbol: str, day: date, timeframe: str = "1m") -> list[Candle]:
        """Return one session (04:00–20:00 ET) of deterministic bars.

        Bars are generated at 1-minute resolution then aggregated to ``timeframe``
        (5m/15m/1h/1d) so the candle count and shape change like a real feed.
        """
        base = generate_candles(symbol, day, timeframe="1m", tz=self.tz)
        return resample_candles(base, timeframe, tz=self.tz)

    def get_candles_range(
        self, symbol: str, start: datetime, end: datetime, timeframe: str = "1m"
    ) -> list[Candle]:
        """Return bars whose open time falls in ``[start, end]`` (inclusive).

        Generates every ET calendar day the range touches and filters. Handles
        multi-day ranges. ``start``/``end`` may be naive (treated as UTC) or
        tz-aware.
        """
        start_u = _as_utc(start)
        end_u = _as_utc(end)
        if end_u < start_u:
            return []

        # Widen the day span by one on each side so ET/UTC offset never clips
        # bars that belong inside the window.
        day = start_u.date() - timedelta(days=1)
        last = end_u.date() + timedelta(days=1)
        out: list[Candle] = []
        while day <= last:
            for c in self.get_candles(symbol, day, timeframe):
                ts = _as_utc(c.ts)
                if start_u <= ts <= end_u:
                    out.append(c)
            day += timedelta(days=1)
        out.sort(key=lambda c: c.ts)
        return out

    # ------------------------------------------------------------------ #
    # Option premium bars (M7b)
    # ------------------------------------------------------------------ #
    def get_option_bars(
        self, contract: OptionContract, day: date, timeframe: str = "1m"
    ) -> list[Candle] | None:
        """Deterministic synthesized premium series for one option contract.

        Derived from this provider's own underlying candles (Black-Scholes with
        a contract-seeded IV), so it is reproducible per contract key and always
        sits in a plausible range. Never returns ``None`` in mock mode — the
        synthetic fallback is exercised by providers (webull) that have no
        option-bar source.
        """
        underlying = self.get_candles(contract.underlying, day, timeframe)
        if not underlying:
            return []
        return synthesize_mock_premium_bars(underlying, contract, timeframe)

    # ------------------------------------------------------------------ #
    # Fills
    # ------------------------------------------------------------------ #
    def get_fills(self, since: datetime | None = None) -> list[Fill]:
        """Return a deterministic set of ~24 fake fills over the last 10 trading
        days for the watch tickers.

        The fills form realistic round-trips: winners and losers, at least one
        short (sell-to-open then buy-to-cover), and a scale-in/scale-out
        example. ``simulated=False``; every price sits inside that day's real
        synthetic candle range. Filtered by ``since`` when provided.
        """
        tickers = self.settings.watch_list or ["RIVN", "HOOD"]
        days = _last_trading_days(10)

        def tk(i: int) -> str:
            return tickers[i % len(tickers)]

        # (ticker_index, day_index, kind) trade templates. Kinds:
        #   long_win / long_lose  -> buy then sell
        #   short_win / short_lose -> sell then buy (short)
        #   scale                 -> buy, buy, sell, sell (scale in/out)
        templates = [
            (0, 1, "long_win"),
            (0, 3, "long_lose"),
            (0, 5, "short_win"),
            (0, 7, "scale"),
            (0, 9, "long_win"),
            (1, 2, "long_win"),
            (1, 4, "short_lose"),
            (1, 6, "long_lose"),
            (1, 8, "long_win"),
            (1, 9, "scale"),
        ]

        fills: list[Fill] = []
        fid = 1
        # Entry near the opening drive; index 0 == 04:00 ET, so 09:35 ET == 335.
        entry_idx = 335
        for tkr_i, day_i, kind in templates:
            symbol = tk(tkr_i)
            day = days[day_i]
            candles = self.get_candles(symbol, day)
            rng = np.random.default_rng(seed_for(symbol, day))
            qty = int(rng.integers(50, 400))

            if kind in ("long_win", "long_lose"):
                want_win = kind == "long_win"
                exit_idx = _find_exit(candles, entry_idx, want_win, long=True)
                fills.append(_leg(fid, symbol, Side.buy, qty, candles[entry_idx]))
                fid += 1
                fills.append(_leg(fid, symbol, Side.sell, qty, candles[exit_idx]))
                fid += 1
            elif kind in ("short_win", "short_lose"):
                # Short: sell to open, buy to cover. Winner => cover lower.
                want_win = kind == "short_win"
                exit_idx = _find_exit(candles, entry_idx, want_win, long=False)
                fills.append(_leg(fid, symbol, Side.sell, qty, candles[entry_idx]))
                fid += 1
                fills.append(_leg(fid, symbol, Side.buy, qty, candles[exit_idx]))
                fid += 1
            else:  # scale in / scale out
                q1, q2 = qty, int(qty // 2)
                i0, i1 = entry_idx, entry_idx + 20
                x0, x1 = entry_idx + 90, entry_idx + 150
                fills.append(_leg(fid, symbol, Side.buy, q1, candles[i0]))
                fid += 1
                fills.append(_leg(fid, symbol, Side.buy, q2, candles[i1]))
                fid += 1
                fills.append(_leg(fid, symbol, Side.sell, q2, candles[x0]))
                fid += 1
                fills.append(_leg(fid, symbol, Side.sell, q1, candles[x1]))
                fid += 1

        fills.sort(key=lambda f: f.ts)
        if since is not None:
            since_u = _as_utc(since)
            fills = [f for f in fills if _as_utc(f.ts) >= since_u]
        return fills

    # ------------------------------------------------------------------ #
    # Positions / account
    # ------------------------------------------------------------------ #
    def get_positions(self) -> list[Position]:
        """Small plausible deterministic open positions for the watch tickers."""
        day = _last_trading_days(1)[0]
        positions: list[Position] = []
        for i, symbol in enumerate(self.settings.watch_list or ["RIVN", "HOOD"]):
            candles = self.get_candles(symbol, day)
            last = candles[-1].close
            rng = np.random.default_rng(seed_for(symbol, day) ^ 0xA5A5)
            qty = float(int(rng.integers(50, 500)))
            avg = round(last * (1.0 - float(rng.uniform(-0.05, 0.05))), 2)
            mv = round(qty * last, 2)
            positions.append(
                Position(
                    symbol=symbol,
                    qty=qty,
                    avg_price=avg,
                    market_value=mv,
                    unrealized_pnl=round(qty * (last - avg), 2),
                )
            )
        return positions

    def get_account(self) -> Account:
        """A small plausible deterministic account snapshot."""
        positions = self.get_positions()
        mv = sum(p.market_value or 0.0 for p in positions)
        cash = 25_000.00
        equity = round(cash + mv, 2)
        return Account(
            account_id="MOCK-0001",
            currency="USD",
            cash=cash,
            equity=equity,
            buying_power=round(cash * 4, 2),  # nominal 4x day-trade BP
            positions=positions,
        )


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def _last_trading_days(n: int, ref: date | None = None) -> list[date]:
    """Return the last ``n`` weekday dates ending at ``ref`` (default today),
    oldest first. Weekends are skipped (holidays are ignored for the mock)."""
    ref = ref or date.today()
    days: list[date] = []
    d = ref
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _find_exit(candles: list[Candle], entry_idx: int, want_win: bool, long: bool) -> int:
    """Find an exit bar producing a winning or losing round-trip.

    For a long, a winner exits above entry; for a short, a winner exits below
    entry. Searches a plausible same-session window and falls back to the bar
    with the best/worst close if no exact match is found.
    """
    entry = candles[entry_idx].close
    lo = entry_idx + 30
    hi = min(entry_idx + 240, len(candles) - 1)
    profitable_higher = want_win if long else (not want_win)
    best = lo
    best_close = candles[lo].close
    for j in range(lo, hi + 1):
        c = candles[j].close
        if profitable_higher and c > entry:
            return j
        if not profitable_higher and c < entry:
            return j
        # track fallback extreme in the desired direction
        if profitable_higher and c > best_close:
            best, best_close = j, c
        if not profitable_higher and c < best_close:
            best, best_close = j, c
    return best


def _leg(fid: int, symbol: str, side: Side, qty: int, candle: Candle) -> Fill:
    """Build one :class:`Fill` from a candle (price = its close, inside range)."""
    return Fill(
        id=fid,
        provider_fill_id=f"mock-{fid:06d}",
        account="MOCK-0001",
        symbol=symbol,
        side=side,
        qty=float(qty),
        price=round(float(candle.close), 2),
        ts=candle.ts,
        fees=round(0.0035 * qty, 2),  # nominal per-share commission
        simulated=False,
    )
