"""Pure sparkline downsampling for the journal day-drill-in cards (M7a).

`downsample_closes` slices a day's underlying candles to a trade's hold window
and returns a small, uniformly strided list of close prices for the tiny card
sparkline. It is deliberately HTTP-free so it can be unit-tested directly
(CONTRACT §3).

Causality note: this only reads close prices of candles that already exist in
the window; it introduces no forward-looking data.
"""
from __future__ import annotations

from datetime import datetime

from app.models.market import Candle
from app.models.review import Sparkline

MAX_SPARK_POINTS = 40


def _as_naive_utc(ts: datetime) -> datetime:
    """Drop tzinfo so tz-aware candle ts (UTC) and naive UTC trade ts compare.

    Both sides are UTC by storage convention; candle `ts` is tz-aware UTC while
    trade `open_ts`/`close_ts` come back naive from SQLite. Normalize to naive.
    """
    if ts.tzinfo is not None:
        return ts.replace(tzinfo=None)
    return ts


def downsample_closes(
    candles: list[Candle],
    open_ts: datetime,
    close_ts: datetime | None,
    max_points: int = MAX_SPARK_POINTS,
) -> Sparkline | None:
    """Downsample underlying close prices over `[open_ts, close_ts]`.

    - Window is `[open_ts, close_ts]` inclusive, clamped to available candles.
      When `close_ts` is None (open trade) the window runs through the last
      candle.
    - Closes are taken in chronological order and downsampled by a uniform
      stride to at most `max_points` values (all of them if the window has
      `<= max_points` candles).
    - Returns `None` when the window contains zero candles.
    """
    if not candles:
        return None

    lo = _as_naive_utc(open_ts)
    hi = _as_naive_utc(close_ts) if close_ts is not None else None

    window = [
        c
        for c in candles
        if _as_naive_utc(c.ts) >= lo and (hi is None or _as_naive_utc(c.ts) <= hi)
    ]
    if not window:
        return None

    window.sort(key=lambda c: _as_naive_utc(c.ts))

    n = len(window)
    if n <= max_points:
        picked = window
    else:
        # Uniform stride: pick indices spread across the window, always
        # including the first and last candle.
        step = (n - 1) / (max_points - 1)
        idxs = sorted({round(i * step) for i in range(max_points)})
        picked = [window[i] for i in idxs]

    values = [c.close for c in picked]
    return Sparkline(
        symbol=picked[0].symbol,
        timeframe=picked[0].timeframe,
        values=values,
        t0=_as_naive_utc(picked[0].ts),
        t1=_as_naive_utc(picked[-1].ts),
    )
