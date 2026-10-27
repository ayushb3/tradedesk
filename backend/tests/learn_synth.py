"""Synthetic 1m session builders for the Learn tests. Hand-made price paths, no real data."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.market import Candle

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
WICK = 0.05


def session_open(day: date) -> datetime:
    return datetime.combine(day, time(9, 30), ET).astimezone(UTC)


def path(waypoints: list[tuple[int, float]], n: int) -> list[float]:
    """Piecewise-linear closes through (index, price) waypoints, flat after the last one."""
    out = []
    for i in range(n):
        if i <= waypoints[0][0]:
            out.append(waypoints[0][1])
        elif i >= waypoints[-1][0]:
            out.append(waypoints[-1][1])
        else:
            for (i0, p0), (i1, p1) in zip(waypoints, waypoints[1:]):
                if i0 <= i <= i1:
                    out.append(p0 + (p1 - p0) * (i - i0) / (i1 - i0))
                    break
    return out


def candles(closes: list[float], day: date, symbol: str = "SPY",
            overrides: dict[int, tuple[float, float, float, float]] | None = None) -> list[Candle]:
    """OHLC from a close path: open = previous close, small wicks. ``overrides`` replaces whole
    bars (index -> (o, h, l, c))."""
    t0 = session_open(day)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o, h, l, cl = prev, max(prev, c) + WICK, min(prev, c) - WICK, c
        if overrides and i in overrides:
            o, h, l, cl = overrides[i]
        out.append(Candle(symbol=symbol, ts=t0 + timedelta(minutes=i), open=o, high=h, low=l,
                          close=cl, volume=1000.0))
        prev = cl
    return out


def full_day(waypoints, day: date, symbol: str = "SPY", n: int = 390, overrides=None):
    return candles(path(waypoints, n), day, symbol, overrides)


# Resistance at 600 touched twice (peaks at 20 and 40), price falls away, then returns.
def resistance_session(day: date, reject: bool, symbol: str = "SPY"):
    wp = [(0, 597.0), (20, 600.0), (30, 598.0), (40, 600.0), (52, 597.5), (60, 599.55),
          (61, 599.6), (62, 599.6), (63, 598.0), (75, 596.0), (110, 592.0), (389, 592.0)]
    over = {}
    if reject:
        over[62] = (599.55, 600.0, 599.0, 599.1)   # red rejection at the level
    else:
        over[62] = (599.4, 600.0, 599.35, 599.95)  # green push into the level, no rejection
    return full_day(wp, day, symbol, overrides=over)


# Resistance at 600 breaks up (close above), runs to 601.5, comes back to retest and holds.
def break_retest_session(day: date, confirm: bool, symbol: str = "SPY"):
    wp = [(0, 597.0), (20, 600.0), (30, 598.0), (40, 600.0), (50, 597.8), (58, 600.4),
          (62, 601.6), (70, 601.2), (76, 600.35), (77, 600.3), (78, 601.0), (120, 603.0),
          (140, 606.5), (389, 606.5)]
    over = {}
    if confirm:
        over[76] = (600.2, 600.6, 600.12, 600.5)   # holds the level, closes up
    else:
        over[76] = (600.45, 600.6, 600.12, 600.2)  # holds the level but closes down
    return full_day(wp, day, symbol, overrides=over)


# Pullback to support at ~595.15 (pivot low at bar 12), then a tiny candle sits on it (bar 50).
def patience_session(day: date, holds: bool, symbol: str = "SPY"):
    wp = [(0, 598.0), (12, 595.2), (22, 597.5), (34, 599.5), (47, 596.0), (48, 595.5),
          (49, 595.4), (50, 595.5), (51, 596.0), (70, 600.0), (100, 602.0), (130, 606.0),
          (389, 606.0)]
    over = {49: (595.9, 596.2, 595.05, 595.4)}
    if holds:
        over[50] = (595.42, 595.58, 595.32, 595.5)   # inside bar, closes above the level
    else:
        over[50] = (595.25, 595.35, 595.08, 595.1)   # closes through the level
    return full_day(wp, day, symbol, overrides=over)
