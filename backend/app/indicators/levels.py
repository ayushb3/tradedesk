"""Intraday reference levels — all strictly causal, aligned to the day's candles.

Every builder returns a list the same length as the day's ``candles``. Values
are ``null`` until knowable and, where a level is "past data" (PDH/PDL) or frozen
(PMH/PML after 09:30, OR after its window), the array holds a constant thereafter
— but no value is ever derived from a future bar. ET wall-clock classification is
DST-correct via :mod:`zoneinfo`.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.models.market import Candle

from ._et import (
    REG_CLOSE_MIN,
    REG_OPEN_MIN,
    et_minute_of_day,
    is_premarket,
    is_regular,
)


def _regular_hi_lo(candles: Sequence[Candle], tz: str) -> tuple[float | None, float | None]:
    """Regular-session (09:30–16:00 ET) high/low over a full day of candles."""
    hi: float | None = None
    lo: float | None = None
    for c in candles:
        if is_regular(et_minute_of_day(c.ts, tz)):
            hi = c.high if hi is None else max(hi, c.high)
            lo = c.low if lo is None else min(lo, c.low)
    return hi, lo


def pdh(
    candles: Sequence[Candle],
    prior_candles: Sequence[Candle],
    tz: str = "America/New_York",
) -> list[float | None]:
    """Prior trading day's regular-session high — constant (known from open)."""
    hi, _ = _regular_hi_lo(prior_candles, tz)
    return [hi] * len(candles)


def pdl(
    candles: Sequence[Candle],
    prior_candles: Sequence[Candle],
    tz: str = "America/New_York",
) -> list[float | None]:
    """Prior trading day's regular-session low — constant (known from open)."""
    _, lo = _regular_hi_lo(prior_candles, tz)
    return [lo] * len(candles)


def pmh(candles: Sequence[Candle], tz: str = "America/New_York") -> list[float | None]:
    """Premarket (04:00–09:30 ET) running high, frozen at the 09:30 value."""
    return _premarket_running(candles, tz, high=True)


def pml(candles: Sequence[Candle], tz: str = "America/New_York") -> list[float | None]:
    """Premarket (04:00–09:30 ET) running low, frozen at the 09:30 value."""
    return _premarket_running(candles, tz, high=False)


def _premarket_running(
    candles: Sequence[Candle], tz: str, high: bool
) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    running: float | None = None
    for i, c in enumerate(candles):
        etmin = et_minute_of_day(c.ts, tz)
        if is_premarket(etmin):
            val = c.high if high else c.low
            if running is None:
                running = val
            else:
                running = max(running, val) if high else min(running, val)
            out[i] = running
        elif etmin >= REG_OPEN_MIN:
            # Regular + after-hours: frozen at the final premarket extreme
            # (None if there were no premarket bars).
            out[i] = running
        # etmin < premarket open (shouldn't occur) stays None.
    return out


def or_high(
    candles: Sequence[Candle], or_minutes: int, tz: str = "America/New_York"
) -> list[float | None]:
    """Opening-range high: first ``or_minutes`` of the regular session, then frozen."""
    return _opening_range(candles, or_minutes, tz, high=True)


def or_low(
    candles: Sequence[Candle], or_minutes: int, tz: str = "America/New_York"
) -> list[float | None]:
    """Opening-range low: first ``or_minutes`` of the regular session, then frozen."""
    return _opening_range(candles, or_minutes, tz, high=False)


def _opening_range(
    candles: Sequence[Candle], or_minutes: int, tz: str, high: bool
) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    or_end = REG_OPEN_MIN + or_minutes
    running: float | None = None
    for i, c in enumerate(candles):
        etmin = et_minute_of_day(c.ts, tz)
        if etmin < REG_OPEN_MIN:
            continue  # before 09:30 -> null
        val = c.high if high else c.low
        if etmin < or_end:
            # Inside the OR window: extend the running extreme.
            if running is None:
                running = val
            else:
                running = max(running, val) if high else min(running, val)
        # After the window: running is unchanged (frozen). None only if the OR
        # window had no bars, which cannot happen given regular bars exist.
        out[i] = running
    return out


def day_high(candles: Sequence[Candle], tz: str = "America/New_York") -> list[float | None]:
    """Running regular-session high up to bar ``i`` (null before 09:30)."""
    return _day_running(candles, tz, high=True)


def day_low(candles: Sequence[Candle], tz: str = "America/New_York") -> list[float | None]:
    """Running regular-session low up to bar ``i`` (null before 09:30)."""
    return _day_running(candles, tz, high=False)


def _day_running(candles: Sequence[Candle], tz: str, high: bool) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    running: float | None = None
    for i, c in enumerate(candles):
        etmin = et_minute_of_day(c.ts, tz)
        if etmin < REG_OPEN_MIN:
            continue  # before the regular open -> null
        if is_regular(etmin):
            val = c.high if high else c.low
            if running is None:
                running = val
            else:
                running = max(running, val) if high else min(running, val)
        # After-hours (>= 16:00): frozen at the final regular-session extreme.
        out[i] = running
    return out
