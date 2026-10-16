"""Exponential moving average — strictly causal.

`null` for the first ``period-1`` bars, the seed bar (``period-1``) is the SMA of
the first ``period`` values, then the standard recurrence
``e = v*k + prev*(1-k)`` with ``k = 2/(period+1)``. Every output at index ``i``
depends only on ``values[0..i]``.
"""
from __future__ import annotations


def ema(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")

    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out

    k = 2.0 / (period + 1)

    # Seed the first defined bar with the SMA of the first `period` values.
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out
