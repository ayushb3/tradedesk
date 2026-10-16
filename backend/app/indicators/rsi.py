"""Relative Strength Index — Wilder's smoothing, strictly causal.

`null` until ``period`` price changes are available. The seed average gain/loss
(at bar ``period``) is the simple mean of the first ``period`` changes; every
later bar uses Wilder's recursive smoothing:
``avg = (avg*(period-1) + current) / period``. Output at index ``i`` depends only
on ``values[0..i]``.
"""
from __future__ import annotations


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")

    n = len(values)
    out: list[float | None] = [None] * n
    if n <= period:
        return out

    # First `period` changes cover indices 1..period; the first RSI lands at
    # bar index `period`.
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
