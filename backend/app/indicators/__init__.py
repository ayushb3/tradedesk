"""Indicators & levels engine (M2) — strictly causal, no-lookahead.

Every function here computes value-at-``i`` from only ``candles[0..i]`` (plus
legitimately-past data such as the prior trading day). Pure functions are
re-exported so QA can unit-test them directly.
"""
from __future__ import annotations

from . import levels
from .ema import ema
from .rsi import rsi
from .service import compute_replay_session
from .vwap import vwap

__all__ = [
    "ema",
    "rsi",
    "vwap",
    "levels",
    "compute_replay_session",
]
