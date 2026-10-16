"""Deterministic synthetic scenario generators for M6 training drills.

Each archetype produces a full regular-session tape (78 five-minute bars,
09:30 → 16:00 ET) from a seed + params. The tape is byte-identical across
runs: ``generate_synthetic_session(archetype, seed, params)`` always returns
the exact same list of :class:`~app.models.market.Candle` objects.

Design constraints:
  - ``random.Random(seed)`` exclusively for all randomness.
    Never ``os.urandom``, ``time.time()``, ``numpy.random.default_rng`` with
    a non-deterministic seed, or any other non-seeded source.
  - numpy is allowed for arithmetic on arrays that are built from
    ``random.Random``-derived values, since those values are already fixed.
  - No network, no DB, no file I/O.

Time grid: 78 five-minute bars, bar-open timestamps in UTC.
  - Bar 0  → 09:30 ET (13:30 UTC in EDT, 14:30 UTC in EST)
  - Bar 77 → 15:55 ET  (the last 5-min bar before the 16:00 close)

Session-date DST handling: same approach as mock/synthetic.py — use
``zoneinfo`` for correct ET → UTC conversion.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.market import Candle
from app.models.training import Archetype

UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")

# Regular session in ET wall-clock minutes since midnight.
REG_OPEN_MIN = 9 * 60 + 30    # 09:30 ET
REG_CLOSE_MIN = 16 * 60        # 16:00 ET
BAR_MINUTES = 5
N_BARS = (REG_CLOSE_MIN - REG_OPEN_MIN) // BAR_MINUTES  # 78

# A stable fallback session date for timestamp purposes when none is provided.
# 2001-01-02 is a Tuesday — a valid trading day.
_FALLBACK_DATE = date(2001, 1, 2)


# --------------------------------------------------------------------------- #
# Public entry point                                                            #
# --------------------------------------------------------------------------- #

def generate_synthetic_session(
    archetype: Archetype,
    seed: int,
    params: dict | None = None,
    session_date: date | None = None,
) -> list[Candle]:
    """Deterministic synthetic tape for the given archetype.

    Same ``(archetype, seed, params)`` always produces the exact same list of
    :class:`~app.models.market.Candle` objects — no wall-clock, no
    ``os.urandom``, no ``time.time()``. Uses ``random.Random(seed)``
    exclusively.

    The tape covers a full US regular session (78 five-minute bars,
    09:30 → 16:00 ET).

    Args:
        archetype: One of the :class:`~app.models.training.Archetype` values.
            ``Archetype.custom`` falls back to ``trend_day`` behaviour.
        seed: Integer seed for :class:`random.Random`.
        params: Optional dict of per-archetype overrides (all keys optional).
            Common keys understood by all archetypes:

            - ``open_price`` (float, default 100.0)
            - ``atr_pct`` (float, default 0.02 — daily range as % of open)
            - ``direction`` (``'up'`` | ``'down'``, default ``'up'``)

        session_date: ET calendar date for bar timestamps. Defaults to
            ``2001-01-02`` when omitted.

    Returns:
        Exactly ``N_BARS`` (78) :class:`~app.models.market.Candle` objects,
        strictly increasing by ``ts``, with ``high >= max(open, close)``,
        ``low <= min(open, close)``, ``low > 0``, ``volume > 0``.
    """
    if params is None:
        params = {}

    rng = random.Random(seed)
    day = session_date or _FALLBACK_DATE

    dispatch = {
        Archetype.gap_and_go: _gen_gap_and_go,
        Archetype.failed_breakout: _gen_failed_breakout,
        Archetype.chop: _gen_chop,
        Archetype.trend_day: _gen_trend_day,
        Archetype.custom: _gen_trend_day,  # custom falls back to trend_day
    }
    gen_fn = dispatch[archetype]
    ohlcv_bars = gen_fn(rng, params)  # list of (open, high, low, close, volume)
    return _build_candles(ohlcv_bars, day)


# --------------------------------------------------------------------------- #
# Shared helpers                                                                #
# --------------------------------------------------------------------------- #

def _default_params(params: dict) -> tuple[float, float, str]:
    """Extract ``(open_price, atr_pct, direction)`` from params with defaults."""
    open_price = float(params.get("open_price", 100.0))
    atr_pct = float(params.get("atr_pct", 0.02))
    direction = params.get("direction", "up")
    return open_price, atr_pct, direction


def _make_candle_body(
    rng: random.Random,
    prev_close: float,
    target_close: float,
    noise_sigma: float,
) -> tuple[float, float]:
    """Return ``(open, close)`` for one bar.

    ``open`` is the previous close (continuous tape). ``close`` is
    ``target_close`` plus Gaussian noise of ``noise_sigma``. Both clamped
    above 0.01.
    """
    open_ = prev_close
    noise = rng.gauss(0, noise_sigma)
    close = max(target_close + noise, 0.01)
    return open_, close


def _add_wicks(
    rng: random.Random,
    open_: float,
    close: float,
    wick_sigma: float,
) -> tuple[float, float, float, float]:
    """Extend ``open/close`` with realistic intrabar wicks.

    Returns ``(open, high, low, close)`` satisfying
    ``low <= min(open, close)`` and ``high >= max(open, close)``.
    """
    body_hi = max(open_, close)
    body_lo = min(open_, close)
    up_wick = abs(rng.gauss(0, wick_sigma))
    dn_wick = abs(rng.gauss(0, wick_sigma))
    high = round(body_hi + up_wick, 2)
    low = max(round(body_lo - dn_wick, 2), 0.01)
    return round(open_, 2), high, low, round(close, 2)


def _build_candles(
    ohlcv_bars: list[tuple[float, float, float, float, float]],
    day: date,
) -> list[Candle]:
    """Convert a list of ``(open, high, low, close, volume)`` tuples into
    timestamped :class:`~app.models.market.Candle` objects.

    Timestamps are bar-open times in UTC, derived DST-correctly from the
    ``America/New_York`` timezone. Same time-grid convention as
    ``mock/synthetic.py``.
    """
    base_et = datetime.combine(
        day,
        time(REG_OPEN_MIN // 60, REG_OPEN_MIN % 60),
        tzinfo=ET,
    )
    out: list[Candle] = []
    for i, (o, h, l, c, v) in enumerate(ohlcv_bars):
        ts = (base_et + timedelta(minutes=i * BAR_MINUTES)).astimezone(UTC)
        out.append(
            Candle(
                symbol="SYNTH",
                ts=ts,
                open=o,
                high=h,
                low=l,
                close=c,
                volume=max(float(round(v)), 1.0),
                timeframe="5m",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Archetype generators                                                          #
# --------------------------------------------------------------------------- #

def _gen_gap_and_go(
    rng: random.Random,
    params: dict,
) -> list[tuple[float, float, float, float, float]]:
    """Gap-and-go archetype.

    Shape: opens gapped up by ``gap_pct`` (default 4%), first 15 bars trend
    in the gap direction with front-loaded volume, bars 15–45 shallow midday
    consolidation, bars 45–77 afternoon continuation toward the session
    extreme. Session close > session open (for 'up' direction).

    Params (all optional):
    - ``open_price`` (float, default 100.0)
    - ``atr_pct`` (float, default 0.02)
    - ``direction`` (``'up'`` | ``'down'``, default ``'up'``)
    - ``gap_pct`` (float, default 0.04) — gap as a fraction of ``open_price``
    """
    open_price, atr_pct, direction = _default_params(params)
    gap_pct = float(params.get("gap_pct", 0.04))
    sign = 1.0 if direction != "down" else -1.0

    # Gap open: price at bar-0 open is already displaced from the prior close.
    gap_open = round(open_price * (1.0 + sign * gap_pct), 2)

    # Per-bar magnitudes.
    trend_step = open_price * atr_pct * 0.06    # per bar in the trend phase
    consol_step = open_price * atr_pct * 0.010  # per bar in consolidation
    noise_sigma = open_price * atr_pct * 0.020
    wick_sigma = open_price * atr_pct * 0.015

    bars: list[tuple[float, float, float, float, float]] = []
    prev_close = gap_open

    for i in range(N_BARS):
        if i < 15:
            # Strong morning continuation — front-loaded volume.
            mu = sign * trend_step * (1.0 - i / 30.0)  # taper slightly
            target = prev_close + mu
            vol = open_price * 200 * max(0.1, 1.0 - i / 25.0) + abs(rng.gauss(0, open_price * 15))
        elif i < 45:
            # Midday consolidation: slight counter-drift, tight range.
            mu = -sign * consol_step * 0.4
            target = prev_close + mu
            vol = open_price * 35 + abs(rng.gauss(0, open_price * 8))
        else:
            # Afternoon continuation.
            mu = sign * trend_step * 0.6
            target = prev_close + mu
            vol = open_price * 75 + abs(rng.gauss(0, open_price * 20))

        open_, close = _make_candle_body(rng, prev_close, target, noise_sigma)
        open_, high, low, close = _add_wicks(rng, open_, close, wick_sigma)
        bars.append((open_, high, low, close, vol))
        prev_close = close

    return bars


def _gen_failed_breakout(
    rng: random.Random,
    params: dict,
) -> list[tuple[float, float, float, float, float]]:
    """Failed-breakout archetype (anti-FOMO "skip" drill).

    Shape:
    - Bars 0 to ``breakout_bar``-1 (~10 bars): tight consolidation near
      ``open_price``. Range < 0.5 × ``atr_pct``.
    - Bar ``breakout_bar``: big candle with an extended upper wick above the
      consolidation range — the "breakout" attempt. High-volume spike.
    - Bars after breakout: reversal trend that closes below the consolidation
      range low. Session close is at least 2 × ATR% below the breakout high.

    Params (all optional):
    - ``open_price`` (float, default 100.0)
    - ``atr_pct`` (float, default 0.02)
    - ``breakout_bar`` (int, default 10) — bar index of the breakout candle
    """
    open_price, atr_pct, _direction = _default_params(params)
    breakout_bar = int(params.get("breakout_bar", 10))
    breakout_bar = max(3, min(breakout_bar, N_BARS - 20))

    consol_sigma = open_price * atr_pct * 0.012
    wick_sigma_consol = open_price * atr_pct * 0.010
    wick_sigma_rev = open_price * atr_pct * 0.014
    noise_sigma_rev = open_price * atr_pct * 0.015

    consol_center = open_price
    consol_range = open_price * atr_pct * 0.40  # tight range during consolidation

    bars: list[tuple[float, float, float, float, float]] = []
    prev_close = open_price

    for i in range(N_BARS):
        if i < breakout_bar:
            # Tight chop inside consolidation.
            target = consol_center + rng.gauss(0, consol_sigma * 0.4)
            target = max(
                consol_center - consol_range,
                min(consol_center + consol_range, target),
            )
            vol = open_price * 25 + abs(rng.gauss(0, open_price * 6))
            open_, close = _make_candle_body(rng, prev_close, target, consol_sigma * 0.5)
            open_, high, low, close = _add_wicks(rng, open_, close, wick_sigma_consol)

        elif i == breakout_bar:
            # Breakout candle: body pushes above range, extended upper wick.
            close = consol_center + consol_range * 1.2  # body above range
            open_ = prev_close
            # Force a prominent upper wick — the "failed" signal. The wick top
            # must sit at least 1× ATR above the close so the failed-breakout
            # shape is visually unambiguous and the recognizability test passes.
            breakout_top = close + open_price * atr_pct * 1.3
            high = round(breakout_top + abs(rng.gauss(0, open_price * atr_pct * 0.08)), 2)
            low = round(min(open_, close) - abs(rng.gauss(0, wick_sigma_consol)), 2)
            low = max(low, 0.01)
            open_ = round(open_, 2)
            close = round(close, 2)
            vol = open_price * 350 + abs(rng.gauss(0, open_price * 60))  # volume spike

        else:
            # Reversal phase: down-trend below consolidation low. Drift is
            # sized so the session close lands ≥ 2× ATR below the consolidation
            # high (the recognizability contract for this archetype).
            bars_since = i - breakout_bar
            rev_target = (
                consol_center
                - consol_range
                - bars_since * open_price * atr_pct * 0.055
            )
            target = rev_target + rng.gauss(0, noise_sigma_rev * 0.4)
            vol = open_price * 55 + abs(rng.gauss(0, open_price * 12))
            open_, close = _make_candle_body(rng, prev_close, target, noise_sigma_rev)
            open_, high, low, close = _add_wicks(rng, open_, close, wick_sigma_rev)

        bars.append((open_, high, low, close, vol))
        prev_close = close

    return bars


def _gen_chop(
    rng: random.Random,
    params: dict,
) -> list[tuple[float, float, float, float, float]]:
    """Chop archetype ("do-nothing" drill).

    Shape: tight sinusoidal-ish range all day, no sustained directional
    bias. Total session range < 1.5 × ``atr_pct`` × ``open_price``.
    Low volume throughout.

    Params (all optional):
    - ``open_price`` (float, default 100.0)
    - ``atr_pct`` (float, default 0.02)
    """
    open_price, atr_pct, _direction = _default_params(params)

    # Chop range is much tighter than a full ATR day.
    half_range = open_price * atr_pct * 0.28
    noise_sigma = open_price * atr_pct * 0.010
    wick_sigma = open_price * atr_pct * 0.007
    # Period: ~40 bars for visible waves.
    period = 40.0

    # Random phase so each seed looks unique.
    phase = rng.uniform(0, 2 * math.pi)

    bars: list[tuple[float, float, float, float, float]] = []
    prev_close = open_price

    for i in range(N_BARS):
        sine_val = half_range * math.sin(2 * math.pi * i / period + phase)
        target = open_price + sine_val
        open_, close = _make_candle_body(rng, prev_close, target, noise_sigma)
        open_, high, low, close = _add_wicks(rng, open_, close, wick_sigma)
        vol = open_price * 18 + abs(rng.gauss(0, open_price * 4))
        bars.append((open_, high, low, close, vol))
        prev_close = close

    return bars


def _gen_trend_day(
    rng: random.Random,
    params: dict,
) -> list[tuple[float, float, float, float, float]]:
    """Trend-day archetype.

    Shape: steady drift in ``direction`` from open to close, small
    pullbacks every 10–15 bars then continuation, expanding intrabar
    range. Session closes within the top 15% of the session range
    (for 'up') or bottom 15% (for 'down').

    Params (all optional):
    - ``open_price`` (float, default 100.0)
    - ``atr_pct`` (float, default 0.02)
    - ``direction`` (``'up'`` | ``'down'``, default ``'up'``)
    """
    open_price, atr_pct, direction = _default_params(params)
    sign = 1.0 if direction != "down" else -1.0

    # Total session move: atr_pct × 0.8 in the trend direction.
    total_move = open_price * atr_pct * 0.80
    per_bar_drift = total_move / N_BARS

    base_noise = open_price * atr_pct * 0.010
    base_wick = open_price * atr_pct * 0.008

    # 2–3 pullbacks in the middle of the session.
    n_pullbacks = rng.randint(2, 3)
    pullback_bars_set = set(
        rng.randint(8, N_BARS - 15) for _ in range(n_pullbacks)
    )

    bars: list[tuple[float, float, float, float, float]] = []
    prev_close = open_price
    in_pullback = False
    pb_bars_left = 0

    for i in range(N_BARS):
        expand = 1.0 + i / N_BARS  # expanding range through the day
        noise_sigma = base_noise * expand
        wick_sigma = base_wick * expand

        if i in pullback_bars_set and not in_pullback:
            in_pullback = True
            pb_bars_left = rng.randint(2, 4)

        if in_pullback and pb_bars_left > 0:
            mu = -sign * per_bar_drift * 1.8  # counter-trend during pullback
            pb_bars_left -= 1
            if pb_bars_left == 0:
                in_pullback = False
        else:
            mu = sign * per_bar_drift * 1.25  # above average to compensate pullbacks

        target = prev_close + mu
        open_, close = _make_candle_body(rng, prev_close, target, noise_sigma)
        open_, high, low, close = _add_wicks(rng, open_, close, wick_sigma)

        # Volume: morning burst + power-hour ramp.
        if i < 6:
            vol = open_price * 110 + abs(rng.gauss(0, open_price * 25))
        elif i > N_BARS - 8:
            vol = open_price * 95 + abs(rng.gauss(0, open_price * 22))
        else:
            vol = open_price * 45 + abs(rng.gauss(0, open_price * 12))

        bars.append((open_, high, low, close, vol))
        prev_close = close

    return bars
