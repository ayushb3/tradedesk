"""Simple moving average — strictly causal.

``None`` until a full ``period`` window exists, then the plain mean of the last
``period`` values. Never a partial average. Every output at index ``i`` depends
only on ``values[0..i]``.
"""
from __future__ import annotations


def sma(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def sma_with_warmup(
    warmup: list[float], values: list[float], period: int
) -> list[float | None]:
    """SMA over ``values`` seeded with earlier closes (e.g. prior sessions), so the
    line is valid from the first bar when enough history exists. Returns a series
    aligned with ``values`` only; still ``None`` wherever the window isn't full."""
    return sma(list(warmup) + list(values), period)[len(warmup):]
