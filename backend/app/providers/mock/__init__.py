"""Deterministic mock provider package.

Exports :class:`MockProvider` (the offline reference provider) and the reusable
:func:`generate_candles` synthetic-bar generator that M2 quant work and M3 fills
build on.
"""
from __future__ import annotations

from .provider import MockProvider
from .synthetic import generate_candles, seed_for, start_price

__all__ = ["MockProvider", "generate_candles", "seed_for", "start_price"]
