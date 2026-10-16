"""Session VWAP anchored at the regular-session open (09:30 ET) — causal.

Typical price ``tp = (high+low+close)/3``, cumulative
``VWAP_i = Σ(tp*vol) / Σ(vol)`` where the sums reset at the anchor. Bars before
the anchor are ``null`` unless ``include_premarket`` is set (then the anchor is
the first premarket bar / first supplied bar). Every value at ``i`` uses only the
running sums over ``candles[0..i]`` — no session-final peeking.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.models.market import Candle

from ._et import REG_OPEN_MIN, et_minute_of_day


def vwap(
    candles: Sequence[Candle],
    tz: str = "America/New_York",
    include_premarket: bool = False,
) -> list[float | None]:
    """Return the anchored session VWAP aligned index-for-index with ``candles``."""
    out: list[float | None] = [None] * len(candles)
    cum_pv = 0.0
    cum_vol = 0.0
    for i, c in enumerate(candles):
        etmin = et_minute_of_day(c.ts, tz)
        if not include_premarket and etmin < REG_OPEN_MIN:
            # Before the anchor: undefined, and don't accumulate premarket flow.
            continue
        tp = (c.high + c.low + c.close) / 3.0
        cum_pv += tp * c.volume
        cum_vol += c.volume
        out[i] = (cum_pv / cum_vol) if cum_vol > 0 else None
    return out
