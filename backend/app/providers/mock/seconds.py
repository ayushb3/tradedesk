"""Deterministic second bars that agree with a minute bar (mock mode).

Each 1-minute bar is split into up to 60 one-second bars whose aggregate is exactly
the minute bar: first open = minute open, last close = minute close, max high =
minute high, min low = minute low, volumes sum to the minute volume. A quiet second
produces no bar, like a real tick feed. Same inputs always give the same output.
"""
from __future__ import annotations

import zlib
from datetime import timedelta
from typing import Iterable

import numpy as np

from app.models.market import Candle
from app.providers.seconds import as_utc

_SKIP_PROB = 0.12


def split_minute_bar(bar: Candle) -> list[Candle]:
    minute = as_utc(bar.ts)
    seed = (zlib.crc32(bar.symbol.encode()) ^ int(minute.timestamp())) & 0xFFFFFFFF
    rng = np.random.default_rng(seed)
    o, c = bar.open, bar.close
    hi, lo = max(bar.high, o, c), min(bar.low, o, c)
    n = 60

    walk = np.concatenate([[0.0], np.cumsum(rng.normal(size=n))])
    bridge = walk - (np.arange(n + 1) / n) * walk[-1]
    peak = float(np.max(np.abs(bridge))) or 1.0
    path = o + (c - o) * np.arange(n + 1) / n + bridge / peak * (hi - lo) * 0.35
    path = np.clip(path, lo, hi)
    path[0], path[n] = o, c

    ih, il = (int(i) for i in rng.choice(n, size=2, replace=False))
    must = {0, n - 1, ih, il}
    skip = rng.random(n) < _SKIP_PROB
    weights = rng.random(n) + 0.1
    wick = rng.random((n, 2))

    rows: list[tuple[int, float, float, float, float]] = []
    for i in range(n):
        if skip[i] and i not in must:
            continue
        a, b = float(path[i]), float(path[i + 1])
        h = min(hi, max(a, b) + wick[i, 0] * (hi - max(a, b)) * 0.25)
        l = max(lo, min(a, b) - wick[i, 1] * (min(a, b) - lo) * 0.25)
        if i == ih:
            h = hi
        if i == il:
            l = lo
        rows.append((i, a, h, l, b))
    total_w = sum(weights[i] for i, *_ in rows)
    return [
        Candle(
            symbol=bar.symbol, ts=minute + timedelta(seconds=i), open=a, high=h, low=l, close=b,
            volume=bar.volume * float(weights[i]) / total_w, timeframe="1s",
            source_label=bar.source_label,
        )
        for i, a, h, l, b in rows
    ]


def split_minute_bars(bars: Iterable[Candle]) -> list[Candle]:
    out: list[Candle] = []
    for b in sorted(bars, key=lambda c: as_utc(c.ts)):
        out.extend(split_minute_bar(b))
    return out
